import json

from odoo import Command
from odoo.tests import HttpCase, new_test_user, tagged

from odoo.addons.ai_assistant.models.ai_assistant_exchange import ASK_AI_SUBTYPE, ASSISTANT_GROUP
from odoo.addons.queue_job.tests.common import trap_jobs


@tagged("post_install", "-at_install")
class TestAskAiTour(HttpCase):
    def test_ask_ai_from_the_chatter_posts_the_question(self):
        partner = self.env["res.partner"].create({"name": "Ask AI Tour Partner"})
        # Trapped: the answer job never runs, so Claude is never called.
        with trap_jobs() as trap:
            self.start_tour(
                f"/web#model=res.partner&id={partner.id}&view_type=form",
                "ai_assistant_ask_ai_tour",
                login="admin",
            )
        trap.assert_jobs_count(1)
        exchange = self.env["ai.assistant.exchange"].search(
            [("res_model", "=", "res.partner"), ("res_id", "=", partner.id)]
        )
        self.assertEqual(exchange.question, "Who is the contact person of this company?")
        self.assertEqual(exchange.user_id, self.env.ref("base.user_admin"))
        self.assertEqual(exchange.question_message_id.subtype_id, self.env.ref(ASK_AI_SUBTYPE))
        self.assertEqual(exchange.allowed_company_ids, self.env.ref("base.user_admin").company_id)

    def test_no_ask_ai_on_the_assistants_own_records(self):
        conversation = self.env["ai.conversation"].create(
            {"assistant": "app", "user_id": self.env.ref("base.user_admin").id}
        )
        self.start_tour(
            f"/web#model=ai.conversation&id={conversation.id}&view_type=form",
            "ai_assistant_ask_ai_hidden_tour",
            login="admin",
        )

    def test_session_info_tells_the_client_who_may_ask_ai(self):
        employee = new_test_user(self.env, login="test_ask_ai_session")

        def can_ask_ai():
            self.authenticate(employee.login, employee.login)
            response = self.url_open(
                "/web/session/get_session_info",
                data=json.dumps({"jsonrpc": "2.0", "method": "call", "params": {}}),
                headers={"Content-Type": "application/json"},
            )
            return response.json()["result"]["can_ask_ai"]

        self.assertTrue(can_ask_ai(), "session_info lets the web client show Ask AI")
        self.env.ref(ASSISTANT_GROUP).write({"users": [Command.unlink(employee.id)]})
        self.assertFalse(can_ask_ai(), "session_info hides Ask AI outside the AI Assistant group")
