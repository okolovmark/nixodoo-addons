from odoo import models


class ProjectTaskSession(models.Model):
    _name = "project.task.session"
    _inherit = ["project.task.session", "project.member.access.line.mixin"]
