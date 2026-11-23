from unittest.mock import patch

from lxml import etree, html

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import format_datetime
from odoo.tools.safe_eval import safe_eval

from .test_ai_assistant_app import AppCase
from .test_ai_assistant_engine import claude_turn, text
from .test_ask_ai import QUESTION, THUMBS_DOWN
from odoo.addons.ai_assistant.models.ai_assistant_exchange import (
    APP_ANSWER_NOTIFICATION,
    ASSISTANT_GROUP,
    question_quote,
)
from odoo.addons.ai_claude.models.ai_claude_gateway import NOT_COVERED
from odoo.addons.queue_job.tests.common import trap_jobs

APP = "ai_assistant"


def filter_domain(env, model, name, view_id=False):
    """The domain of the filter `name` in the model's search view, its default one without `view_id`."""
    arch = env[model].get_views([(view_id, "search")])["views"]["search"]["arch"]
    [node] = etree.fromstring(arch).xpath(f"//filter[@name='{name}']")
    return safe_eval(node.get("domain"))


class ReviewCase(AppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The AI Assistant's partner has no address: its notification mails go from the catchall domain's.
        cls.env["ir.config_parameter"].sudo().set_param("mail.catchall.domain", "example.com")

    def _flagged(self, question=QUESTION, user=None, company=None, companies=None):
        """An app question, in a conversation of its own, that was not answered: an answer to review."""
        user = user or self.employee
        company = company or self.company
        conversation = self.env["ai.conversation"].sudo().create({"assistant": "app", "user_id": user.id})
        return (
            self.env["ai.assistant.exchange"]
            .sudo()
            .create(
                {
                    "kind": "app",
                    "user_id": user.id,
                    "question": question,
                    "conversation_id": conversation.id,
                    "company_id": company.id,
                    "allowed_company_ids": [Command.set((companies or company).ids)],
                    "state": "unavailable",
                    "error": "no Claude model answered",
                }
            )
        )

    def _send_new_answer(self, exchange):
        """(new answer, trap) of an administrator's Send new answer on `exchange`, its job trapped."""
        with trap_jobs() as trap:
            exchange.with_user(self.administrator).action_send_new_answer()
        self.env.flush_all()
        return exchange.sudo().reply_ids.sorted("id")[-1:], trap

    def _bot_posts(self, conversation):
        return conversation.message_ids.filtered(lambda message: message.author_id == self.bot_partner)


@tagged("post_install", "-at_install")
class TestAiAssistantReviewQueue(ReviewCase):
    def test_to_review_is_an_answer_to_review_that_no_administrator_closed(self):
        thumbs_down = self._answered()
        answer_note = thumbs_down.answer_message_id.with_user(self.employee)
        answer_note._message_add_reaction(THUMBS_DOWN)
        _conversation, not_covered = self._app_answered(answer=f"Nobody knows.\n\n{NOT_COVERED}")
        unavailable = self._flagged()
        fine = self._answered(question="Is it in Belgium?")
        pending, _trap = self._ask("Still thinking?")
        exchanges = thumbs_down | not_covered | unavailable | fine | pending
        self.assertEqual(exchanges.mapped("to_review"), [True, True, True, False, False], "pending is never flagged")
        self.assertEqual(
            self.env["ai.assistant.exchange"].search([("id", "in", exchanges.ids), ("to_review", "=", True)]),
            thumbs_down | not_covered | unavailable,
            "stored, so the queue is searched",
        )

        answer_note._message_remove_reaction(THUMBS_DOWN)
        self.assertFalse(thumbs_down.to_review, "a thumbs-down taken back")
        (not_covered | unavailable).with_user(self.administrator).action_dismiss()
        self.assertEqual((not_covered | unavailable).mapped("to_review"), [False, False])

    def test_fixed_and_dismiss_close_every_selected_answer_as_their_administrator(self):
        first, second, third = (self._flagged(f"Question {index}?") for index in range(3))
        (first | second).with_user(self.administrator).action_mark_fixed()
        third.with_user(self.administrator).action_dismiss()
        for exchange, review_state in ((first, "fixed"), (second, "fixed"), (third, "dismissed")):
            self.assertEqual(
                (exchange.review_state, exchange.reviewed_by_id, exchange.to_review),
                (review_state, self.administrator, False),
                exchange.question,
            )
            self.assertTrue(exchange.reviewed_date)

    def test_fixed_and_dismiss_leave_the_selected_answers_not_to_review_as_they_are(self):
        to_review = self._flagged("A?")
        dismissed = self._flagged("B?")
        dismissed.with_user(self.administrator).action_dismiss()
        fine = self._answered(question="Is it in Belgium?")
        pending, _trap = self._ask("Still thinking?")

        (to_review | dismissed | fine | pending).with_user(self.administrator).action_mark_fixed()

        self.assertEqual(
            (to_review | dismissed | fine | pending).mapped("review_state"),
            ["fixed", "dismissed", False, False],
            "a reviewed answer keeps its review, one never flagged or pending gets none",
        )
        self.assertFalse((fine | pending).reviewed_by_id)
        with self.assertRaisesRegex(UserError, "^None of the selected answers is to review.$"):
            (dismissed | fine | pending).with_user(self.administrator).action_dismiss()

    def test_to_review_follows_not_covered_alone(self):
        exchange = self._answered()
        self.assertFalse(exchange.to_review)
        exchange.sudo().write({"not_covered": True})
        self.assertTrue(exchange.to_review)
        exchange.sudo().write({"not_covered": False})
        self.assertFalse(exchange.to_review)

    def test_the_review_queue_s_filter_is_indexed(self):
        self.env.cr.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'ai_assistant_exchange' AND indexdef LIKE '%(to_review)'"
        )
        self.assertTrue(self.env.cr.fetchone(), "the queue's filter is indexed")

    def test_an_answer_is_named_by_its_question(self):
        long_question = "Where " + "was this company founded " * 6
        exchanges = self._flagged() | self._flagged(long_question)
        self.assertEqual(exchanges.mapped("display_name"), [QUESTION, question_quote(long_question)])

    def test_only_an_administrator_reviews_and_through_the_buttons_alone(self):
        exchange = self._flagged()
        for action in ("action_mark_fixed", "action_dismiss", "action_send_new_answer"):
            with self.subTest(action), trap_jobs() as trap:
                with self.assertRaises(AccessError):
                    getattr(exchange.with_user(self.employee), action)()
                trap.assert_jobs_count(0)
        self.assertEqual((exchange.review_state, exchange.reply_ids), (False, self.env["ai.assistant.exchange"]))
        with self.assertRaises(AccessError, msg="the administrators' access stays read-only"):
            exchange.with_user(self.administrator).write({"review_state": "fixed"})

    def test_to_review_opens_the_answers_to_review_with_the_review_filters(self):
        to_review, fixed, dismissed = self._flagged("A?"), self._flagged("B?"), self._flagged("C?")
        fixed.with_user(self.administrator).action_mark_fixed()
        dismissed.with_user(self.administrator).action_dismiss()
        action = self.env.ref(f"{APP}.ai_assistant_exchange_action_to_review")
        self.assertEqual(
            (action.name, action.res_model, action.view_mode, safe_eval(action.context)),
            ("To Review", "ai.assistant.exchange", "tree,kanban,form", {"search_default_to_review": 1}),
        )
        self.assertIn("marked Fixed or Dismissed", action.help)
        search = self.env["ai.assistant.exchange"].get_views([(False, "search")])["views"]["search"]["arch"]
        self.assertEqual(etree.fromstring(search).xpath("//filter[@name='to_review']/@string"), ["To Review"])
        exchanges = to_review | fixed | dismissed
        for filter_name, expected in (
            ("to_review", to_review),
            ("review_fixed", fixed),
            ("review_dismissed", dismissed),
        ):
            domain = filter_domain(self.env, "ai.assistant.exchange", filter_name)
            found = self.env["ai.assistant.exchange"].search([*domain, ("id", "in", exchanges.ids)])
            self.assertEqual(found, expected, filter_name)

    def test_the_review_buttons_are_on_the_form_and_on_the_selection_of_the_list(self):
        views = (
            self.env["ai.assistant.exchange"]
            .with_user(self.administrator)
            .get_views([(False, "form"), (False, "list")])
        )
        form = etree.fromstring(views["views"]["form"]["arch"])
        self.assertEqual(
            form.xpath("//header/button/@name"), ["action_mark_fixed", "action_dismiss", "action_send_new_answer"]
        )
        self.assertTrue(form.xpath("//field[@name='reply_to_id']") and form.xpath("//field[@name='reply_ids']"))
        tree = etree.fromstring(views["views"]["list"]["arch"])
        self.assertEqual(tree.xpath("//header/button/@name"), ["action_mark_fixed", "action_dismiss"])
        self.assertTrue(tree.xpath("//field[@name='review_state']"))


