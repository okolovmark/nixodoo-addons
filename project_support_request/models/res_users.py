from odoo import models
from odoo.fields import Domain


class ResUsers(models.Model):
    _inherit = "res.users"

    def _support_request_task_read_domain(self):
        """Return the domain of the module's read restriction of tasks: core's multi-company restriction
        as it stands, or a task of the project that develops this Odoo the user is an internal UAT user of.

        The user of the evaluation context keeps the company selection being checked, which core's
        domain is evaluated in."""
        self.ensure_one()
        own_request = Domain("project_id.develops_this_odoo", "=", True) & Domain(
            "uat_user_ids", "any", Domain("id", "=", self.id) & Domain("share", "=", False)
        )
        return self.env["ir.access"].sudo()._support_request_core_task_domain() | own_request
