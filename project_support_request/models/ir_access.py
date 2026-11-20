import logging

from odoo import api, models
from odoo.fields import Domain
from odoo.tools.safe_eval import safe_eval

from ..hooks import (
    CORE_TASK_ACCESS_XMLID,
    match_core_task_accesses,
    read_core_task_access_row,
    set_read_of_core_task_restrictions,
)

_logger = logging.getLogger(__name__)


class IrAccess(models.Model):
    _inherit = "ir.access"

    def _support_request_core_task_domain(self):
        """Return the domain of core's multi-company restriction of tasks as it stands, evaluated as
        core evaluates a restriction, in the company selection of this environment.

        Core's restriction as it stands is every active access taken for it that covers update. One
        with a group is a permission, OR-ed by Odoo: it fills core's place and restricts nothing. With
        nothing in core's place, reads keep the domain of project's file, or the stock one when the file
        cannot be read or has no row for it: the module never lifts the company restriction of reads
        beyond its exception, so lifting it takes archiving the module's restriction too."""
        core_row = read_core_task_access_row(self.env)
        accesses_in_core_place = self.search(match_core_task_accesses(self.env, core_row) & Domain("active", "=", True))
        if accesses_in_core_place:
            domains = accesses_in_core_place.filtered(lambda access: access.kind == "restriction").mapped("domain")
        else:
            domains = [core_row.get("domain")]
        eval_context = self._eval_context()
        return Domain.AND(Domain(safe_eval(domain, eval_context)) if domain else Domain.TRUE for domain in domains)

    @api.model
    def _support_request_narrow_core_task_restrictions(self):
        """Take read off core's multi-company restriction of tasks and its copies, for the module's read
        restriction to take over; create, update and delete stay as they are. Run by the module's data
        on every install and update."""
        restrictions = set_read_of_core_task_restrictions(self.env, has_read=False)
        if not restrictions:
            return
        core_access = self.env.ref(CORE_TASK_ACCESS_XMLID, raise_if_not_found=False) or self.browse()
        _logger.info(
            "Read taken off the restrictions of tasks %s, core's %s among them",
            restrictions.ids,
            (restrictions & core_access).ids,
        )
