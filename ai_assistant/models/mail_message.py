from odoo import models


class MailMessage(models.Model):
    _inherit = "mail.message"

    def _message_add_reaction(self, content):
        return super(MailMessage, self._ask_ai_reaction_target())._message_add_reaction(content)

    def _message_remove_reaction(self, content):
        return super(MailMessage, self._ask_ai_reaction_target())._message_remove_reaction(content)

    def _ask_ai_reaction_target(self):
        """The message to react on, as superuser when it is the AI Assistant's answer to the reacting
        employee's own question: their reaction is its feedback, also on a record they may only read."""
        self.ensure_one()
        if self.env.su:
            return self
        is_own_answer = (
            self.env["ai.assistant.exchange"]
            .sudo()
            .search_count([("answer_message_id", "=", self.id), ("user_id", "=", self.env.uid)], limit=1)
        )
        if not is_own_answer:
            return self
        self.check_access_rule("read")
        return self.sudo()
