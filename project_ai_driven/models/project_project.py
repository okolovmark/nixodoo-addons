from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    is_ai_driven = fields.Boolean(string="AI-driven Project")
