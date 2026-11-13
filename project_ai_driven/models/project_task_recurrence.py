from odoo import api, models


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _get_recurring_fields(self):
        """The next task of a recurrence takes the task type of the last one,
        as it takes its estimate: the first stage of its project may take a
        task only with both.
        """
        return [*super()._get_recurring_fields(), "task_type"]
