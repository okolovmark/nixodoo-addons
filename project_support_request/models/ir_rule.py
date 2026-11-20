import logging

from odoo import api, models

_logger = logging.getLogger(__name__)

TASK_COMPANY_RULE = "project.task_comp_rule"
SHIPPED_TASK_COMPANY_NAME = "Project/Task: multi-company"
SHIPPED_TASK_COMPANY_DOMAIN = "[('company_id', 'in', company_ids)]"
READ_DOMAIN_METHOD = "_support_request_task_read_domain"


class IrRule(models.Model):
    _inherit = "ir.rule"

    @api.model
    def _support_request_split_task_company_rule(self):
        """Take reading off core's company rule of tasks, for the module's read rule to take over.

        A deleted rule is left alone, and so is a rule whose only permission is Read: a rule keeps one.
        """
        company_rule = self.env.ref(TASK_COMPANY_RULE, raise_if_not_found=False)
        if not company_rule or not company_rule.perm_read:
            return
        if not (company_rule.perm_write or company_rule.perm_create or company_rule.perm_unlink):
            _logger.warning(
                "%s applies to reading only and keeps it: reads of tasks stay as that rule has them, "
                "without the exception for the user's own support requests",
                TASK_COMPANY_RULE,
            )
            return
        company_rule.perm_read = False
