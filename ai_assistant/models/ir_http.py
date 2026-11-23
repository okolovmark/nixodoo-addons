from odoo import models

from .ai_assistant_exchange import ASSISTANT_GROUP


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        info = super().session_info()
        info["can_ask_ai"] = self.env.user.has_group(ASSISTANT_GROUP)
        return info
