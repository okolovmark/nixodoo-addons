from odoo import fields, models


class AiReportShare(models.TransientModel):
    _name = "ai.report.share"
    _description = "Share an AI report"

    def _default_user_ids(self):
        report = self.env["ai.report"].browse(self.env.context.get("default_report_id"))
        return report._shared_users() if report else self.env["res.users"]

    report_id = fields.Many2one("ai.report", string="AI report", required=True, readonly=True, ondelete="cascade")
    user_ids = fields.Many2many(
        "res.users",
        string="Share with",
        default=_default_user_ids,
        domain=[("share", "=", False)],
        help="Colleagues who use the AI Assistant. Leave someone out to stop sharing with them.",
    )

    def action_share(self):
        self.ensure_one()
        self.report_id._share_with(self.user_ids)
        return {"type": "ir.actions.act_window_close"}
