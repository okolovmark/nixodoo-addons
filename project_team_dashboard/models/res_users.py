from odoo import fields, models

GANTT_COLUMN_KEYS = ("assignees", "deadline", "planned_hours", "spent_hours", "stage")


class ResUsers(models.Model):
    _inherit = "res.users"

    dashboard_default_project_id = fields.Many2one(
        "project.project",
        string="Default Dashboard Project",
        user_writeable=True,
        help="Project selected by default when opening the Team Dashboard.",
    )
    gantt_columns = fields.Char(
        string="Gantt Columns",
        default=",".join(GANTT_COLUMN_KEYS[:4]),
        user_writeable=True,
        help="Comma-separated attribute columns shown in the left pane of the Gantt chart.",
    )

    def _get_gantt_columns(self):
        self.ensure_one()
        chosen_keys = (self.gantt_columns or "").split(",")
        return [key for key in GANTT_COLUMN_KEYS if key in chosen_keys]
