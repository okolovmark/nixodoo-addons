from odoo import Command, _, api, models
from odoo.exceptions import AccessError, ValidationError

from .project_member import RESTRICTED_ACCESS_MODES

ROLE_CHECKED_FIELDS = {"project_id", "user_ids", "uat_user_ids", "stage_id"}
# The project of a task follows them, and a sub-task without a project of its own follows its parent's.
PROJECT_MOVING_FIELDS = {"project_id", "parent_id", "display_project_id"}


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.constrains("user_ids", "uat_user_ids", "project_id")
    def _check_private_project_people_are_members(self):
        for record in self.sudo().filtered(lambda task: task.project_id.access_mode == "private"):
            non_members = (record.user_ids | record.uat_user_ids) - record.project_id.member_user_ids
            if non_members:
                # checked before the create rule: the project is not named
                raise ValidationError(
                    _("In a Private project only its members are assignees or UAT users of a task. Not members: %s")
                    % ", ".join(non_members.mapped("display_name"))
                )

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks._revoke_access_tokens()
        if not (self.env.su or self.env.user._is_system()):
            tasks._ensure_admin_assigns_on_create()
            tasks._ensure_uat_users_allowed_on_create()
        return tasks

    def write(self, vals):
        is_role_checked = not (self.env.su or self.env.user._is_system())
        if is_role_checked and ROLE_CHECKED_FIELDS & vals.keys():
            # before the checks in sudo, whose messages name the task and its project
            self.check_access_rights("write")
            self.check_access_rule("write")
            self._ensure_member_role_allows(vals)
        former_projects = self._get_former_projects() if PROJECT_MOVING_FIELDS & vals.keys() else {}
        writable_sub_tasks = self.browse()
        if former_projects and is_role_checked:
            # before the write: after it, a sub-task that left is judged by its new project
            writable_sub_tasks = self._get_restricted_sub_tasks(former_projects)._filter_access_rules_python("write")
        # core pops the key, then writes the same vals to other tasks of the recurrence
        is_recurrence_written = vals.get("recurrence_update", "this") != "this"
        result = super().write(vals)
        if former_projects:
            entered_tasks = self._get_entered_tasks(former_projects)
            if is_role_checked:
                entered_tasks._ensure_entering_allowed()
                self._ensure_carried_sub_tasks_written(former_projects, writable_sub_tasks)
            entered_tasks._revoke_access_tokens()
        self._revoke_access_tokens()
        if is_recurrence_written:
            self.sudo().recurrence_id.task_ids._revoke_access_tokens()
        return result

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        recipients_data = super()._notify_get_recipients(message, msg_vals, **kwargs)
        # Sudo: a follower of a task may not read its project.
        return self.sudo().project_id._filter_reading_recipients(self, recipients_data)

    def _portal_ensure_token(self):
        # a token opens the task to whoever holds it, whatever their role
        if self.sudo().project_id.access_mode in RESTRICTED_ACCESS_MODES:
            return ""
        return super()._portal_ensure_token()

    def _ensure_admin_assigns_on_create(self):
        for task in self.sudo().filtered(lambda record: record.user_ids):
            project = task.project_id
            if project.access_mode in RESTRICTED_ACCESS_MODES and self.env.user not in project.admin_user_ids:
                raise AccessError(_("Only an Admin of %s creates a task with assignees in it.") % project.display_name)

    def _ensure_uat_users_allowed_on_create(self):
        for task in self.sudo().filtered(
            lambda record: record.uat_user_ids and record.project_id.access_mode in RESTRICTED_ACCESS_MODES
        ):
            task._ensure_uat_users_change_allowed()

    def _ensure_member_role_allows(self, vals):
        """Refuse what the role of the user does not let them write on a task that stays
        in its Public or Private project: the assignees, the UAT users, the stage. A task
        moved to another project is judged by that project, after the write.
        """
        user = self.env.user
        for task in self.sudo():
            project = task.project_id
            if project.access_mode not in RESTRICTED_ACCESS_MODES or vals.get("project_id", project.id) != project.id:
                continue
            if task._is_people_changed("user_ids", vals) and user not in project.admin_user_ids:
                raise AccessError(_("Only an Admin of %s changes the assignees of its tasks.") % project.display_name)
            if task._is_people_changed("uat_user_ids", vals):
                task._ensure_uat_users_change_allowed()
            is_stage_moved = "stage_id" in vals and vals["stage_id"] != task.stage_id.id
            if is_stage_moved and not task._is_assignee_or_editor(user):
                raise AccessError(
                    _("Only an assignee of %(task)s, or an Editor or Admin of %(project)s, moves it to another stage.")
                    % {"task": task.display_name, "project": project.display_name}
                )

    def _is_people_changed(self, field_name, vals):
        """Whether vals change the users of the people field field_name of this task, a
        clearing and a user created through it included.
        """
        if field_name not in vals:
            return False
        value = vals[field_name]
        # core's write replaces the users by ids given without commands, where the cache adds them
        if isinstance(value, tuple) or (isinstance(value, list) and value and not isinstance(value[0], (tuple, list))):
            value = [Command.set(value)]
        elif isinstance(value, list):
            commands = [command for command in value if isinstance(command, (tuple, list)) and command]
            if any(command[0] == Command.CREATE for command in commands):
                return True
            # an update changes the user, not who is in the field; the cache would also apply it, here in sudo
            value = [command for command in commands if command[0] != Command.UPDATE]
        return set(self._fields[field_name].convert_to_cache(value, self)) != set(self[field_name].ids)

    def _ensure_uat_users_change_allowed(self):
        """Refuse setting the UAT users of this task unless the user is one of its assignees
        or an Editor or Admin of its project.
        """
        if not self._is_assignee_or_editor(self.env.user):
            raise AccessError(
                _("Only an assignee of %(task)s, or an Editor or Admin of %(project)s, sets its UAT users.")
                % {"task": self.display_name, "project": self.project_id.display_name}
            )

    def _is_assignee_or_editor(self, user):
        task = self.sudo()
        return user in task.user_ids or user in task.project_id.editor_user_ids

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
        their parent's, unless the role of the user lets them move these tasks in.
        """
        user = self.env.user
        for task in self:
            project = task.project_id
            if user not in project.contributor_user_ids:
                raise AccessError(
                    _("Only a Contributor, Editor or Admin of %s moves a task into it.")
                    % self._get_project_name_for_user(project)
                )
            if task.user_ids and user not in project.admin_user_ids:
                raise AccessError(
                    _("Only an Admin of %s moves a task with assignees into it.")
                    % self._get_project_name_for_user(project)
                )
            # entering is a create there; an assignee who is no Admin was refused above
            if task.uat_user_ids and user not in project.editor_user_ids:
                raise AccessError(
                    _("Only an Editor or Admin of %s moves a task with UAT users into it.")
                    % self._get_project_name_for_user(project)
                )

    def _get_project_name_for_user(self, project):
        """The name of project in a refusal, for a user who reads it: the project of a
        parent written by id may be one they do not.
        """
        if self._is_read_by_user(project):
            return project.display_name
        return _("a Public or Private project")

    def _is_read_by_user(self, project):
        return bool(project.with_user(self.env.user)._filter_access_rules("read"))

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
        would open them to its readers. A task is read where its project is: the sub-tasks
        and their project are named only to a user who reads it.
        """
        left_sub_tasks = (
            (self._get_restricted_sub_tasks(former_projects) - writable_sub_tasks)
            .sudo()
            .filtered(lambda task: task.project_id != former_projects[task.id])
        )
        if not left_sub_tasks:
            return
        project = former_projects[left_sub_tasks[0].id]
        if not self._is_read_by_user(project):
            raise AccessError(
                _("Sub-tasks you may not edit would leave a Public or Private project with their parent.")
            )
        raise AccessError(
            _("Sub-tasks you may not edit would leave %(project)s with their parent: %(tasks)s")
            % {
                "project": project.display_name,
                "tasks": ", ".join(
                    left_sub_tasks.filtered(lambda task: former_projects[task.id] == project).mapped("display_name")
                ),
            }
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
