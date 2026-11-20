from odoo import models


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    def _new_task_values(self, task):
        """In a Private project the next task of a recurrence, and each of its
        sub-tasks, takes only the assignees who are still members of it. The
        task it repeats may keep one removed since, whom the project refuses on
        a new task, and the refusal would roll back the whole run of the cron.
        """
        values = super()._new_task_values(task)
        project = self.env["project.project"].sudo().browse(values.get("project_id"))
        if project.access_mode == "private" and values.get("user_ids"):
            member_ids = set(project.member_user_ids.ids)
            values["user_ids"] = [user_id for user_id in values["user_ids"] if user_id in member_ids]
        return values
