from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    is_ai_driven = fields.Boolean(string="AI-driven Project")
    # Name, table and columns of project_task_pull_request: the stages it marked stay marked.
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
