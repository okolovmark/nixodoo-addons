from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    is_ai_driven = fields.Boolean(string="AI-driven Project")
    # Both stage fields have the names, tables and columns of the 16.0 branch: a database upgraded
    # from it keeps its marked stages.
    pr_required_states = fields.Many2many(
        "project.task.type",
        "project_pr_required",
        "project_id",
        "state_id",
        string="Stages Requiring a Resolution",
        help="A task of an AI-driven project enters these stages only with a Resolution,"
        " and with a pull request when the Resolution is Code.",
    )
    estimate_required_states = fields.Many2many(
        "project.task.type",
        "project_estimate_required",
        "project_id",
        "state_id",
        string="Stages Requiring a Type and an Estimate",
        help="A task of an AI-driven project enters these stages only with a task type"
        " and an estimate above zero hours.",
    )

    def action_view_tasks(self):
        action = super().action_view_tasks()
        # read by the column_invisible of the list's Priority column, as allow_milestones is for the Milestone one
        action["context"]["ai_driven"] = self.is_ai_driven
        return action

    def map_tasks(self, new_project_id):
        """A project template made from a project keeps no Resolution and no
        pull request of its tasks. They go once the tasks are copied: core
        creates the copy of a sub-task in this project and moves it after, and
        until then the closing rule needs them.
        """
        result = super().map_tasks(new_project_id)
        new_project = self.browse(new_project_id)
        if new_project.is_template and not self.is_template:
            new_project.with_context(active_test=False).tasks._clear_closing_values()
        return result
