from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .project_member import RESTRICTED_ACCESS_MODES
from odoo.addons.project.models.project_task import CLOSED_STATES

ROLE_CHECKED_FIELDS = {"project_id", "user_ids", "stage_id", "state"}
# The project of a task follows them, and a sub-task shown under its parent follows its parent's.
PROJECT_MOVING_FIELDS = {"project_id", "parent_id"}


class ProjectTask(models.Model):
    _inherit = "project.task"

    # read by the task card, which hides its share entry inside
    project_access_mode = fields.Selection(related="project_id.access_mode", string="Project Access")

    @api.constrains("user_ids", "uat_user_ids", "project_id")
    def _check_private_project_people_are_members(self):
        for record in self.sudo().filtered(lambda task: task.project_id.access_mode == "private"):
            # the people fields keep archived users: an archived member stays a member
            members = record.project_id.with_context(active_test=False).member_user_ids
            non_members = (record.user_ids | record.uat_user_ids) - members
            if non_members:
                # checked before the create restriction: the project is not named
                raise ValidationError(
                    self.env._(
                        "In a Private project only its members are assignees or UAT users of a task. Not members: %s",
                        ", ".join(non_members.mapped("display_name")),
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        """In a Public or Private project a new task keeps no access token, only an Admin
        creates it with assignees, and only an assignee, an Editor or an Admin creates it closed.
        """
        tasks = super().create(vals_list)
        tasks._revoke_access_tokens()
        if not (self.env.su or self.env.user._is_system()):
            tasks._ensure_admin_assigns_on_create()
            tasks._ensure_closing_allowed_on_create()
        return tasks

    def write(self, vals):
        """Check, by the role of the user in a Public or Private project, what the access
        rows cannot: the assignees, a move of stage, closing and reopening, a task entering
        by its own move or its parent's, and the sub-tasks a task carries out. Tasks in such
        a project keep no access token.
        """
        is_role_checked = not (self.env.su or self.env.user._is_system())
        if is_role_checked and ROLE_CHECKED_FIELDS & vals.keys():
            # before the checks in sudo, whose messages name the task and its project
            self.check_access("write")
            self._ensure_member_role_allows(vals)
        former_projects = self._get_former_projects() if PROJECT_MOVING_FIELDS & vals.keys() else {}
        writable_sub_tasks = self.browse()
        if former_projects and is_role_checked:
            # before the write: a sub-task that left is judged by the rows of its new project
            writable_sub_tasks = self._get_restricted_sub_tasks(former_projects)._filtered_access("write")
        result = super().write(vals)
        if former_projects:
            entered_tasks = self._get_entered_tasks(former_projects)
            if is_role_checked:
                entered_tasks._ensure_entering_allowed()
                self._ensure_carried_sub_tasks_written(former_projects, writable_sub_tasks)
            entered_tasks._revoke_access_tokens()
        self._revoke_access_tokens()
        return result

    def _notify_get_recipients(self, message, **kwargs):
        recipients_data = super()._notify_get_recipients(message, **kwargs)
        # Sudo: a follower of a task may not read its project.
        return self.sudo().project_id._filter_reading_recipients(self, recipients_data)

    def _portal_ensure_token(self):
        # a token opens the task to whoever holds it, whatever their role
        if self.sudo().project_id.access_mode in RESTRICTED_ACCESS_MODES:
            return ""
        return super()._portal_ensure_token()

    def action_open_share_task_wizard(self):
        """Show a refusal in place of the share wizard of a task of a Public or Private project."""
        restricted_projects = self.sudo().project_id._get_restricted_projects()
        if restricted_projects:
            return restricted_projects._get_share_refusal()
        return super().action_open_share_task_wizard()

    def _ensure_admin_assigns_on_create(self):
        for task in self.sudo().filtered(lambda record: record.user_ids):
            project = task.project_id
            if project.access_mode in RESTRICTED_ACCESS_MODES and self.env.user not in project.admin_user_ids:
                raise AccessError(
                    self.env._("Only an Admin of %s creates a task with assignees in it.", project.display_name)
                )

    def _ensure_closing_allowed_on_create(self):
        for task in self.sudo().filtered(
            lambda record: record.state in CLOSED_STATES and record.project_id.access_mode in RESTRICTED_ACCESS_MODES
        ):
            task._ensure_moving_on_allowed()

    def _ensure_member_role_allows(self, vals):
        """Refuse what the role of the user does not let them write on a task that stays
        in its Public or Private project: the assignees, the stage, closing or reopening
        it. A task moved to another project is judged by that project, after the write.
        """
        user = self.env.user
        assignees_field = self._fields["user_ids"]
        for task in self.sudo():
            project = task.project_id
            if project.access_mode not in RESTRICTED_ACCESS_MODES or vals.get("project_id", project.id) != project.id:
                continue
            assignees = task.user_ids
            if "user_ids" in vals:
                assignees = assignees.browse(assignees_field.convert_to_cache(vals["user_ids"], task))
            if assignees != task.user_ids and user not in project.admin_user_ids:
                raise AccessError(
                    self.env._("Only an Admin of %s changes the assignees of its tasks.", project.display_name)
                )
            if task._is_moved_on(vals):
                task._ensure_moving_on_allowed()

    def _ensure_moving_on_allowed(self):
        """Refuse moving this task to another stage, closing or reopening it, unless the
        user is one of its assignees or an Editor or Admin of its project.
        """
        user = self.env.user
        if user not in self.user_ids and user not in self.project_id.editor_user_ids:
            raise AccessError(
                self.env._(
                    "Only an assignee of %(task)s, or an Editor or Admin of %(project)s, moves it to another"
                    " stage, closes it or reopens it.",
                    task=self.display_name,
                    project=self.project_id.display_name,
                )
            )

    def _is_moved_on(self, vals):
        """Whether vals move this task to another stage, close it or reopen it: the state
        took over closing from the stages, and its other values mark a review.
        """
        is_stage_moved = "stage_id" in vals and vals["stage_id"] != self.stage_id.id
        is_closing_changed = "state" in vals and (vals["state"] in CLOSED_STATES) != (self.state in CLOSED_STATES)
        return is_stage_moved or is_closing_changed

    def _get_former_projects(self):
        """Return {task id: project} of self and of all their sub-tasks, archived included."""
        tasks = self.sudo().with_context(active_test=False).search([("id", "child_of", self.ids)])
        return {task.id: task.project_id for task in tasks}

    def _get_entered_tasks(self, former_projects):
        """Return, in sudo, the tasks of former_projects now in another project, a Public or Private one."""
        return (
            self.sudo()
            .browse(list(former_projects))
            .filtered(
                lambda task: task.project_id != former_projects[task.id]
                and task.project_id.access_mode in RESTRICTED_ACCESS_MODES
            )
        )

    def _ensure_entering_allowed(self):
        """Refuse tasks that entered a Public or Private project, by their own move or by
        their parent's, unless the role of the user lets them move these tasks in. Core has
        read the project as the user by then, so naming it reveals nothing.
        """
        user = self.env.user
        for task in self:
            project = task.project_id
            if user not in project.contributor_user_ids:
                raise AccessError(
                    self.env._("Only a Contributor, Editor or Admin of %s moves a task into it.", project.display_name)
                )
            if task.user_ids and user not in project.admin_user_ids:
                raise AccessError(
                    self.env._("Only an Admin of %s moves a task with assignees into it.", project.display_name)
                )

    def _get_restricted_sub_tasks(self, former_projects):
        """Return the tasks of former_projects other than self that were in a Public or Private project."""
        written_ids = set(self.ids)
        return self.browse(
            [
                task_id
                for task_id, project in former_projects.items()
                if task_id not in written_ids and project.access_mode in RESTRICTED_ACCESS_MODES
            ]
        )

    def _ensure_carried_sub_tasks_written(self, former_projects, writable_sub_tasks):
        """Refuse sub-tasks that left their Public or Private project with these tasks, as
        core moves them in sudo, unless the user could write them there: their new project
        would open them to its readers. The user read them there, so naming them reveals nothing.
        """
        left_sub_tasks = (
            (self._get_restricted_sub_tasks(former_projects) - writable_sub_tasks)
            .sudo()
            .filtered(lambda task: task.project_id != former_projects[task.id])
        )
        if left_sub_tasks:
            project = former_projects[left_sub_tasks[0].id]
            raise AccessError(
                self.env._(
                    "Sub-tasks you may not edit would leave %(project)s with their parent: %(tasks)s",
                    project=project.display_name,
                    tasks=", ".join(
                        left_sub_tasks.filtered(lambda task: former_projects[task.id] == project).mapped("display_name")
                    ),
                )
            )

    def _revoke_access_tokens(self):
        """Clear the access token of those of these tasks in a Public or Private project,
        whatever filled it: values, a context default, a copy, an import.
        """
        tokened_tasks = self.sudo().filtered(
            lambda task: task.access_token and task.project_id.access_mode in RESTRICTED_ACCESS_MODES
        )
        # guarded: the write comes back here
        if tokened_tasks:
            tokened_tasks.write({"access_token": False})
