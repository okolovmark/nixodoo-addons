from odoo import api, models

from .project_task import NEXT_OCCURRENCE_CREATION, OCCURRENCE_LINE_FIELDS


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _create_next_occurrences(self, occurrences_from):
        """Core creates each next occurrence, its sub-tasks included, in the
        first stage of its project, or in the stage of the task it repeats when
        the project lists none. Neither stage rule checks that creation: a
        refusal would take the closing of the task it repeats with it.

        The tasks repeated carry the key too: core pairs them with their
        occurrences in _resolve_copied_dependencies, whose override in this
        module gives the cases of the occurrences their Verification kind.
        """
        marked_context = {"project_ai_driven_next_occurrence": NEXT_OCCURRENCE_CREATION}
        occurrences = super(ProjectTaskRecurrence, self.with_context(**marked_context))._create_next_occurrences(
            occurrences_from.with_context(**marked_context)
        )
        # the occurrences go back without the key: a later create through them is checked
        return occurrences.with_context(self.env.context)

    @api.model
    def _create_next_occurrences_values(self, recurrence_by_task):
        """The values of an occurrence are those of a copy of the task it
        repeats, without the Resolution and the pull requests that copy takes
        when it keeps the stage of its task: the occurrence is new work, in
        whatever stage it starts.

        It takes the checklist items and the test cases of its task from zero,
        as a task created from a template does, and no question row: core's
        copy values are built without lines and get these here, the
        Verification of a prepared case after the creation. Sudo, as core
        builds its values: whoever closes the task, the occurrence gets them.

        The child tasks of an occurrence get their values from this method too.
        """
        values_list = super(
            ProjectTaskRecurrence, self.with_context(copy_without_lines=True)
        )._create_next_occurrences_values(recurrence_by_task)
        task_model = self.env["project.task"]
        for task, values in zip(recurrence_by_task, values_list):
            task_model._remove_closing_values(values)
            task.sudo()._add_line_values(values, keep_progress=False, field_names=OCCURRENCE_LINE_FIELDS)
        return values_list
