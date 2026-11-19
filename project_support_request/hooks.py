import csv
import logging

from odoo.fields import Domain
from odoo.tools import file_path
from odoo.tools.sql import escape_like_value

_logger = logging.getLogger(__name__)

CORE_TASK_ACCESS_FILE = "project/security/ir.access.csv"
CORE_TASK_ACCESS_ID = "task_comp_rule"
CORE_TASK_ACCESS_XMLID = f"project.{CORE_TASK_ACCESS_ID}"
STOCK_CORE_TASK_ACCESS_NAME = "Project/Task: multi-company"
STOCK_CORE_TASK_ACCESS_DOMAIN = "[('company_id', 'in', company_ids + [False])]"
READ_RESTRICTION_XMLID = "project_support_request.task_comp_rule_read"
READ_DOMAIN_METHOD = "_support_request_task_read_domain"


def uninstall_hook(env):
    _give_read_back_to_core_task_restrictions(env)
    _archive_read_restriction_copies(env)


def _give_read_back_to_core_task_restrictions(env):
    """Give read back to core's multi-company restriction of tasks and to its copies that lack it,
    whatever took it off, the module or an administrator before the install."""
    restrictions = set_read_of_core_task_restrictions(env, has_read=True)
    _logger.info("Read given back to the restrictions of tasks %s", restrictions.ids)


def _archive_read_restriction_copies(env):
    """Archive every access whose domain calls the module's method, on any model and with or without a
    group, and the copies Customize made of the module's read restriction, named after it: once the
    module is gone, that domain no longer evaluates."""
    read_restriction = env.ref(READ_RESTRICTION_XMLID, raise_if_not_found=False) or env["ir.access"]
    copy_of_read_restriction = Domain("domain", "like", escape_like_value(READ_DOMAIN_METHOD))
    if read_restriction.name:
        copy_of_read_restriction |= (
            Domain("model_id.model", "=", "project.task")
            & Domain("group_id", "=", False)
            & Domain("name", "=like", f"{escape_like_value(read_restriction.name)}%")
        )
    copies = env["ir.access"].search(Domain("id", "not in", read_restriction.ids) & copy_of_read_restriction)
    copies.active = False
    _logger.info("Copies %s of the read restriction of tasks %s archived", copies.ids, read_restriction.ids)


def set_read_of_core_task_restrictions(env, has_read):
    """Tick or untick read on core's multi-company restriction of tasks, archived too, and on its active
    copies, all without a group, leaving create, update and delete as they are; return the accesses
    changed. Unticking takes only those that cover update, which the read restriction follows; ticking
    takes them whatever they cover, to give the company restriction of reads back as Odoo ships it once
    the read restriction is gone. Core's record goes on restricting once an administrator unarchives it,
    and a group makes an access a permission."""
    core_access = env.ref(CORE_TASK_ACCESS_XMLID, raise_if_not_found=False) or env["ir.access"]
    restrictions = (
        env["ir.access"]
        .with_context(active_test=False)
        .search(
            # taking read off needs update: an access left with no operation is not a valid key
            match_core_task_accesses(env, read_core_task_access_row(env), must_cover_update=not has_read)
            & Domain("group_id", "=", False)
            & Domain("for_read", "=", not has_read)
            & (Domain("active", "=", True) | Domain("id", "in", core_access.ids))
        )
    )
    # Not through the inverse of for_read: each write of an access clears the cache, and with it the Read
    # still to be inverted on the records after the first.
    for operation, accesses in restrictions.grouped("operation").items():
        flags = set(operation) | {"r"} if has_read else set(operation) - {"r"}
        accesses.operation = "".join(flag for flag in "crud" if flag in flags)
    return restrictions


def match_core_task_accesses(env, core_row, *, must_cover_update=True):
    """Return the search domain of the accesses of tasks taken for core's multi-company restriction: core's
    record and the copies Customize makes of it ("<name> (copy)"), by a name starting with core's or by
    core's domain, each as project's file and the live record give it; with must_cover_update, only those
    that cover update, so that reads follow what restricts writes. Never the module's read restriction or
    its copies, named after core's too."""
    core_access = env.ref(CORE_TASK_ACCESS_XMLID, raise_if_not_found=False) or env["ir.access"]
    names = {name for name in (core_row.get("name"), core_access.name) if name}
    domains = list({domain for domain in (core_row.get("domain"), core_access.domain) if domain})
    taken_for_core = Domain.OR(Domain("name", "=like", f"{escape_like_value(name)}%") for name in names)
    if domains:
        taken_for_core |= Domain("domain", "in", domains)
    if core_access:
        taken_for_core |= Domain("id", "=", core_access.id)
    return (
        Domain("model_id.model", "=", "project.task")
        & (Domain("for_write", "=", True) if must_cover_update else Domain.TRUE)
        & taken_for_core
        & Domain("domain", "not like", escape_like_value(READ_DOMAIN_METHOD))
    )


def read_core_task_access_row(env):
    """Core's row of the restriction in project's file, or its stock name and domain when the file or
    the row cannot be read."""
    try:
        with open(file_path(CORE_TASK_ACCESS_FILE, env=env), newline="", encoding="utf-8") as access_file:
            for row in csv.DictReader(access_file):
                if row.get("id") == CORE_TASK_ACCESS_ID:
                    return row
        _logger.warning("No row %s in %s: taken as Odoo ships it", CORE_TASK_ACCESS_ID, CORE_TASK_ACCESS_FILE)
    except OSError as error:
        _logger.warning(
            "Cannot read %s, %s taken as Odoo ships it: %s", CORE_TASK_ACCESS_FILE, CORE_TASK_ACCESS_ID, error
        )
    return {"id": CORE_TASK_ACCESS_ID, "name": STOCK_CORE_TASK_ACCESS_NAME, "domain": STOCK_CORE_TASK_ACCESS_DOMAIN}
