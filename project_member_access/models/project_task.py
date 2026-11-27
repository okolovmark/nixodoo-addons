from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import SQL

from odoo.addons.project.models.project_task import CLOSED_STATES

from .project_member import RESTRICTED_ACCESS_MODES

ROLE_CHECKED_FIELDS = {"project_id", "user_ids", "uat_user_ids", "stage_id", "state"}
PRIVATE_PROJECT_PEOPLE_FIELDS = ("user_ids", "uat_user_ids")
# The project of a task follows them, and a sub-task shown under its parent follows its parent's.
PROJECT_MOVING_FIELDS = {"project_id", "parent_id"}
STAGE_SETTING_FIELDS = {"project_id", "stage_id"}


class ProjectTask(models.Model):
    _inherit = "project.task"

    # read by the task card, which hides its share entry inside
    project_access_mode = fields.Selection(related="project_id.access_mode", string="Project Access")
    # read by the people pickers of the task form
    project_member_user_ids = fields.Many2many(related="project_id.member_user_ids", string="Project Member Users")
    is_assignee_change_allowed = fields.Boolean(compute="_compute_is_assignee_change_allowed")
    is_uat_user_change_allowed = fields.Boolean(compute="_compute_is_uat_user_change_allowed")

    @api.depends("project_id.access_mode", "project_id.admin_user_ids")
    @api.depends_context("uid")
    def _compute_is_assignee_change_allowed(self):
        is_role_checked = self._is_role_checked()
        user = self.env.user
        for record in self:
            # judged on the task as saved, so that a value typed before a change of project reaches the save; a new
            # task, saved in no project, keeps them editable to clear those a screen fills in: its create checks them
            record.is_assignee_change_allowed = not is_role_checked or record._origin._may_change_assignees(user)

    @api.depends("user_ids", "project_id.access_mode", "project_id.editor_user_ids")
    @api.depends_context("uid")
    def _compute_is_uat_user_change_allowed(self):
        is_role_checked = self._is_role_checked()
        user = self.env.user
        for record in self:
            record.is_uat_user_change_allowed = not is_role_checked or record._origin._may_change_uat_users(user)

    @api.constrains(*PRIVATE_PROJECT_PEOPLE_FIELDS, "project_id")
    def _check_private_project_people_are_members(self):
        for record in self.sudo().filtered(lambda task: task.project_id.access_mode == "private"):
            # the people fields keep archived users: an archived member stays a member
            members = record.project_id.with_context(active_test=False).member_user_ids
            people = record.env["res.users"].union(record[field_name] for field_name in PRIVATE_PROJECT_PEOPLE_FIELDS)
            non_members = people - members
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
        creates it with assignees or on a stage the project does not list, only an assignee,
        an Editor or an Admin creates it closed or with UAT users.
        """
        is_role_checked = self._is_role_checked()
        listed_stages = self._get_restricted_listed_stages() if is_role_checked else set()
        tasks = super().create(vals_list)
        tasks._revoke_access_tokens()
        if is_role_checked:
            # a refusal undoes the create and the key it gave: the checks name a task by its name
            tasks._ensure_admin_assigns_on_create()
            tasks._ensure_uat_users_allowed_on_create()
            tasks._ensure_closing_allowed_on_create()
            tasks._ensure_stages_listed_on_create(listed_stages)
        return tasks

    def write(self, vals):
        """Check, by the role of the user in a Public or Private project, what the access
        rows cannot: the assignees and UAT users, a move of stage, a stage the project does
        not list, closing and reopening, a task entering by its own move or its parent's,
        and the sub-tasks a task carries out. Tasks in such a project keep no access token.
        """
        is_role_checked = self._is_role_checked()
        if is_role_checked and ROLE_CHECKED_FIELDS & vals.keys():
            # before the checks in sudo, whose messages name the task and its project
            self.check_access("write")
            self._ensure_member_role_allows(vals)
        former_projects = self._get_former_projects() if PROJECT_MOVING_FIELDS & vals.keys() else {}
        writable_sub_tasks = self.browse()
        if former_projects and is_role_checked:
            # before the write: a sub-task that left is judged by the rows of its new project
            writable_sub_tasks = self._get_restricted_sub_tasks(former_projects)._filtered_access("write")
        former_placements = (
            self._get_former_placements() if is_role_checked and STAGE_SETTING_FIELDS & vals.keys() else {}
        )
        result = super().write(vals)
        if former_projects:
            entered_tasks = self._get_entered_tasks(former_projects)
            if is_role_checked:
                entered_tasks._ensure_entering_allowed()
                self._ensure_carried_sub_tasks_written(former_projects, writable_sub_tasks)
            entered_tasks._revoke_access_tokens()
        if former_placements:
            self._ensure_stages_listed_on_write(former_placements)
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

    def _ensure_uat_users_allowed_on_create(self):
        for task in self.sudo().filtered(
            lambda record: record.uat_user_ids and record.project_id.access_mode in RESTRICTED_ACCESS_MODES
        ):
            task._ensure_uat_users_change_allowed(name_field="name")

    def _ensure_closing_allowed_on_create(self):
        for task in self.sudo().filtered(
            lambda record: record.state in CLOSED_STATES and record.project_id.access_mode in RESTRICTED_ACCESS_MODES
        ):
            task._ensure_moving_on_allowed(name_field="name")

    @api.model
    def _get_restricted_listed_stages(self):
        """{(project id, stage id)} of the stages the Public and Private projects list, archived
        ones included, read before a create: core adds the stage of a new task to its project
        when the project lacks it, whatever gave the stage, the values, the context or a default.
        """
        projects = self.env["project.project"]
        projects.flush_model(["access_mode"])
        stages_field = projects._fields["type_ids"]
        rows = self.env.execute_query(
            SQL(
                "SELECT rel.%s, rel.%s FROM %s rel JOIN %s project ON project.id = rel.%s"
                " WHERE project.access_mode IN %s",
                SQL.identifier(stages_field.column1),
                SQL.identifier(stages_field.column2),
                SQL.identifier(stages_field.relation),
                SQL.identifier(projects._table),
                SQL.identifier(stages_field.column1),
                RESTRICTED_ACCESS_MODES,
            )
        )
        return set(rows)

    def _ensure_stages_listed_on_create(self, listed_stages):
        for task in self.sudo().filtered(
            lambda record: record.stage_id and record.project_id.access_mode in RESTRICTED_ACCESS_MODES
        ):
            if (task.project_id.id, task.stage_id.id) not in listed_stages:
                task._ensure_unlisted_stage_allowed()

    def _get_former_placements(self):
        return {task.id: (task.project_id, task.stage_id) for task in self.sudo()}

    def _ensure_stages_listed_on_write(self, former_placements):
        for task in self.sudo():
            if (
                (task.project_id, task.stage_id) != former_placements[task.id]
                and task.stage_id
                and task.stage_id not in task.project_id.with_context(active_test=False).type_ids
            ):
                task._ensure_unlisted_stage_allowed()

    def _ensure_unlisted_stage_allowed(self):
        """Refuse this task of a Public or Private project on a stage the project does not
        list, unless the user is an Admin of the project: a stage of such a project is
        changed by its Admins only, and core adds the stage of a new task to its project.
        """
        project = self.project_id
        if project.access_mode in RESTRICTED_ACCESS_MODES and self.env.user not in project.admin_user_ids:
            raise AccessError(
                self.env._(
                    "Only an Admin of %s puts a task on a stage the project does not list.", project.display_name
                )
            )

    def _ensure_member_role_allows(self, vals):
        """Refuse what the role of the user does not let them write on a task that stays
        in its Public or Private project: the assignees, the UAT users, the stage, closing or
        reopening it. A task moved to another project is judged by that project, after the write.
        """
        user = self.env.user
        for task in self.sudo():
            project = task.project_id
            if project.access_mode not in RESTRICTED_ACCESS_MODES or vals.get("project_id", project.id) != project.id:
                continue
            if task._is_people_changed("user_ids", vals) and not task._may_change_assignees(user):
                raise AccessError(
                    self.env._("Only an Admin of %s changes the assignees of its tasks.", project.display_name)
                )
            if task._is_people_changed("uat_user_ids", vals):
                task._ensure_uat_users_change_allowed()
            if task._is_moved_on(vals):
                task._ensure_moving_on_allowed()

    def _is_people_changed(self, fname, vals):
        """Whether vals change the users of the people field fname of this task, a clearing included."""
        if fname not in vals:
            return False
        return set(self._fields[fname].convert_to_cache(vals[fname], self)) != set(self[fname].ids)

    def _ensure_uat_users_change_allowed(self, name_field="display_name"):
        """Refuse setting the UAT users of this task unless the user is one of its assignees
        or an Editor or Admin of its project.
        """
        if not self._is_assignee_or_editor(self.env.user):
            raise AccessError(
                self.env._(
                    "Only an assignee of %(task)s, or an Editor or Admin of %(project)s, sets its UAT users.",
                    task=self[name_field],
                    project=self.project_id.display_name,
                )
            )

    def _ensure_moving_on_allowed(self, name_field="display_name"):
        """Refuse moving this task to another stage, closing or reopening it, unless the
        user is one of its assignees or an Editor or Admin of its project.
        """
        if not self._is_assignee_or_editor(self.env.user):
            raise AccessError(
                self.env._(
                    "Only an assignee of %(task)s, or an Editor or Admin of %(project)s, moves it to another"
                    " stage, closes it or reopens it.",
                    task=self[name_field],
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

    def _is_role_checked(self):
        return not (self.env.su or self.env.user._is_system())

    def _may_change_assignees(self, user):
        project = self.sudo().project_id
        return project.access_mode not in RESTRICTED_ACCESS_MODES or user in project.admin_user_ids

    def _may_change_uat_users(self, user):
        task = self.sudo()
        return task.project_id.access_mode not in RESTRICTED_ACCESS_MODES or task._is_assignee_or_editor(user)

    def _is_assignee_or_editor(self, user):
        task = self.sudo()
        return user in task.user_ids or user in task.project_id.editor_user_ids

    def _get_former_projects(self):
        """Return {task id: project} of self and of all their sub-tasks, archived included, in the
        order of the model: the first task refused names the project of the refusal.
        """
        return {task.id: task.project_id for task in self._get_moving_tasks().sorted()}

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
            # entering is a create there: UAT users take who may set them on create
            if task.uat_user_ids and not task._is_assignee_or_editor(user):
                raise AccessError(
                    self.env._(
                        "Only an Editor or Admin of %s moves a task with UAT users into it.", project.display_name
                    )
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
