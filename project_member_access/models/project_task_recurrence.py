from odoo import Command, api, models

from .project_task import PRIVATE_PROJECT_PEOPLE_FIELDS


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _create_next_occurrences_values(self, recurrence_by_task):
        """In a Private project the next occurrence of a recurring task, and each
        of its sub-tasks, takes only the assignees and UAT users who are still
        members of the project. The task it repeats may keep one who left since,
        whom the project refuses on a new task, and the refusal would undo the
        change that closes that task.
        """
        values_list = super()._create_next_occurrences_values(recurrence_by_task)
        for values in values_list:
            self._remove_non_member_people(values)
        return values_list

    @api.model
    def _remove_non_member_people(self, values):
        project = self.env["project.project"].sudo().browse(values.get("project_id"))
        if project.access_mode != "private":
            return
        # as the check on the people of a Private project: an archived member stays a member
        member_ids = set(project.with_context(active_test=False).member_user_ids.ids)
        task_model = self.env["project.task"]
        for field_name in PRIVATE_PROJECT_PEOPLE_FIELDS:
            if values.get(field_name):
                user_ids = task_model._fields[field_name].convert_to_cache(values[field_name], task_model)
                values[field_name] = [Command.set([user_id for user_id in user_ids if user_id in member_ids])]
