from odoo import models


class ProjectUpdate(models.Model):
    _inherit = "project.update"

    def write(self, vals):
        result = super().write(vals)
        if "project_id" in vals:
            # the write rules were checked on the project the update leaves
            self.check_access_rule("write")
        return result

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        recipients_data = super()._notify_get_recipients(message, msg_vals, **kwargs)
        # Sudo: a follower of an update may not read its project.
        return self.sudo().project_id._filter_reading_recipients(self, recipients_data)
