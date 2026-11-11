from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    is_software_project = fields.Boolean(string="Software Project")