@tagged("post_install", "-at_install")
class TestAiAssistantNewAnswer(ReviewCase):
    def test_a_new_answer_in_the_app_answers_again_in_the_conversation_and_notifies_the_asker(self):
        self.employee.tz = "Asia/Tokyo"
        self.assertNotEqual(self.employee.tz, self.env.user.tz, "the date is the asker's, not the sender's")
        conversation, _earlier = self._app_answered(answer="Earlier answer.", question="Earlier?")
        _same, original = self._app_answered(answer="A wrong answer.", conversation=conversation)
        self._app_answered(answer="Later answer.", question="Later?", conversation=conversation)
        conversation.with_user(self.employee).app_feedback(original.id, "down")

        reply, trap = self._send_new_answer(original)

        self.assertEqual(
            (reply.kind, reply.user_id, reply.question, reply.conversation_id, reply.reply_to_id, reply.state),
            ("app", self.employee, QUESTION, conversation, original, "pending"),
        )
        self.assertEqual((reply.company_id, reply.allowed_company_ids), (self.company, self.company))
        self.assertFalse(reply.question_message_id)
        self.assertEqual(
            (original.review_state, original.reviewed_by_id, original.to_review, original.reply_ids),
            ("fixed", self.administrator, False, reply),
        )
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(reply._app_answer, properties={"identity_key": reply._answer_job_key()})
        self.assertEqual(trap.enqueued_jobs[0].channel, "root.ai_assistant")
        self.assertFalse(self._bot_posts(conversation), "the asker is told of the answer, not of the question")

        with self._claude(claude_turn([text("The right answer.")], "end_turn")) as claude:
            trap.perform_enqueued_jobs()

        messages = claude.create.call_args.kwargs["messages"]
        self.assertEqual(
            messages[:-1],
            [{"role": "user", "content": "Earlier?"}, {"role": "assistant", "content": "Earlier answer."}],
            "the history stops before the question answered again: the wrong answer and what came after stay out",
        )
        self.assertTrue(messages[-1]["content"].endswith(QUESTION))
        self.assertEqual((reply.state, reply.answer, reply.to_review), ("answered", "The right answer.", False))
        reply_to = {"id": original.id, "question": QUESTION, "date": fields.Datetime.to_string(original.create_date)}
        self.assertEqual(reply._app_message()["reply_to"], reply_to)
        self.assertFalse(original._app_message()["reply_to"])
        payloads = self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)
        self.assertIn(self._app_payload(conversation, reply), payloads, "the asker's open app shows it")

        [post] = self._bot_posts(conversation)
        self.assertEqual(
            (post.subtype_id, post.message_type, post.partner_ids),
            (self.env.ref("mail.mt_note"), "comment", self.employee.partner_id),
        )
        notifications = self.env["mail.notification"].search([("mail_message_id", "=", post.id)])
        self.assertEqual(notifications.res_partner_id, self.employee.partner_id, "the asker alone")
        body = html.fromstring(post.body)
        asked_on = format_datetime(self.env, original.create_date, tz=self.employee.tz, dt_format="short")
        self.assertIn(f'a new answer to your question of {asked_on}: "{QUESTION}"', body.text_content())
        [link] = body.xpath("//a/@href")
        menu = self.env.ref(f"{APP}.ai_assistant_app_menu")
        self.assertEqual(
            link,
            f"{original.get_base_url()}/web#action=ai_assistant.app&conversation_id={conversation.id}"
            f"&menu_id={menu.id}",
            "the app opened on the conversation",
        )
        self.assertEqual(
            [message["id"] for message in conversation.with_user(self.employee).app_messages()][-1], reply.id
        )

    def test_a_new_answer_on_a_record_replies_to_the_question_note_and_notifies_the_asker_alone(self):
        earlier = self._answered(question="Earlier?", answer="Earlier answer.", user=self.colleague)
        original = self._answered(answer="A wrong answer.")
        original.answer_message_id.with_user(self.employee)._message_add_reaction(THUMBS_DOWN)
        self._answered(question="Later?", answer="Later answer.")

        reply, trap = self._send_new_answer(original)

        self.assertEqual(
            (reply.kind, reply.user_id, reply.res_model, reply.res_id, reply.question, reply.reply_to_id),
            ("ask_ai", self.employee, "res.partner", self.partner.id, QUESTION, original),
        )
        self.assertEqual(
            (reply.question_message_id, reply.conversation_id, reply.allowed_company_ids),
            (original.question_message_id, original.conversation_id, self.company),
        )
        self.assertEqual((original.review_state, original.reviewed_by_id), ("fixed", self.administrator))
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(reply._ask_ai_answer, properties={"identity_key": reply._answer_job_key()})

        with self._claude(claude_turn([text("The right answer.")], "end_turn")) as claude:
            trap.perform_enqueued_jobs()

        self.assertEqual(
            claude.create.call_args.kwargs["messages"][:-1],
            [
                {"role": "user", "content": earlier._history_question(self.employee)},
                {"role": "assistant", "content": "Earlier answer."},
            ],
            "the history stops before the question answered again",
        )
        note = reply.answer_message_id
        self.assertEqual(
            (note.parent_id, note.author_id, note.subtype_id),
            (original.question_message_id, self.bot_partner, self.ask_ai_subtype),
        )
        self.assertIn(f'New answer to the question of Erin Employee: "{QUESTION}"', note.body)
        self.assertIn("The right answer.", note.body)
        notifications = self.env["mail.notification"].search([("mail_message_id", "=", note.id)])
        self.assertEqual(notifications.res_partner_id, self.employee.partner_id, "the asker alone")
        self.assertEqual(self._notified(original.question_message_id | original.answer_message_id), 0)

    def test_a_new_answer_that_is_not_answered_notifies_nobody(self):
        original = self._answered(answer="A wrong answer.")
        original.sudo().feedback = "down"
        reply, trap = self._send_new_answer(original)
        # Without a Claude key the answer is unavailable.
        trap.perform_enqueued_jobs()
        self.assertEqual((reply.state, reply.to_review), ("unavailable", True), "to review like any answer")
        self.assertIn("not available", reply.answer_message_id.body)
        self.assertEqual(self._notified(reply.answer_message_id), 0)

        conversation, app_original = self._app_answered(answer="A wrong answer.")
        app_original.sudo().feedback = "down"
        app_reply, trap = self._send_new_answer(app_original)
        trap.perform_enqueued_jobs()
        self.assertEqual(app_reply.state, "unavailable")
        self.assertFalse(self._bot_posts(conversation))

    def test_a_fixed_answer_and_a_new_answer_to_review_get_a_new_answer_to_the_question_asked(self):
        conversation, _earlier = self._app_answered(answer="Earlier answer.", question="Earlier?")
        _same, original = self._app_answered(answer="A wrong answer.", conversation=conversation)
        original.sudo().feedback = "down"
        original.with_user(self.administrator).action_mark_fixed()
        first_reply, trap = self._send_new_answer(original)
        trap.perform_enqueued_jobs()
        self.assertTrue(first_reply.to_review)

        second_reply, trap = self._send_new_answer(first_reply)

        self.assertEqual(
            (second_reply.reply_to_id, first_reply.review_state, original.reply_ids),
            (first_reply, "fixed", first_reply),
        )
        self.assertEqual(second_reply._app_message()["reply_to"]["id"], original.id, "the question the asker asked")
        with self._claude(claude_turn([text("The right answer.")], "end_turn")) as claude:
            trap.perform_enqueued_jobs()
        self.assertEqual(
            claude.create.call_args.kwargs["messages"][:-1],
            [{"role": "user", "content": "Earlier?"}, {"role": "assistant", "content": "Earlier answer."}],
        )
        self.assertEqual(len(self._bot_posts(conversation)), 1, "only the new answer that answered is notified")

    def test_a_new_answer_reads_in_the_companies_of_the_question_the_asker_still_has(self):
        both = self.company | self.other_company
        kept = self._flagged("Kept?", company=self.other_company, companies=both)
        reply, _trap = self._send_new_answer(kept)
        self.assertEqual(reply._asked_company_ids(), [self.other_company.id, self.company.id], "the current one first")

        narrowed = self._flagged("Narrowed?", company=self.other_company, companies=both)
        self.employee.write({"company_ids": [Command.unlink(self.other_company.id)]})
        reply, _trap = self._send_new_answer(narrowed)
        self.assertEqual((reply.company_id, reply.allowed_company_ids), (self.company, self.company))

    def test_a_new_answer_is_refused_with_its_reason_and_nothing_queued(self):
        def assert_refused(exchange, message):
            replies = exchange.sudo().reply_ids
            with trap_jobs() as trap:
                with self.assertRaisesRegex(UserError, message):
                    exchange.with_user(self.administrator).action_send_new_answer()
                trap.assert_jobs_count(0)
            self.assertEqual(exchange.sudo().reply_ids, replies, message)

        fine = self._answered(question="Is it in Belgium?")
        assert_refused(fine, "^Only an answer with a thumbs-down, not covered or not answered gets a new answer.$")
        self.assertFalse(fine.review_state)

        pressed_twice = self._flagged("Twice?")
        self._send_new_answer(pressed_twice)
        reviewed_date = pressed_twice.reviewed_date
        assert_refused(pressed_twice, "^A new answer to this question is already being written.$")
        self.assertEqual(pressed_twice.reviewed_date, reviewed_date)

        answered_again = self._flagged("Again?")
        first_reply, trap = self._send_new_answer(answered_again)
        trap.perform_enqueued_jobs()
        self._send_new_answer(first_reply)
        assert_refused(answered_again, "^A new answer to this question is already being written.$")

        elsewhere = self._flagged("Elsewhere?", user=self.colleague, company=self.other_company)
        self.colleague.write({"company_ids": [Command.unlink(self.other_company.id)]})
        assert_refused(elsewhere, "Colin Colleague no longer has any of the companies the question was asked in")

        archived = self._flagged("Archived?", user=self.colleague)
        self.colleague.action_archive()
        assert_refused(archived, "^No new answer was sent: Colin Colleague is archived.$")

        hr_record = self.env["hr.employee"].create({"name": "AI Asked Employee"})
        on_hr_record = self._answered(user=self.hr_officer, record=hr_record)
        on_hr_record.sudo().feedback = "down"
        self.env.ref("hr.group_hr_user").write({"users": [Command.unlink(self.hr_officer.id)]})
        assert_refused(on_hr_record, "may no longer Ask AI on this record")

        left_the_group = self._flagged("Left?")
        self.env.ref(ASSISTANT_GROUP).write({"users": [Command.unlink(self.employee.id)]})
        assert_refused(left_the_group, "Erin Employee no longer has access to the AI Assistant")
        self.assertEqual((elsewhere | archived | on_hr_record | left_the_group).mapped("review_state"), [False] * 4)

    def test_send_new_answer_locks_the_question_asked_before_it_looks_for_a_pending_new_answer(self):
        original = self._flagged()
        first_reply, trap = self._send_new_answer(original)
        trap.perform_enqueued_jobs()
        statements = []
        cursor_cls = type(self.env.cr)
        execute = cursor_cls.execute

        def recording_execute(cr, query, params=None, log_exceptions=True):
            statements.append((str(query), params))
            return execute(cr, query, params, log_exceptions)

        with patch.object(cursor_cls, "execute", recording_execute):
            self._send_new_answer(first_reply)

        [lock_at] = [
            index
            for index, (query, params) in enumerate(statements)
            if query.startswith("SELECT id FROM ai_assistant_exchange") and query.endswith("FOR UPDATE")
        ]
        [pending_at] = [index for index, (query, _params) in enumerate(statements) if "WITH RECURSIVE chain" in query]
        self.assertEqual(statements[lock_at][1], (original.id,), "the question asked, not the answer pressed")
        self.assertLess(lock_at, pending_at)

    def test_a_new_answer_is_queued_as_its_asker_and_the_administrator_is_told(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.employee.write({"lang": "fr_FR", "tz": "Europe/Brussels"})
        self.administrator.write({"tz": "America/Sao_Paulo"})
        original = self._flagged(company=self.other_company, companies=self.company | self.other_company)

        with trap_jobs() as trap:
            action = (
                original.with_user(self.administrator)
                .with_context(allowed_company_ids=self.company.ids)
                .action_send_new_answer()
            )

        [job] = trap.enqueued_jobs
        job_env = job.recordset.env
        self.assertEqual(
            (job_env.uid, job_env.su, job.company_id),
            (self.employee.id, True, self.other_company.id),
        )
        self.assertEqual(
            {key: job_env.context.get(key) for key in ("lang", "tz", "allowed_company_ids")},
            {"lang": "fr_FR", "tz": "Europe/Brussels", "allowed_company_ids": [self.other_company.id, self.company.id]},
        )
        self.assertEqual(
            action,
            {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "success",
                    "message": "New answer queued: Erin Employee is notified once the AI Assistant has answered.",
                    "next": {"type": "ir.actions.act_window_close"},
                },
            },
        )

    def test_a_new_answer_refused_when_its_job_runs_notifies_nobody(self):
        ask_ai_original = self._answered(answer="A wrong answer.")
        conversation, app_original = self._app_answered(answer="A wrong answer.")
        (ask_ai_original | app_original).sudo().write({"feedback": "down"})
        ask_ai_reply, ask_ai_trap = self._send_new_answer(ask_ai_original)
        app_reply, app_trap = self._send_new_answer(app_original)
        self.env.ref(ASSISTANT_GROUP).write({"users": [Command.unlink(self.employee.id)]})

        ask_ai_trap.perform_enqueued_jobs()
        app_trap.perform_enqueued_jobs()

        for reply in (ask_ai_reply, app_reply):
            self.assertEqual((reply.state, reply.is_refused, reply.to_review), ("unavailable", True, True), reply.kind)
        self.assertFalse(self._bus_payloads(self.employee.partner_id, "simple_notification"), "no sticky warning")
        self.assertFalse(ask_ai_reply.answer_message_id, "nothing on the record")
        self.assertFalse(self._bot_posts(conversation), "no notification of the app's conversation")
        app_payloads = self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)
        self.assertNotIn(
            app_reply.id,
            [payload["exchange_id"] for payload in app_payloads],
            "an open app shows no refusal under a question its asker never asked again",
        )

    def _stuck_new_answer(self, keeps_assistant):
        """An app new answer closed by `_close_stuck` (stale, failed job, conflicts), its asker keeping the
        AI Assistant or not; the exchange ids the asker's open app was sent."""
        conversation, original = self._app_answered(answer="A wrong answer.")
        original.sudo().write({"feedback": "down"})
        reply, _trap = self._send_new_answer(original)
        if not keeps_assistant:
            self.env.ref(ASSISTANT_GROUP).write({"users": [Command.unlink(self.employee.id)]})
        reply._close_stuck(error="no answer within 1800 s")
        self.assertFalse(self._bot_posts(conversation), "no notification of the app's conversation")
        app_payloads = self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)
        return reply, [payload["exchange_id"] for payload in app_payloads]

    def test_a_refused_new_answer_closed_as_stuck_notifies_nobody(self):
        reply, shown_ids = self._stuck_new_answer(keeps_assistant=False)
        self.assertEqual((reply.state, reply.is_refused, reply.to_review), ("unavailable", True, True))
        self.assertNotIn(reply.id, shown_ids, "an open app shows no refusal under a question its asker never asked")

    def test_a_new_answer_closed_as_stuck_shows_in_the_open_app(self):
        reply, shown_ids = self._stuck_new_answer(keeps_assistant=True)
        self.assertEqual((reply.state, reply.is_refused, reply.to_review), ("unavailable", False, True))
        self.assertIn(reply.id, shown_ids, "the open app re-enables its composer on the not-available text")

    def test_later_questions_carry_only_the_new_answer_that_replaced_an_answer(self):
        conversation, original = self._app_answered(answer="A wrong answer.")
        conversation.with_user(self.employee).app_feedback(original.id, "down")
        first_reply, trap = self._send_new_answer(original)
        with self._claude(claude_turn([text("A better answer.")], "end_turn")):
            trap.perform_enqueued_jobs()
        conversation.with_user(self.employee).app_feedback(first_reply.id, "down")
        second_reply, trap = self._send_new_answer(first_reply)
        with self._claude(claude_turn([text("The right answer.")], "end_turn")):
            trap.perform_enqueued_jobs()
        self.assertEqual((first_reply.state, second_reply.state), ("answered", "answered"))

        _conversation, later, _trap = self._app_ask("And its capital?", conversation=conversation)

        self.assertEqual(
            later._history(),
            [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "The right answer."}],
        )

    def test_a_new_answer_to_a_follow_up_carries_the_new_answer_to_the_question_before_it(self):
        conversation, first = self._app_answered(answer="A wrong answer.")
        _same, follow_up = self._app_answered(
            question="And its capital?", answer="Another wrong answer.", conversation=conversation
        )
        for exchange in (first, follow_up):
            conversation.with_user(self.employee).app_feedback(exchange.id, "down")
        first_reply, trap = self._send_new_answer(first)
        with self._claude(claude_turn([text("The right answer.")], "end_turn")):
            trap.perform_enqueued_jobs()

        follow_up_reply, _trap = self._send_new_answer(follow_up)

        self.assertEqual(
            follow_up_reply._history(),
            [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "The right answer."}],
            "the question asked before the follow-up, with the new answer sent after the follow-up was asked",
        )

    def test_a_question_asked_before_a_new_answer_carries_it(self):
        conversation, original = self._app_answered(answer="A wrong answer.")
        original.sudo().feedback = "down"
        _same, waiting, _trap = self._app_ask("And its capital?", conversation=conversation)
        reply, trap = self._send_new_answer(original)
        with self._claude(claude_turn([text("The right answer.")], "end_turn")):
            trap.perform_enqueued_jobs()

        self.assertEqual(
            waiting._history(),
            [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "The right answer."}],
        )
        self.assertEqual(reply._history(), [], "nothing was asked before the question it answers again")

    def test_a_question_carries_the_latest_new_answer_that_answered(self):
        conversation, original = self._app_answered(answer="A wrong answer.")
        conversation.with_user(self.employee).app_feedback(original.id, "down")
        _same, waiting, _trap = self._app_ask("And its capital?", conversation=conversation)
        first_reply, trap = self._send_new_answer(original)
        with self._claude(claude_turn([text("A better answer.")], "end_turn")):
            trap.perform_enqueued_jobs()
        conversation.with_user(self.employee).app_feedback(first_reply.id, "down")
        second_reply, trap = self._send_new_answer(first_reply)
        with self._claude(claude_turn([text("The right answer.")], "end_turn")):
            trap.perform_enqueued_jobs()
        conversation.with_user(self.employee).app_feedback(second_reply.id, "down")
        third_reply, _trap = self._send_new_answer(second_reply)

        self.assertEqual(third_reply.state, "pending")
        self.assertEqual(
            waiting._history(),
            [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "The right answer."}],
        )

    def test_a_later_question_on_a_record_carries_a_new_answer_while_its_note_is_in_the_chatter(self):
        original = self._answered(answer="A wrong answer.")
        original.sudo().feedback = "down"
        later, _trap = self._ask("And its capital?")
        reply, trap = self._send_new_answer(original)
        with self._claude(claude_turn([text("The right answer.")], "end_turn")):
            trap.perform_enqueued_jobs()
        self.assertEqual(
            later._history(),
            [{"role": "user", "content": QUESTION}, {"role": "assistant", "content": "The right answer."}],
        )

        reply.answer_message_id.unlink()

        self.assertEqual(later._history(), [], "the answer it replaced does not come back")

    def test_the_transcript_shows_a_new_answer_under_the_question_it_answers_again(self):
        conversation, original = self._app_answered(answer="A wrong answer.")
        original.sudo().feedback = "down"
        reply, trap = self._send_new_answer(original)
        with self._claude(claude_turn([text("The right answer.")], "end_turn")):
            trap.perform_enqueued_jobs()
        self.env.invalidate_all()

        transcript = conversation.transcript
        reply_line = "New answer to the question of %s" % format_datetime(self.env, original.create_date)
        self.assertEqual(transcript.count(QUESTION), 1, "the question shows once, as its asker asked it")
        self.assertLess(transcript.index("A wrong answer."), transcript.index(reply_line))
        self.assertLess(transcript.index(reply_line), transcript.index("The right answer."))

    def test_a_long_question_is_quoted_by_its_start(self):
        long_question = "Where " + "was this company founded " * 6
        reply, _trap = self._send_new_answer(self._flagged(long_question))
        self.assertEqual(reply._app_message()["reply_to"]["question"], question_quote(long_question))


