from odoo import api, models


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _create_next_occurrences_values(self, recurrence_by_task):
        """The values of an occurrence are those of a copy of the task it
        repeats, completed while they held its stage: the stage the occurrence
        starts in is set on them afterwards. An occurrence that starts in
        another stage takes nothing, like any copy sent to another stage.

        The child tasks of an occurrence get their values from this method too.
        """
        values_list = super()._create_next_occurrences_values(recurrence_by_task)
        for task, values in zip(recurrence_by_task, values_list):
            task._remove_closing_values(values)
        return values_list
