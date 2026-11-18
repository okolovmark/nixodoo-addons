from odoo import models


class ProjectMilestone(models.Model):
    _inherit = "project.milestone"

    def write(self, vals):
        result = super().write(vals)
        if "project_id" in vals:
            # the write restriction was checked on the project the milestone leaves
            self.check_access("write")
        return result

    def _notify_get_recipients(self, message, **kwargs):
        recipients_data = super()._notify_get_recipients(message, **kwargs)
        # Sudo: a follower of a milestone may not read its project.
        return self.sudo().project_id._filter_reading_recipients(self, recipients_data)
