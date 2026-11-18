from odoo import models


class ProjectUpdate(models.Model):
    _inherit = "project.update"

    def write(self, vals):
        """An update moved to another project is also checked against that project's write restriction."""
        result = super().write(vals)
        if "project_id" in vals:
            # the write restriction was checked on the project the update leaves
            self.check_access("write")
        return result

    def _notify_get_recipients(self, message, **kwargs):
        recipients_data = super()._notify_get_recipients(message, **kwargs)
        # Sudo: a follower of a update may not read its project.
        return self.sudo().project_id._filter_reading_recipients(self, recipients_data)
