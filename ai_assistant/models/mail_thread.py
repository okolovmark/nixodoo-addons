from odoo import models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def _message_add_reaction_after_hook(self, message, content):
        result = super()._message_add_reaction_after_hook(message, content)
        self.env["ai.assistant.exchange"]._ask_ai_feedback_changed(message)
        return result

    def _message_remove_reaction_after_hook(self, message, content):
        result = super()._message_remove_reaction_after_hook(message, content)
        self.env["ai.assistant.exchange"]._ask_ai_feedback_changed(message)
        return result
