from odoo.modules.neutralize import get_neutralization_queries
from odoo.tests import TransactionCase, tagged

BLANKED_KEYS = ("claude_models", "digest_recipients", "incident_emails")


@tagged("post_install", "-at_install")
class TestNeutralize(TransactionCase):
    def test_neutralize_blanks_the_claude_models_and_the_mail_recipients(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_str("ai_assistant.claude_models", "claude-sonnet-5-5")
        icp.set_str("ai_assistant.digest_recipients", "ai-review@example.com")
        icp.set_str("ai_assistant.incident_emails", "it@example.com")
        icp.set_str("ai_assistant.max_rows", "150")
        self.env.flush_all()
        for query in get_neutralization_queries(["ai_assistant"]):
            self.env.cr.execute(query)
        self.env.invalidate_all()
        self.env.transaction.invalidate_ormcache()

        for key in BLANKED_KEYS:
            self.assertFalse(icp.get_str(f"ai_assistant.{key}"), key)
            self.assertEqual(
                icp.search_count([("key", "=", f"ai_assistant.{key}")]), 1, "the key stays for re-pointing"
            )
        self.assertEqual(icp.get_str("ai_assistant.max_rows"), "150")
        self.assertFalse(self.env["ai.claude.gateway"]._claude_configured("ai_assistant"))
        self.assertEqual(self.env["ai.assistant.exchange"]._recipients("digest_recipients"), [])
