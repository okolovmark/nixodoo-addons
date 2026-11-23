from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .ai_assistant_exchange import PENDING_STALE_SECONDS
from .ai_assistant_tools import (
    ASSISTANT_CLAUDE_MAX_TOKENS,
    ASSISTANT_PREFIX,
    MAX_ROWS,
    REPORT_MAX_TOKENS,
    TOOL_SECONDS,
    positive_number,
)
from odoo.addons.ai_claude.models.ai_claude_gateway import (
    CIRCUIT_COOLDOWN_SECONDS,
    CIRCUIT_THRESHOLD,
    CLAUDE_EFFORT,
    CLAUDE_EFFORT_VALUES,
    CLAUDE_MAX_TOOL_ROUNDS,
    CLAUDE_TIMEOUT_SECONDS,
    CLAUDE_TOOL_BUDGET_SECONDS,
    positive_int,
)

CLAUDE_EFFORT_SELECTION = [(effort, effort) for effort in CLAUDE_EFFORT_VALUES]
# Their readers take only a value above zero: saving another is refused, a stored one shows as the reader's default.
POSITIVE_SETTINGS = (
    "ai_assistant_claude_max_tokens",
    "ai_assistant_claude_timeout_seconds",
    "ai_assistant_claude_max_tool_rounds",
    "ai_assistant_claude_tool_budget_seconds",
    "ai_assistant_tool_seconds",
    "ai_assistant_max_rows",
    "ai_assistant_circuit_threshold",
    "ai_assistant_circuit_cooldown_seconds",
    "ai_assistant_pending_stale_seconds",
    "ai_assistant_report_max_tokens",
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    ai_assistant_claude_models = fields.Char(
        string="AI Assistant Claude models",
        config_parameter="ai_assistant.claude_models",
        help="Comma-separated, tried in order. Empty: the AI Assistant does not answer: no other model answers.",
    )
    ai_assistant_claude_effort = fields.Selection(
        CLAUDE_EFFORT_SELECTION,
        string="AI Assistant Claude effort",
        config_parameter="ai_assistant.claude_effort",
        default=CLAUDE_EFFORT,
        help="The effort Claude is asked for: a higher one takes more tokens and time per answer.",
    )
    ai_assistant_claude_max_tokens = fields.Integer(
        string="AI Assistant Claude answer tokens",
        config_parameter="ai_assistant.claude_max_tokens",
        default=ASSISTANT_CLAUDE_MAX_TOKENS,
        help="The most tokens one Claude answer may take.",
    )
    ai_assistant_claude_timeout_seconds = fields.Integer(
        string="AI Assistant Claude timeout (s)",
        config_parameter="ai_assistant.claude_timeout_seconds",
        default=CLAUDE_TIMEOUT_SECONDS,
        help="Seconds one Claude call may take.",
    )
    ai_assistant_claude_key_env = fields.Char(
        string="AI Assistant API key variable",
        config_parameter="ai_assistant.claude_key_env",
        help="The environment variable of the Odoo service that holds the API key."
        " Without it the AI Assistant does not answer.",
    )
    ai_assistant_claude_max_tool_rounds = fields.Integer(
        string="Tool rounds",
        config_parameter="ai_assistant.claude_max_tool_rounds",
        default=CLAUDE_MAX_TOOL_ROUNDS,
        help="The most tool rounds of one model's answer.",
    )
    ai_assistant_claude_tool_budget_seconds = fields.Integer(
        string="Tool loop budget (s)",
        config_parameter="ai_assistant.claude_tool_budget_seconds",
        default=CLAUDE_TOOL_BUDGET_SECONDS,
        help="Seconds one model's tool loop may take.",
    )
    # Not Integer: the reader takes fractions, which an Integer field could neither show nor save.
    ai_assistant_tool_seconds = fields.Float(
        string="Tool call timeout (s)",
        config_parameter="ai_assistant.tool_seconds",
        default=TOOL_SECONDS,
        help="Each tool call's statement timeout.",
    )
    ai_assistant_max_rows = fields.Integer(
        string="Rows per tool call",
        config_parameter="ai_assistant.max_rows",
        default=MAX_ROWS,
        help="The most rows one tool call returns.",
    )
    ai_assistant_circuit_threshold = fields.Integer(
        string="AI Assistant breaker threshold",
        config_parameter="ai_assistant.circuit_threshold",
        default=CIRCUIT_THRESHOLD,
        help="Consecutive failures after which Claude is skipped for the cooldown.",
    )
    ai_assistant_circuit_cooldown_seconds = fields.Integer(
        string="AI Assistant breaker cooldown (s)",
        config_parameter="ai_assistant.circuit_cooldown_seconds",
        default=CIRCUIT_COOLDOWN_SECONDS,
        help="Seconds an open breaker skips Claude.",
    )
    ai_assistant_pending_stale_seconds = fields.Integer(
        string="Pending question timeout (s)",
        config_parameter="ai_assistant.pending_stale_seconds",
        default=PENDING_STALE_SECONDS,
        help="A question still pending after this is closed as unavailable, its asker told.",
    )
    ai_assistant_report_max_tokens = fields.Integer(
        string="AI report data budget (tokens)",
        config_parameter="ai_assistant.report_max_tokens",
        default=REPORT_MAX_TOKENS,
        help="The most data, in estimated tokens, an AI report hands the model; past it the model is asked to"
        " aggregate further.",
    )
    ai_assistant_deny_models_extra = fields.Char(
        string="AI deny list: extra models",
        config_parameter="ai_assistant.deny_models_extra",
        help="Comma-separated fnmatch patterns of models added to the AI deny list, e.g. x_salary*,hr.leave.allocation.",
    )
    ai_assistant_deny_fields_extra = fields.Char(
        string="AI deny list: extra fields",
        config_parameter="ai_assistant.deny_fields_extra",
        help="The same for fields, as model.field, e.g. sale.order.x_margin*.",
    )
    ai_assistant_incident_emails = fields.Char(
        string="AI Assistant incident recipients",
        config_parameter="ai_assistant.incident_emails",
        help="Comma-separated addresses mailed when Claude stops answering the AI Assistant. Empty: only logged.",
    )
    ai_assistant_digest_recipients = fields.Char(
        string="AI Assistant digest recipients",
        config_parameter="ai_assistant.digest_recipients",
        help="Comma-separated addresses of the weekly digest of answers to review. Empty: only logged.",
    )

    @api.constrains(lambda settings: settings._positive_settings())
    def _check_positive_settings(self):
        for settings in self:
            labels = [
                settings._fields[name]._description_string(self.env)
                for name in self._positive_settings()
                if settings[name] <= 0
            ]
            if labels:
                raise ValidationError(self.env._("These settings must be above zero: %s", ", ".join(labels)))

    @api.model
    def get_values(self):
        """The values the readers use: a stored number not above zero, or an effort the API would reject, shows
        as the reader's default rather than as 0 or an error."""
        values = super().get_values()
        IrConfigParameter = self.env["ir.config_parameter"].sudo()
        for name in self._positive_settings():
            field = self._fields[name]
            read_value = positive_number if field.type == "float" else positive_int
            values[name] = read_value(IrConfigParameter.get_str(field.config_parameter), field.default(self))
        values["ai_assistant_claude_effort"] = self.env["ai.claude.gateway"]._claude_effort(ASSISTANT_PREFIX)
        return values

    @api.model
    def _positive_settings(self):
        """The settings whose readers take only a value above zero, in the order their error names them."""
        return POSITIVE_SETTINGS
