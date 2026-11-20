from odoo import models
from odoo.osv import expression
from odoo.tools.safe_eval import safe_eval

from .ir_rule import SHIPPED_TASK_COMPANY_DOMAIN, TASK_COMPANY_RULE


class ResUsers(models.Model):
    _inherit = "res.users"

    def _support_request_task_read_domain(self):
        """Return the read domain of tasks for this user: the current domain of core's company rule
        of tasks, or a task of the project that develops this Odoo with the user among its UAT users.

        With core's rule archived or deleted, reads keep the domain project ships it with: lifting the
        company restriction from reading takes archiving the module's read rule too. With core's rule
        given groups, a user outside them gets no restriction, as core's rule skips them.
        """
        self.ensure_one()
        company_rule = self.sudo().env.ref(TASK_COMPANY_RULE, raise_if_not_found=False) or self.env["ir.rule"]
        if not company_rule.active:
            company_domain_text = SHIPPED_TASK_COMPANY_DOMAIN
        elif company_rule.groups and not company_rule.groups & self.sudo().groups_id:
            return []
        else:
            company_domain_text = company_rule.domain_force or "[]"
        company_domain = safe_eval(company_domain_text, self.env["ir.rule"]._eval_context())
        if self.share:
            return company_domain
        own_request_domain = [("project_id.develops_this_odoo", "=", True), ("uat_user_ids", "in", self.ids)]
        return expression.OR([expression.normalize_domain(company_domain), own_request_domain])
