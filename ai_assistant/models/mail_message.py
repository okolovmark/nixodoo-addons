from odoo import models


class MailMessage(models.Model):
    _inherit = "mail.message"

    def _get_with_access(self, mode="read", **kwargs):
        """The AI Assistant's answer to the current employee's own question, for a reaction, when they may read it:
        their reaction is its feedback, also on a record they may only read."""
        message = super()._get_with_access(mode=mode, **kwargs)
        if message or mode != "create" or not self or not self._is_own_ask_ai_answer():
            return message
        return super()._get_with_access(mode="read", **kwargs)

    def _message_reaction(self, content, action, partner, guest, store=None):
        result = super()._message_reaction(content, action, partner, guest, store=store)
        self.env["ai.assistant.exchange"]._ask_ai_feedback_changed(self)
        return result

    def _is_own_ask_ai_answer(self):
        self.ensure_one()
        return bool(
            self.env["ai.assistant.exchange"]
            .sudo()
            .search_count([("answer_message_id", "=", self.id), ("user_id", "=", self.env.uid)], limit=1)
        )
