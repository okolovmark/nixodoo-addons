import logging
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged

from .test_ai_assistant_app import AppCase
from .test_ai_assistant_engine import claude_turn, text, tool_use
from .test_ask_ai import QUESTION, THUMBS_DOWN
from odoo.addons.ai_assistant.models.ai_assistant_exchange import DIGEST_MAX_ROWS
from odoo.addons.ai_assistant.models.ai_assistant_tools import ASSISTANT_PREFIX
from odoo.addons.queue_job.tests.common import trap_jobs

MAIL_LOGGER = "odoo.addons.ai_claude.models.ai_exchange_mixin"
RECIPIENTS = "ai-review@example.com, it@example.com"


@tagged("post_install", "-at_install")
class TestAiAssistantDigest(AppCase):
    def setUp(self):
        super().setUp()
        # The digest reads the last week of the whole table: questions asked in this database meanwhile stay out.
        self.env["ai.assistant.exchange"].sudo().search([]).unlink()
        self.icp = self.env["ir.config_parameter"].sudo()
        self.icp.set_param(f"{ASSISTANT_PREFIX}.digest_recipients", RECIPIENTS)
        self.startPatcher(patch.object(type(self.env["mail.mail"]), "send", autospec=True))

    def _digest_mails(self):
        return self.env["mail.mail"].search([("subject", "like", "AI Assistant: % to review")])

    def _week(self):
        """Exchanges of both kinds this week, three of them to review, and one to review of last month."""
        thumbs_down = self._answered(
            question="Who is <script>alert(1)</script> this?",
            answer="It is **AI Assistant Acme**.",
        )
        thumbs_down.answer_message_id.with_user(self.employee)._message_add_reaction(THUMBS_DOWN)
        _conversation, not_covered = self._app_answered(
            answer="Nothing here says how rooms are booked. Please ask IT.\n\n[[NOT_COVERED]]",
            question="How do I book a meeting room?",
            screen=self._partner_screen(),
        )
        _conversation, unavailable, _trap = self._app_ask("And the car park?")
        unavailable.sudo().write({"state": "unavailable", "error": "no Claude model answered"})
        fine = self._answered(question="Is it in Belgium?")
        pending, _trap = self._ask("Still thinking?")
        last_month = self._answered(question="An old question")
        last_month.sudo().feedback = "down"
        self.env.cr.execute(
            "UPDATE ai_assistant_exchange SET create_date = create_date - interval '8 days' WHERE id = %s",
            (last_month.id,),
        )
        self.env.invalidate_all()
        return thumbs_down, not_covered, unavailable, fine, pending, last_month

    def _digest_row_of(self, body, question):
        [row] = [row for row in body.split("<li>") if question in row]
        return row

    def test_the_digest_mails_every_answer_to_review_of_both_kinds_this_week_s_marked_new(self):
        thumbs_down, not_covered, unavailable, fine, pending, last_month = self._week()
        self.assertEqual(
            (thumbs_down.feedback, not_covered.not_covered, fine.state, pending.state),
            ("down", True, "answered", "pending"),
        )

        self.assertTrue(self.env["ai.assistant.exchange"]._cron_weekly_digest())

        mail = self._digest_mails()
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail.subject, "AI Assistant: 4 questions to review, 3 new this week")
        self.assertEqual(mail.email_to, "ai-review@example.com,it@example.com")
        body = mail.body_html
        self.assertIn(
            "4 answers of the AI Assistant wait in To Review, 3 new this week. 5 questions were asked this week.",
            body,
        )
        self.assertIn("Who is &lt;script&gt;alert(1)&lt;/script&gt; this?", body, "a question is escaped")
        self.assertNotIn("<script>", body)
        self.assertIn("<b>AI Assistant Acme</b>", body, "the answer as the asker read it")
        for snippet in (
            "Erin Employee - Ask AI - thumbs down - record: Contact: AI Assistant Acme",
            "AI Assistant - not covered - screen: Contact: AI Assistant Acme",
            "AI Assistant - not answered: no Claude model answered - screen: none",
            "How do I book a meeting room?",
            "And the car park?",
            "no Claude model answered",
        ):
            self.assertIn(snippet, body)
        for left_out in ("Is it in Belgium?", "Still thinking?"):
            self.assertNotIn(left_out, body)
        self.assertNotIn("<b>New</b>", self._digest_row_of(body, "An old question"), "to review, of last month")
        base_url = self.icp.get_param("web.base.url")
        for exchange in (thumbs_down, not_covered, unavailable):
            row = self._digest_row_of(body, exchange.question.replace("<", "&lt;").replace(">", "&gt;"))
            self.assertTrue(row.startswith("<p><b>New</b> - "), exchange.question)
            answer_url = f"{base_url}/web#id={exchange.id}&amp;model=ai.assistant.exchange&amp;view_type=form"
            conversation_url = (
                f"{base_url}/web#id={exchange.conversation_id.id}&amp;model=ai.conversation&amp;view_type=form"
            )
            self.assertIn(f'<a href="{answer_url}">Review</a> - <a href="{conversation_url}">AI conversation</a>', row)

    def test_a_reviewed_answer_leaves_the_digest_which_is_mailed_while_there_is_something_to_say(self):
        thumbs_down, not_covered, unavailable, fine, pending, last_month = self._week()
        (thumbs_down | not_covered).with_user(self.administrator).action_mark_fixed()
        unavailable.with_user(self.administrator).action_dismiss()
        self.assertTrue(self.env["ai.assistant.exchange"]._cron_weekly_digest())
        mail = self._digest_mails()
        self.assertEqual(mail.subject, "AI Assistant: 1 question to review, 0 new this week")
        self.assertIn("1 answer of the AI Assistant waits in To Review, 0 new this week.", mail.body_html)

        self._digest_mails().unlink()
        self.env.cr.execute("UPDATE ai_assistant_exchange SET create_date = create_date - interval '8 days'")
        self.env.invalidate_all()
        self.assertTrue(self.env["ai.assistant.exchange"]._cron_weekly_digest(), "a backlog without a question")
        self.assertIn("An old question", self._digest_mails().body_html)

        self._digest_mails().unlink()
        last_month.with_user(self.administrator).action_mark_fixed()
        self.assertFalse(self.env["ai.assistant.exchange"]._cron_weekly_digest(), "nothing to review, nobody asked")
        self.assertFalse(self._digest_mails())

        self._answered(question="Is it still in Belgium?")
        self.assertTrue(self.env["ai.assistant.exchange"]._cron_weekly_digest(), "nothing to review, a question")
        mail = self._digest_mails()
        self.assertEqual(mail.subject, "AI Assistant: 0 questions to review, 0 new this week")
        self.assertIn(
            "0 answers of the AI Assistant wait in To Review, 0 new this week. 1 question was asked this week.",
            mail.body_html,
        )
        self.assertIn("<li>none</li>", mail.body_html)

    def test_a_new_answer_is_no_question_asked_and_names_the_question_it_answers(self):
        last_month = self._answered(question="An old question")
        last_month.sudo().feedback = "down"
        self.env.cr.execute(
            "UPDATE ai_assistant_exchange SET create_date = create_date - interval '8 days' WHERE id = %s",
            (last_month.id,),
        )
        self.env.invalidate_all()
        with trap_jobs():
            last_month.with_user(self.administrator).action_send_new_answer()
        reply = last_month.sudo().reply_ids
        reply.write({"state": "unavailable", "error": "no Claude model answered"})

        self.assertTrue(self.env["ai.assistant.exchange"]._cron_weekly_digest())

        body = self._digest_mails().body_html
        self.assertIn(
            "1 answer of the AI Assistant waits in To Review, 1 new this week. 0 questions were asked this week.",
            body,
        )
        row = self._digest_row_of(body, "An old question")
        self.assertTrue(row.startswith("<p><b>New</b> - "), "new to review this week")
        asked_on = fields.Datetime.to_string(last_month.create_date)[:10]
        self.assertIn(f"Ask AI, new answer to the question of {asked_on} - not answered", row)

    def test_without_recipients_or_questions_the_digest_is_only_logged(self):
        self.assertFalse(self.env["ai.assistant.exchange"]._cron_weekly_digest(), "a week without questions")
        self._week()
        self.icp.set_param(f"{ASSISTANT_PREFIX}.digest_recipients", " ")
        # An int: Odoo names its level 25 "INFO" too, which logging would look up instead.
        with self.assertLogs(MAIL_LOGGER, logging.INFO) as logs:
            self.assertFalse(self.env["ai.assistant.exchange"]._cron_weekly_digest())
        self.assertIn("ai_assistant.digest_recipients -- no recipients configured", logs.output[0])
        self.assertFalse(self._digest_mails())

    def test_digest_reasons_and_place(self):
        thumbs_down, not_covered, unavailable, fine, pending, _last_month = self._week()
        self.assertEqual(thumbs_down._digest_reasons(), ["thumbs down"])
        self.assertEqual(not_covered._digest_reasons(), ["not covered"])
        self.assertEqual(unavailable._digest_reasons(), ["not answered: no Claude model answered"])
        self.assertEqual(fine._digest_reasons(), [])
        self.assertEqual(pending._digest_reasons(), [], "a pending question is still being answered")
        unavailable.sudo().error = False
        self.assertEqual(unavailable._digest_reasons(), ["not answered: no reason recorded"])

        self.assertEqual(thumbs_down._digest_place(), "record: Contact: AI Assistant Acme")
        self.assertEqual(not_covered._digest_place(), "screen: Contact: AI Assistant Acme")
        self.assertEqual(unavailable._digest_place(), "screen: none")
        unavailable.sudo().write({"res_model": "x.uninstalled.model", "res_id": 7})
        self.assertEqual(unavailable._digest_place(), "screen: x.uninstalled.model 7")

    def _answered_after_module_docs(self, arguments, question=QUESTION):
        exchange, trap = self._ask(question=question)
        answer_turns = (
            claude_turn([tool_use("module_docs", arguments)], "tool_use"),
            claude_turn([text("Belgium.")], "end_turn"),
        )
        with self._claude(*answer_turns):
            trap.perform_enqueued_jobs()
        return exchange

    def test_digest_modules_are_those_whose_documentation_the_answer_read(self):
        exchange = self._answered_after_module_docs({"model": "res.partner", "module": "", "offset": 0})
        call = self.env["ai.assistant.tool.call"].search([("exchange_id", "=", exchange.id)])
        self.assertEqual(
            (call.tool, call.model_name, call.conversation_id), ("module_docs", "res.partner", exchange.conversation_id)
        )
        module_names = [module.name for module, _text in self._tools(self.employee)._module_readmes("res.partner")]
        self.assertEqual(exchange._digest_modules(), module_names)
        self.assertIn(", ".join(module_names), exchange._digest_row())
        self.assertEqual(self._answered(question="Again?")._digest_modules(), [], "no module_docs call, no module")

    def test_the_digest_lists_at_most_fifty_rows(self):
        self.env["ai.assistant.exchange"].sudo().create(
            [
                {
                    "kind": "app",
                    "user_id": self.employee.id,
                    "question": f"Question {index}",
                    "state": "unavailable",
                    "error": "no Claude model answered",
                }
                for index in range(DIGEST_MAX_ROWS + 1)
            ]
        )
        self.env["ai.assistant.exchange"]._cron_weekly_digest()
        body = self._digest_mails().body_html
        self.assertIn(f"{DIGEST_MAX_ROWS + 1} answers of the AI Assistant wait in To Review", body)
        self.assertEqual(body.count("<li>"), DIGEST_MAX_ROWS)
        self.assertNotIn("Question 0<", body, "the newest first")

    def test_recipients_read_a_comma_separated_parameter(self):
        Exchange = self.env["ai.assistant.exchange"]
        self.assertEqual(Exchange._recipients("digest_recipients"), ["ai-review@example.com", "it@example.com"])
        self.assertEqual(Exchange._recipients("no_such_parameter"), [])

    def test_the_weekly_digest_cron_keeps_running(self):
        cron = self.env.ref("ai_assistant.cron_weekly_digest").with_context(active_test=False)
        self.assertEqual((cron.numbercall, cron.interval_type, cron.code), (-1, "weeks", "model._cron_weekly_digest()"))
        self.assertEqual((cron.model_id.model, cron.interval_number), ("ai.assistant.exchange", 1))
