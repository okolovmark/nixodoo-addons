from odoo import fields, models


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    is_in_progress = fields.Boolean(
        string="In Progress Stage",
        help="If checked, tasks in this stage are considered actively being worked on. "
        "Used by the team dashboard to determine a member's current task.",
    )
    is_backlog = fields.Boolean(
        string="Backlog Stage",
        help="If checked, tasks in this stage are considered part of the backlog. "
        "Used for backlog views and dashboard grouping.",
    )
