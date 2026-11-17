from odoo import api, models

from .project_task import COPIED_LINE_FIELDS


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _create_next_occurrences_values(self, recurrence_by_task):
        """The values of an occurrence are those of a copy of the task it
        repeats, completed while they held its stage: the stage the occurrence
        starts in is set on them afterwards. An occurrence that starts in
        another stage takes nothing, like any copy sent to another stage. It
        takes none of the lines of its task either.

        The child tasks of an occurrence get their values from this method too.
        """
        values_list = super()._create_next_occurrences_values(recurrence_by_task)
        for task, values in zip(recurrence_by_task, values_list):
            task._remove_closing_values(values)
            for field_name in COPIED_LINE_FIELDS:
                values.pop(field_name, None)
        return values_list
