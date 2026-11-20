import logging

from odoo import SUPERUSER_ID, api
from odoo.osv import expression
from odoo.tools.sql import escape_psql

from .models.ir_rule import (
    READ_DOMAIN_METHOD,
    SHIPPED_TASK_COMPANY_DOMAIN,
    SHIPPED_TASK_COMPANY_NAME,
    TASK_COMPANY_RULE,
)

_logger = logging.getLogger(__name__)

READ_RULE = "project_support_request.project_task_rule_company_read"


def uninstall_hook(cr, registry):
    """Give core's company rule of tasks and its copies their read back, and archive the copies of the
    module's read rule: their domain calls a method that goes with the module. The read rule goes with
    its data."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    _give_read_back_to_task_company_rules(env)
    _archive_read_rule_copies(env)


def _give_read_back_to_task_company_rules(env):
    """Give Read back to core's rule and to its active global copies: a copy an administrator made while
    the module was installed carries Read off, and with the original archived it would leave reads of
    tasks without the company restriction."""
    task_company_rule = env.ref(TASK_COMPANY_RULE, raise_if_not_found=False) or env["ir.rule"]
    read_less_copies = env["ir.rule"].search(_read_less_task_company_rule_copies_domain(task_company_rule))
    (task_company_rule | read_less_copies).perm_read = True
    _logger.info(
        "Read given back to core's company rule of tasks %s and to its copies %s",
        task_company_rule.ids,
        read_less_copies.ids,
    )


def _read_less_task_company_rule_copies_domain(task_company_rule):
    """Copies of core's rule are taken by its domain or by a name starting with its name, each as the
    rule has it and as project ships it; the module's read rule and its copies, named after core's, never
    match. Read off implies another permission (`no_access_rights`)."""
    names = {name for name in (task_company_rule.name, SHIPPED_TASK_COMPANY_NAME) if name}
    domains = [domain for domain in {task_company_rule.domain_force, SHIPPED_TASK_COMPANY_DOMAIN} if domain]
    taken_for_core = expression.OR(
        [[("name", "=like", f"{escape_psql(name)}%")] for name in names] + [[("domain_force", "in", domains)]]
    )
    return expression.AND(
        [
            [
                ("id", "not in", task_company_rule.ids),
                ("model_id.model", "=", "project.task"),
                ("groups", "=", False),
                ("perm_read", "=", False),
                ("domain_force", "not like", escape_psql(READ_DOMAIN_METHOD)),
            ],
            taken_for_core,
        ]
    )


def _archive_read_rule_copies(env):
    read_rule = env.ref(READ_RULE, raise_if_not_found=False) or env["ir.rule"]
    read_rule_copies = env["ir.rule"].search(
        [
            ("id", "not in", read_rule.ids),
            ("model_id.model", "=", "project.task"),
            ("domain_force", "like", escape_psql(READ_DOMAIN_METHOD)),
        ]
    )
    read_rule_copies.active = False
    _logger.info("Copies %s of the read rule of tasks archived", read_rule_copies.ids)
