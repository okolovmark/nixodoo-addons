from odoo import models


class ProjectTaskQuestion(models.Model):
    _name = "project.task.question"
    _inherit = ["project.task.question", "project.member.access.line.mixin"]
