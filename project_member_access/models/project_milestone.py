from odoo import models


class ProjectMilestone(models.Model):
    _inherit = "project.milestone"

    def write(self, vals):
        result = super().write(vals)
        if "project_id" in vals:
            # the write rules were checked on the project the milestone leaves
            self.check_access_rule("write")
        return result
