from odoo import api, models

from .ai_assistant_tools import ASSISTANT_CLAUDE_MAX_TOKENS, ASSISTANT_PREFIX

# The AI Assistant's fallbacks that differ from ai_claude's, for the readers the gateway shares.
ASSISTANT_INT_FALLBACKS = {"claude_max_tokens": ASSISTANT_CLAUDE_MAX_TOKENS}


class AiClaudeGateway(models.AbstractModel):
    _inherit = "ai.claude.gateway"

    @api.model
    def _assistant_int_param(self, prefix, suffix, default):
        """For the AI Assistant's parameters, its own default where ASSISTANT_INT_FALLBACKS has one."""
        if prefix == ASSISTANT_PREFIX:
            default = ASSISTANT_INT_FALLBACKS.get(suffix, default)
        return super()._assistant_int_param(prefix, suffix, default)
