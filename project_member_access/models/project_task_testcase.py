from odoo import models


class ProjectTaskTestcase(models.Model):
    _name = "project.task.testcase"
    _inherit = ["project.task.testcase", "project.member.access.line.mixin"]
