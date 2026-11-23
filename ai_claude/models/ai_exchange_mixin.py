import logging

from markupsafe import Markup, escape

from odoo import api, fields, models
from odoo.tools.translate import LazyTranslate

from .ai_claude_gateway import claude_trace_kinds

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

# Claude going quiet is an incident of its own: its cause (a spend limit, a key) is fixed in the Anthropic Console.
CLAUDE_INCIDENT_WINDOW_MINUTES = 30
INCIDENT_MIN_CALLS = 3
CLAUDE_INCIDENT_REASONS = (
    ("spend_limit", "the spend limit is reached"),
    ("config", "the API key or its configuration"),
    ("unavailable", "Anthropic is unavailable"),
    ("refusal", "Claude refused to answer"),
    ("bad_request", "Anthropic rejected the request (a retired model, a bad parameter)"),
    ("empty", "Claude gave empty answers"),
    ("truncated", "Claude's answers were cut off"),
)
CLAUDE_INCIDENT_BODY = _lt(
    "<p>Claude answered none of the last %(count)s questions to the %(assistant)s, over the last"
    " %(minutes)s minutes: another model answered them, or nothing did.</p>"
    "<p>Reason: %(reasons)s.</p>%(link)s"
)


class AiExchangeMixin(models.AbstractModel):
    _name = "ai.exchange.mixin"
    _description = "Who answered an assistant's question, with what and at what cost"

    conversation_id = fields.Many2one(
        "ai.conversation", string="AI conversation", readonly=True, index="btree_not_null"
    )
    provider = fields.Selection([("claude", "Claude")], readonly=True, help="Claude when a Claude model answered.")
    model = fields.Char(readonly=True)
    input_tokens = fields.Integer(readonly=True)
    output_tokens = fields.Integer(readonly=True)
    cache_read_tokens = fields.Integer(readonly=True)
    cache_write_tokens = fields.Integer(readonly=True)
    cost = fields.Float(
        string="Cost (USD)", digits=(16, 6), readonly=True, help="Of every billed Claude call for this question."
    )
    attempts = fields.Text(readonly=True, help="Every model tried for this question, in order, with its outcome.")

    def _visitor_questions(self):
        """The exchanges that answer someone's question, as the question and fallback counts read them."""
        return self

    def _claude_trace_skip_prefixes(self):
        """The starts of the names of the models in `attempts` that are not Claude's."""
        return ()

    def _claude_kinds(self):
        """The kinds of the exchange's Claude steps, in order."""
        self.ensure_one()
        return claude_trace_kinds(self.attempts, self._claude_trace_skip_prefixes())

    def _claude_fell_back(self):
        """Whether the exchange's first Claude step did not answer it."""
        kinds = self._claude_kinds()
        return bool(kinds) and kinds[0] != "answered"

    def _claude_ended_on_tool_limit(self):
        """Whether the exchange's last Claude step ran out of tool rounds or time: Claude answered its calls, the
        question needed more lookups than allowed."""
        return self._claude_kinds()[-1:] == ["tool_limit"]

    @api.model
    def _claude_incident_body(self):
        """The body of the mail that Claude answered none of an assistant's recent questions."""
        return CLAUDE_INCIDENT_BODY

    @api.model
    def _claude_incident_check(self, prefix, exchanges, assistant_name, recipients):
        """Mail once when Claude has answered none of an assistant's recent questions, once when it answers again.

        `exchanges` are the window's finished questions; at least
        INCIDENT_MIN_CALLS of them make an incident, none at all is silence. A
        question whose last Claude step ran out of tool rounds or time counts
        for neither: Claude answered its calls, the question needed more
        lookups than allowed. The asker is told so by the same rule.
        """
        icp = self.env["ir.config_parameter"].sudo()
        param = f"{prefix}.claude_incident_open"
        was_open = icp.get_str(param) == "1"
        if not self.env["ai.claude.gateway"]._claude_configured(prefix):
            # Switched off on purpose: nothing to recover from, nobody to tell.
            if was_open:
                icp.set_str(param, "0")
            return
        exchanges = exchanges.filtered(lambda exchange: not exchange._claude_ended_on_tool_limit())
        if not exchanges:
            return
        answered_by_claude = "claude" in exchanges.mapped("provider")
        if was_open and answered_by_claude:
            icp.set_str(param, "0")
            self._mail(
                recipients,
                self.env._("%s: Claude is answering again", assistant_name),
                self.env._("<p>Claude is answering the %s again.</p>") % escape(assistant_name),
                param,
            )
        elif not was_open and not answered_by_claude and len(exchanges) >= INCIDENT_MIN_CALLS:
            icp.set_str(param, "1")
            kinds = {kind for exchange in exchanges for kind in exchange._claude_kinds()}
            reasons = [label for kind, label in CLAUDE_INCIDENT_REASONS if kind in kinds] or [
                "the Claude breaker is open" if "circuit_open" in kinds else "no reason recorded"
            ]
            conversation = exchanges.sorted("id", reverse=True).conversation_id[:1]
            link = ""
            if conversation:
                url = f"{conversation.get_base_url()}/odoo/ai.conversation/{conversation.id}"
                link = Markup('<p>One of the conversations: <a href="%s">%s</a></p>') % (url, url)
            self._mail(
                recipients,
                self.env._("%s: Claude is not answering", assistant_name),
                self.env._(self._claude_incident_body())
                % {
                    "count": len(exchanges),
                    "assistant": escape(assistant_name),
                    "minutes": CLAUDE_INCIDENT_WINDOW_MINUTES,
                    "reasons": escape(", ".join(reasons)),
                    "link": link,
                },
                param,
            )

    @api.model
    def _mail(self, recipients, subject, body, purpose):
        if not recipients:
            _logger.info("ai claude: %s -- no recipients configured, logging only: %s", purpose, subject)
            return False
        self.env["mail.mail"].sudo().create(
            {
                "subject": subject,
                "body_html": body,
                "email_to": ",".join(recipients),
                "auto_delete": True,
            }
        ).send()
        return True
