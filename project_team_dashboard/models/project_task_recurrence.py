from odoo import api, models

from .project_task import PLANNED_DATE_FIELDS


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _create_next_occurrences_values(self, recurrence_by_task):
        """Move the planned dates of the next occurrence and of its sub-tasks by the interval, as Odoo
        moves their Deadline. Not through _get_recurring_fields_to_postpone: Odoo reads those as the
        user who closes the task, and a portal collaborator may not read the planned dates; and it
        moves each date alone, so a move onto a shorter month can put the start after the end."""
        vals_list = super()._create_next_occurrences_values(recurrence_by_task)
        for (task, recurrence), vals in zip(recurrence_by_task.items(), vals_list, strict=True):
            vals.update(recurrence._get_postponed_planned_dates(task.sudo()))
        return vals_list

    def _get_postponed_planned_dates(self, task):
        source_start, source_end = (task[field_name] for field_name in PLANNED_DATE_FIELDS)
        # no delta without a date, as Odoo for the Deadline; a recurrence stored without a unit moves nothing
        if not (source_start or source_end) or not self.repeat_unit:
            return {}
        delta = self._get_recurrence_delta()
        date_start, date_end = (date and date + delta for date in (source_start, source_end))
        # each date moves as the Deadline does; a month on, 30 Jan 10:00 - 31 Jan 10:00 lands on 28 Feb
        # 10:00 for both: the end, which the Deadline matches, stays and the start keeps the length
        if date_start and date_end and date_start >= date_end:
            date_start = date_end - (source_end - source_start)
        return dict(zip(PLANNED_DATE_FIELDS, (date_start, date_end), strict=True))
