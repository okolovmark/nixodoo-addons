from odoo import Command, api, models

# The value of the context key under which core creates the next occurrences: an object of
# this module, which no RPC call can send, so no caller switches the stage rules off with it.
NEXT_OCCURRENCE_CREATION = object()
OCCURRENCE_LINE_FIELDS = ("checklist_item_ids", "testcase_ids")


def is_next_occurrence_creation(env):
    """Whether env is that of core's creation of the next tasks of recurrences (_create_next_task)."""
    return env.context.get("project_ai_driven_next_occurrence") is NEXT_OCCURRENCE_CREATION


class ProjectTaskRecurrence(models.Model):
    _inherit = "project.task.recurrence"

    @api.model
    def _get_recurring_fields(self):
        """The next task of a recurrence takes the task type of the last one,
        as it takes its estimate, and the Estimate Coefficient that estimate
        applied.
        """
        return [*super()._get_recurring_fields(), "task_type", "estimate_coefficient"]

    def _new_task_values(self, task):
        """The next task of a recurrence, and each of its sub-tasks, takes the
        checklist items and the test cases of the task it repeats from zero,
        and no question row. Sudo, as core reads the task: whoever runs the
        recurrence, the occurrence gets them.
        """
        values = super()._new_task_values(task)
        for field_name in OCCURRENCE_LINE_FIELDS:
            lines = task.sudo()[field_name]
            values[field_name] = [Command.create(line_values) for line_values in lines._get_copy_values_from_zero()]
        return values

    def _create_next_task(self):
        """Core creates the next task of each recurrence, its sub-tasks
        included, in the first stage of its project, or in the stage of the
        task it repeats when the project lists none. None of the three stage
        rules checks that creation, whichever the stage: one refusal would roll
        back the whole run of the cron, and no due recurrence would get its task.
        """
        # core returns None: no task created under the key reaches a caller that would write it unchecked
        return super(
            ProjectTaskRecurrence,
            self.with_context(project_ai_driven_next_occurrence=NEXT_OCCURRENCE_CREATION),
        )._create_next_task()
