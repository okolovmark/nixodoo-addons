from odoo import fields, models


class ProjectBaseline(models.Model):
    _name = "project.baseline"
    _description = "Project Baseline"
    _order = "date desc, id desc"
    _rec_name = "date"

    project_id = fields.Many2one(
        "project.project",
        required=True,
        ondelete="cascade",
        index=True,
    )
    user_id = fields.Many2one(
        "res.users",
        string="Saved By",
        default=lambda self: self.env.user,
    )
    date = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
    )
    line_ids = fields.One2many(
        "project.baseline.line",
        "baseline_id",
        string="Lines",
    )


class ProjectBaselineLine(models.Model):
    _name = "project.baseline.line"
    _description = "Project Baseline Line"

    baseline_id = fields.Many2one(
        "project.baseline",
        required=True,
        ondelete="cascade",
        index=True,
    )
    task_id = fields.Many2one(
        "project.task",
        required=True,
        ondelete="cascade",
        index=True,
    )
    planned_date_start = fields.Datetime()
    planned_date_end = fields.Datetime()
    date_deadline = fields.Datetime(string="Deadline")
