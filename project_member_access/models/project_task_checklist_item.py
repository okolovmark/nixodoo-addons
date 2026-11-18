from odoo import models


class ProjectTaskChecklistItem(models.Model):
    _name = "project.task.checklist.item"
    _inherit = ["project.task.checklist.item", "project.member.access.line.mixin"]
