import logging

from dateutil.relativedelta import relativedelta
from psycopg2 import errors as pg_errors

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

RETENTION_MONTHS = 12


class AiAssistantToolCall(models.Model):
    """One tool call of the AI Assistant: what it asked for and how it went, never the data it read."""

    _name = "ai.assistant.tool.call"
    _description = "AI Assistant tool call"
    _order = "id desc"
    _rec_name = "tool"

    # The row outlives its employee and a conversation its owner deletes: it is the administrators' audit of what
    # was read.
    user_id = fields.Many2one("res.users", string="Employee", readonly=True, index=True)
    conversation_id = fields.Many2one(
        "ai.conversation", string="AI conversation", readonly=True, index="btree_not_null"
    )
    exchange_id = fields.Many2one(
        "ai.assistant.exchange", string="AI Assistant answer", readonly=True, index="btree_not_null"
    )
    tool = fields.Char(readonly=True, required=True)
    model_name = fields.Char(string="Model", readonly=True, index=True)
    arguments = fields.Text(readonly=True, help="The tool's arguments as the model sent them, in JSON.")
    row_count = fields.Integer(string="Rows", readonly=True)
    documents = fields.Char(
        string="Documentation",
        readonly=True,
        help="module_docs: the modules whose README text the result held, comma separated.",
    )
    outcome = fields.Selection(
        [("ok", "OK"), ("denied", "Denied"), ("error", "Error")],
        required=True,
        readonly=True,
        index=True,
        help="Denied: the AI deny list or the employee's access rights refused the call.",
    )
    error = fields.Text(readonly=True)
    latency_ms = fields.Integer(string="Latency (ms)", readonly=True)

    @api.model
    def _log(self, values):
        """Write one row in a transaction of its own, so it outlives a question whose transaction is rolled back.

        Never raises: a lost row must not cost the employee the answer.
        """
        try:
            try:
                self._create_in_own_cursor(values)
            except pg_errors.ForeignKeyViolation:
                # A conversation or exchange created in the asking transaction is not committed yet.
                self._create_in_own_cursor({**values, "conversation_id": False, "exchange_id": False})
        except Exception:  # noqa: BLE001
            _logger.exception("ai assistant: could not log the tool call %s", values.get("tool"))

    @api.model
    def _create_in_own_cursor(self, values):
        with self.env.registry.cursor() as cr:
            fresh_log = self.with_env(self.env(cr=cr, su=True))
            fresh_log.create(values)

    @api.autovacuum
    def _gc_tool_calls(self):
        """Delete the rows older than RETENTION_MONTHS."""
        cutoff = fields.Datetime.now() - relativedelta(months=RETENTION_MONTHS)
        self.sudo().search([("create_date", "<", cutoff)], limit=models.GC_UNLINK_LIMIT).unlink()