@tagged("post_install", "-at_install")
class TestAiAssistantConversationAccess(ReviewCase):
    def test_the_asker_opens_their_conversation_in_the_app_and_an_administrator_its_form(self):
        conversation, _exchange = self._app_answered()
        menu = self.env.ref(f"{APP}.ai_assistant_app_menu")
        app_url = (
            f"{conversation.get_base_url()}/web#action=ai_assistant.app&conversation_id={conversation.id}"
            f"&menu_id={menu.id}"
        )
        self.assertEqual(
            conversation.sudo()._get_access_action(access_uid=self.employee.id),
            {"type": "ir.actions.act_url", "url": app_url, "target": "self"},
            "a mail's View button",
        )
        self.assertEqual(
            conversation.with_user(self.employee).app_open_action(),
            {
                "type": "ir.actions.client",
                "tag": "ai_assistant.app",
                "name": "AI Assistant",
                "params": {"conversation_id": conversation.id},
            },
            "the Inbox's link",
        )
        ask_ai_conversation = self._answered().conversation_id
        for other_conversation in (conversation, ask_ai_conversation):
            with self.subTest(assistant=other_conversation.assistant):
                access_action = other_conversation.sudo()._get_access_action(access_uid=self.administrator.id)
                self.assertEqual(
                    (access_action["type"], access_action["res_model"], access_action["res_id"]),
                    ("ir.actions.act_window", "ai.conversation", other_conversation.id),
                )
                self.assertFalse(other_conversation.with_user(self.administrator).app_open_action())

    def test_the_inbox_s_link_to_a_deleted_conversation_does_not_open_the_app(self):
        conversation, _exchange = self._app_answered()
        conversation_id = conversation.id
        conversation.with_user(self.employee).unlink()
        self.assertFalse(self.env["ai.conversation"].with_user(self.employee).browse(conversation_id).app_open_action())

    def test_the_owner_reads_the_conversation_form_without_the_administrators_logs(self):
        conversation, _exchange = self._app_answered()
        administrators_only = {"transcript", "assistant_exchange_ids", "cost"}
        for user in (self.employee, self.administrator):
            with self.subTest(user=user.login):
                conversations = self.env["ai.conversation"].with_user(user)
                arch = etree.fromstring(conversations.get_views([(False, "form")])["views"]["form"]["arch"])
                names = {
                    node.get("name")
                    for node in arch.iter("field")
                    if not any(ancestor.tag == "field" for ancestor in node.iterancestors())
                }
                if user == self.administrator:
                    self.assertLessEqual(administrators_only, names)
                else:
                    self.assertFalse(administrators_only & names)
                self.env.invalidate_all()
                conversations.browse(conversation.id).read(sorted(names))
