from unittest.mock import patch

import requests
from markupsafe import Markup
from psycopg2 import errors as pg_errors

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import plaintext2html

from odoo.addons.ai_assistant.models.ai_assistant_engine import MODE_PROMPTS
from odoo.addons.ai_assistant.models.ai_assistant_exchange import (
    APP_ANSWER_NOTIFICATION,
    ASSISTANT_GROUP,
    CONFLICT_ERROR,
    FINISH_TRIES,
    HISTORY_EXCHANGES,
)
from odoo.addons.ai_assistant.models.ai_conversation import APP_TITLE_CHARS
from odoo.addons.ai_claude.models.ai_claude_gateway import ClaudeOutcome, answer_to_html
from odoo.addons.queue_job.tests.common import trap_jobs

from .test_ai_assistant_engine import api_timeout, claude_turn, text, tool_use
from .test_ask_ai import ANSWER, QUESTION, AskAiCase

AI_CONVERSATION_MODULE = "odoo.addons.ai_assistant.models.ai_conversation"
UNAVAILABLE = "The AI Assistant is not available right now"
REFUSED = "This question was not answered: you no longer have access to the AI Assistant."


class AppCase(AskAiCase):
    def _app_ask(self, question=QUESTION, user=None, conversation=None, screen=None):
        """(conversation, exchange, trap) of one question in the app, its answer job trapped."""
        with trap_jobs() as trap:
            result = (
                self.env["ai.conversation"]
                .with_user(user or self.employee)
                .app_ask(
                    question,
                    conversation_id=conversation.id if conversation else False,
                    screen=screen,
                    allowed_company_ids=self.company.ids,
                )
            )
        self.env.flush_all()
        return (
            self.env["ai.conversation"].browse(result["conversation"]["id"]),
            self.env["ai.assistant.exchange"].browse(result["message"]["id"]),
            trap,
        )

    def _app_answered(self, answer=ANSWER, **ask_options):
        conversation, exchange, trap = self._app_ask(**ask_options)
        with self._claude(claude_turn([text(answer)], "end_turn")):
            trap.perform_enqueued_jobs()
        return conversation, exchange

    def _partner_screen(self, partner=None):
        return {"res_model": "res.partner", "res_id": (partner or self.partner).id}

    def _app_payload(self, conversation, exchange):
        return {"conversation_id": conversation.id, "exchange_id": exchange.id, "message": exchange._app_message()}


@tagged("post_install", "-at_install")
class TestAiAssistantApp(AppCase):
    def test_app_ask_starts_a_private_conversation_and_queues_the_answer(self):
        conversation, exchange, trap = self._app_ask()
        self.assertEqual(
            (conversation.assistant, conversation.user_id, conversation.name, conversation.res_model),
            ("app", self.employee, QUESTION, False),
        )
        self.assertEqual(
            (exchange.kind, exchange.user_id, exchange.question, exchange.state, exchange.conversation_id),
            ("app", self.employee, QUESTION, "pending", conversation),
        )
        self.assertEqual(exchange.allowed_company_ids, self.company)
        self.assertFalse(exchange.question_message_id, "the app posts no note on any record")
        trap.assert_jobs_count(1)
        trap.assert_enqueued_job(exchange._app_answer, properties={"identity_key": exchange._answer_job_key()})
        self.assertEqual(trap.enqueued_jobs[0].channel, "root.ai_assistant")
        self.assertEqual(
            exchange._app_message(),
            {
                "id": exchange.id,
                "question": QUESTION,
                "answer_html": False,
                "state": "pending",
                "is_refused": False,
                "feedback": False,
                "reports": [],
                "reply_to": False,
            },
        )
        self.assertEqual(
            conversation._app_summary(),
            {
                "id": conversation.id,
                "name": QUESTION,
                "date_last": exchange.create_date,
                "res_model": False,
                "res_id": False,
            },
        )

        same_conversation, follow_up, _trap = self._app_ask("And in France?", conversation=conversation)
        self.assertEqual(same_conversation, conversation, "a question in a conversation continues it")
        self.assertEqual(conversation.name, QUESTION, "the title stays the first question")
        self.assertEqual(conversation.question_count, 2)
        self.assertEqual(conversation._conversation_exchanges(), exchange | follow_up)

        with trap_jobs() as trap:
            direct = self.env["ai.assistant.exchange"].with_user(self.employee)._app_enqueue(conversation, "Direct?")
        trap.assert_jobs_count(1)
        self.assertEqual(
            (direct.kind, direct.user_id, direct.conversation_id, direct.allowed_company_ids),
            ("app", self.employee, conversation, self.employee.company_id),
            "without companies from the client, the employee's main company",
        )

    def test_app_ask_refuses_what_it_cannot_take(self):
        Conversation = self.env["ai.conversation"].with_user(self.employee)
        with self.assertRaisesRegex(UserError, "Write a question"), trap_jobs() as trap:
            Conversation.app_ask("   ")
        trap.assert_jobs_count(0)
        self.assertEqual(self.env["ai.assistant.exchange"]._clean_question("  Why?  "), "Why?")
        self._leave_group(ASSISTANT_GROUP, self.colleague)
        with self.assertRaises(AccessError):
            self.env["ai.conversation"].with_user(self.colleague).app_ask(QUESTION)
        self.assertFalse(self.env["ai.conversation"].sudo().search([("user_id", "=", self.colleague.id)]))

    def test_a_long_first_question_is_shortened_into_the_title(self):
        conversation, _exchange, _trap = self._app_ask("Which    orders " + "are late " * 20)
        self.assertLessEqual(len(conversation.name), APP_TITLE_CHARS)
        self.assertTrue(conversation.name.startswith("Which orders are late"))
        self.assertTrue(conversation.name.endswith("..."))
        empty = self.env["ai.conversation"].sudo().create({"assistant": "app", "user_id": self.employee.id})
        self.assertEqual(empty.name, "New chat")
        conversation.assistant_exchange_ids.sudo().question = "Reworded"
        conversation._compute_name()
        self.assertEqual(conversation.name, "Reworded")

    def test_the_job_answers_in_app_mode_and_tells_the_asker_over_the_bus(self):
        ask_ai_notes = self.env["mail.message"].search_count([("subtype_id", "=", self.ask_ai_subtype.id)])
        conversation, exchange, trap = self._app_ask(screen=self._partner_screen())
        with self._claude(claude_turn([text(ANSWER)], "end_turn")) as claude:
            trap.perform_enqueued_jobs()

        request = claude.create.call_args.kwargs
        self.assertTrue(request["system"][0]["text"].endswith(MODE_PROMPTS["app"]), "app mode")
        self.assertIn(f"Record: Contact (res.partner) id {self.partner.id}", request["messages"][-1]["content"])
        self.assertEqual(
            (exchange.state, exchange.answer, exchange.provider, exchange.model, exchange.feedback),
            ("answered", ANSWER, "claude", "claude-sonnet-5-5", False),
        )
        self.assertEqual(exchange._answer_html(), answer_to_html(ANSWER))
        self.assertEqual(exchange._app_message()["answer_html"], answer_to_html(ANSWER))
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION),
            [self._app_payload(conversation, exchange)],
            "the answer travels with the notification",
        )
        self.assertEqual(
            self.env["mail.message"].search_count([("subtype_id", "=", self.ask_ai_subtype.id)]),
            ask_ai_notes,
            "the app posts no Ask AI note",
        )
        self.assertFalse(conversation.message_ids.filtered("body"), "a plain answer posts no deviation")
        self.assertEqual(conversation.cost, exchange.cost)

    def test_a_duplicate_run_never_answers_twice(self):
        _conversation, exchange = self._app_answered()
        with self._claude() as claude:
            exchange._app_answer()
            exchange._app_finish(ClaudeOutcome(answer="A second answer.", model="claude-sonnet-5-5"))
        claude.factory.assert_not_called()
        self.assertEqual(exchange.answer, ANSWER)

    def test_the_history_is_the_conversation_s_last_ten_answered_turns(self):
        conversation, _first, _trap = self._app_ask("Question 0")
        other_conversation, _other, _trap = self._app_ask("Elsewhere")
        Exchange = self.env["ai.assistant.exchange"].sudo()
        common = {"kind": "app", "user_id": self.employee.id, "state": "answered"}
        Exchange.create(
            [
                {
                    **common,
                    "conversation_id": conversation.id,
                    "question": f"Question {index}",
                    "answer": f"Answer {index}",
                }
                for index in range(1, HISTORY_EXCHANGES + 2)
            ]
            + [
                {**common, "conversation_id": conversation.id, "question": "Unanswered", "state": "unavailable"},
                {**common, "conversation_id": other_conversation.id, "question": "Other", "answer": "x"},
            ]
        )
        _same, exchange, trap = self._app_ask("Question now", conversation=conversation)
        expected = [
            turn
            for index in range(2, HISTORY_EXCHANGES + 2)
            for turn in (
                {"role": "user", "content": f"Question {index}"},
                {"role": "assistant", "content": f"Answer {index}"},
            )
        ]
        self.assertEqual(exchange._history(), expected, "oldest first, this conversation only, answered only")
        with self._claude(claude_turn([text("Done.")], "end_turn")) as claude:
            trap.perform_enqueued_jobs()
        messages = claude.create.call_args.kwargs["messages"]
        self.assertEqual(messages[:-1], expected)
        self.assertTrue(messages[-1]["content"].endswith("Question now"))

    def test_the_panel_continues_the_latest_conversation_only_on_its_screen(self):
        Conversation = self.env["ai.conversation"].with_user(self.employee)
        other_partner = self.env["res.partner"].create({"name": "AI Assistant Other Acme"})
        acme = self._partner_screen()
        self.assertEqual(
            Conversation.app_panel(acme), {"conversation": False, "screen_name": "Contact: AI Assistant Acme"}
        )

        on_acme, exchange, _trap = self._app_ask(screen=acme)
        self.assertEqual((on_acme.res_model, on_acme.res_id), ("res.partner", self.partner.id))
        self.assertEqual((exchange.res_model, exchange.res_id), ("res.partner", self.partner.id))
        self.assertEqual(Conversation.app_panel(acme)["conversation"], on_acme._app_summary())
        self.assertFalse(Conversation.app_panel(self._partner_screen(other_partner))["conversation"])
        self.assertFalse(
            Conversation.app_panel({"res_model": "res.partner"})["conversation"], "the list is not the record"
        )
        self.assertFalse(Conversation.app_panel(None)["conversation"])

        on_other, _exchange, _trap = self._app_ask(screen=self._partner_screen(other_partner))
        self.assertNotEqual(on_other, on_acme)
        self.assertFalse(Conversation.app_panel(acme)["conversation"], "only the latest conversation continues")
        self.assertEqual(Conversation.app_panel(self._partner_screen(other_partner))["conversation"]["id"], on_other.id)

        _same, from_full_page, _trap = self._app_ask("And its city?", conversation=on_acme)
        # One transaction gives every row the same create_date: make the new question the latest activity.
        self.env.cr.execute(
            "UPDATE ai_conversation SET date_last = date_last - interval '1 minute' WHERE id = %s", (on_other.id,)
        )
        self.env.invalidate_all()
        self.assertEqual(
            (from_full_page.res_model, from_full_page.res_id),
            ("res.partner", self.partner.id),
            "the full page keeps the conversation's screen",
        )
        self.assertEqual(Conversation.app_panel(acme)["conversation"]["id"], on_acme.id)
        self.assertFalse(
            self.env["ai.conversation"].with_user(self.colleague).app_panel(acme)["conversation"],
            "nobody continues another employee's conversation",
        )

    def test_the_screen_is_cleaned_and_named_as_the_employee_sees_it(self):
        Conversation = self.env["ai.conversation"].with_user(self.employee)
        self.assertEqual(Conversation._app_screen({"res_model": "res.partner", "res_id": 7}), ("res.partner", 7))
        self.assertEqual(Conversation._app_screen({"res_model": "res.partner", "res_id": "7"}), ("res.partner", False))
        self.assertEqual(Conversation._app_screen({"res_model": "no.such.model", "res_id": 7}), (False, False))
        self.assertEqual(Conversation._app_screen(None), (False, False))
        self.assertEqual(Conversation._app_screen_name("res.partner", False), "Contact")
        self.assertFalse(Conversation._app_screen_name(False, False))
        hidden = self.env["res.partner"].create({"name": "AI Assistant Hidden Contact"})
        self.env["ir.access"].create(
            {
                "name": "AI test: hide one contact",
                "model_id": self.env["ir.model"]._get_id("res.partner"),
                "operation": "crud",
                "domain": "[('id', '!=', %s)]" % hidden.id,
            }
        )
        self.env.invalidate_all()
        self.assertEqual(
            Conversation._app_screen_name("res.partner", hidden.id), "Contact", "a record they may not read"
        )
        self.assertEqual(Conversation._app_screen_name("res.partner", 0), "Contact")

    def test_conversations_are_listed_newest_first_and_only_the_employee_s_own(self):
        first, _exchange, _trap = self._app_ask("First")
        second, _exchange, _trap = self._app_ask("Second")
        self._app_ask("The colleague's", user=self.colleague)
        self.env["ai.conversation"]._for_ask_ai("res.partner", self.partner.id)
        listed = self.env["ai.conversation"].with_user(self.employee).app_conversations()
        self.assertEqual([row["id"] for row in listed], [second.id, first.id])
        self.assertFalse(
            self.env["ai.conversation"].with_user(self.administrator).app_conversations(),
            "the app lists an administrator's own conversations only",
        )

    def test_the_list_ends_on_a_linked_conversation_of_the_employee_too_old_to_be_in_it(self):
        older, _exchange, _trap = self._app_ask("Older")
        newer, _exchange, _trap = self._app_ask("Newer")
        colleagues, _exchange, _trap = self._app_ask("The colleague's", user=self.colleague)
        gone, _exchange, _trap = self._app_ask("Gone")
        gone_id = gone.id
        gone.unlink()
        Conversation = self.env["ai.conversation"].with_user(self.employee)
        with patch(f"{AI_CONVERSATION_MODULE}.APP_LIST_LIMIT", 1):
            for conversation_id, expected in (
                (False, newer),
                (newer.id, newer),
                (older.id, newer | older),
                (colleagues.id, newer),
                (gone_id, newer),
            ):
                listed = Conversation.app_conversations(conversation_id=conversation_id)
                self.assertEqual([row["id"] for row in listed], expected.ids, conversation_id)

    def test_conversations_are_private_to_their_owner(self):
        conversation, exchange = self._app_answered()
        Conversation = self.env["ai.conversation"]
        ask_ai_conversation = Conversation._for_ask_ai("res.partner", self.partner.id)
        everything = conversation | ask_ai_conversation

        self.assertEqual(Conversation.with_user(self.employee).search([("id", "in", everything.ids)]), conversation)
        self.assertEqual(conversation.with_user(self.employee).app_messages(), [exchange._app_message()])

        as_colleague = conversation.with_user(self.colleague)
        self.assertFalse(Conversation.with_user(self.colleague).search([("id", "in", everything.ids)]))
        for name, attempt in (
            ("read", lambda: as_colleague.read(["name"])),
            ("write", lambda: as_colleague.write({"res_model": "res.partner"})),
            ("unlink", as_colleague.unlink),
            ("app_messages", as_colleague.app_messages),
            ("app_feedback", lambda: as_colleague.app_feedback(exchange.id, "down")),
            ("app_ask", lambda: Conversation.with_user(self.colleague).app_ask("Mine?", conversation.id)),
            ("exchange read", lambda: exchange.with_user(self.colleague).read(["question"])),
        ):
            with self.subTest(name), self.assertRaises(AccessError):
                attempt()
        self.assertTrue(conversation.exists())
        self.assertEqual(exchange.feedback, False)
        with self.assertRaises(AccessError):
            exchange.with_user(self.employee).read(["question"])
        self.assertFalse(
            self.env["mail.message"]
            .with_user(self.colleague)
            .search([("model", "=", "ai.conversation"), ("res_id", "=", conversation.id)])
        )
        with self.assertRaises(AccessError):
            ask_ai_conversation.with_user(self.employee).read(["name"])

        with self.assertRaises(AccessError):
            conversation.with_user(self.colleague)._app_check(owner=False)
        conversation.with_user(self.employee)._app_check(owner=True)
        conversation.with_user(self.administrator)._app_check(owner=False)
        with self.assertRaises(AccessError):
            conversation.with_user(self.administrator)._app_check(owner=True)

        as_administrator = conversation.with_user(self.administrator)
        self.assertEqual(Conversation.with_user(self.administrator).search([("id", "in", everything.ids)]), everything)
        self.assertEqual(as_administrator.app_messages(), [exchange._app_message()], "administrators review")
        with self.assertRaises(AccessError):
            as_administrator.app_feedback(exchange.id, "down")
        with self.assertRaises(AccessError), trap_jobs():
            Conversation.with_user(self.administrator).app_ask("Continue?", conversation.id)
        with self.assertRaises(AccessError):
            ask_ai_conversation.with_user(self.administrator).app_messages()

    def test_the_owner_deletes_a_conversation_with_its_questions_and_answers(self):
        conversation, exchange = self._app_answered()
        tool_call = (
            self.env["ai.assistant.tool.call"]
            .sudo()
            .create(
                {
                    "user_id": self.employee.id,
                    "conversation_id": conversation.id,
                    "exchange_id": exchange.id,
                    "tool": "fields",
                    "outcome": "ok",
                }
            )
        )
        ask_ai_exchange = self._answered()
        conversation.with_user(self.employee).unlink()
        self.assertFalse(conversation.exists())
        self.assertFalse(exchange.exists())
        self.assertTrue(tool_call.exists(), "the administrators' audit of what was read stays")
        self.assertEqual(
            (tool_call.conversation_id, tool_call.exchange_id, tool_call.tool),
            (conversation.browse(), exchange.browse(), "fields"),
        )
        self.assertTrue(ask_ai_exchange.exists() and ask_ai_exchange.conversation_id.exists())

        admin_deleted = ask_ai_exchange.conversation_id
        admin_deleted.with_user(self.administrator).unlink()
        self.assertTrue(ask_ai_exchange.exists(), "Ask AI exchanges stay with their notes on the record")

    def test_a_conversation_deleted_before_its_answer_is_never_answered(self):
        conversation, exchange, trap = self._app_ask()
        conversation.with_user(self.employee).unlink()
        with self._claude() as claude:
            trap.perform_enqueued_jobs()
        claude.factory.assert_not_called()
        self.assertFalse(exchange.exists())

    def test_an_employee_who_left_the_group_is_not_answered_and_their_app_shows_it(self):
        conversation, exchange, trap = self._app_ask()
        self._leave_group(ASSISTANT_GROUP, self.employee)
        with self._claude() as claude:
            trap.perform_enqueued_jobs()
        claude.factory.assert_not_called()
        self.assertEqual((exchange.state, exchange.is_refused), ("unavailable", True))
        self.assertIn("may not use the AI Assistant", exchange.error)
        [payload] = self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)
        self.assertEqual(payload, self._app_payload(conversation, exchange), "the composer is enabled again")
        self.assertEqual((payload["message"]["state"], payload["message"]["is_refused"]), ("unavailable", True))
        self.assertEqual(payload["message"]["answer_html"], plaintext2html(REFUSED), "told why, not to try again")
        with self.assertRaises(AccessError, msg="why the answer travels: the app could not reload it"):
            conversation.with_user(self.employee).app_messages()

    def test_an_answer_that_keeps_conflicting_closes_the_row_and_tells_the_app(self):
        conversation, exchange, trap = self._app_ask()
        exchange_cls = type(exchange)
        with (
            patch.object(exchange_cls, "_app_finish", side_effect=pg_errors.SerializationFailure()) as finish,
            self._claude(claude_turn([text(ANSWER)], "end_turn")) as claude,
        ):
            trap.perform_enqueued_jobs()
        self.assertEqual((finish.call_count, claude.create.call_count), (FINISH_TRIES, 1))
        self.assertEqual(
            (exchange.state, exchange.is_refused, exchange.error), ("unavailable", False, CONFLICT_ERROR % FINISH_TRIES)
        )
        [payload] = self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)
        self.assertEqual(payload, self._app_payload(conversation, exchange))
        self.assertIn(UNAVAILABLE, payload["message"]["answer_html"])

    def test_feedback_comes_from_the_asker_only(self):
        conversation, exchange = self._app_answered()
        as_owner = conversation.with_user(self.employee)
        self.assertEqual(as_owner.app_feedback(exchange.id, "up")["feedback"], "up")
        self.assertEqual(exchange.feedback, "up")
        self.assertEqual(as_owner.app_feedback(exchange.id, "down")["feedback"], "down")
        self.assertFalse(as_owner.app_feedback(exchange.id, "down")["feedback"], "the same rating again takes it back")
        with self.assertRaisesRegex(UserError, "up or down"):
            as_owner.app_feedback(exchange.id, "meh")
        with self.assertRaises(AccessError):
            as_owner.app_feedback(exchange.id + 1000, "up")
        _same, pending, _trap = self._app_ask("Still there?", conversation=conversation)
        with self.assertRaisesRegex(UserError, "Only an answer"):
            as_owner.app_feedback(pending.id, "up")

    def test_without_claude_the_answer_says_unavailable_and_no_other_model_is_asked(self):
        conversation, exchange, trap = self._app_ask()
        with self._claude(api_timeout(), api_timeout()) as claude, patch.object(requests, "post") as other_model:
            trap.perform_enqueued_jobs()
        self.assertEqual(claude.create.call_count, 2)
        other_model.assert_not_called()
        self.assertEqual((exchange.state, exchange.error), ("unavailable", "no Claude model answered"))
        self.assertIn(UNAVAILABLE, exchange._app_message()["answer_html"])
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION),
            [self._app_payload(conversation, exchange)],
        )
        self.assertIn("no other model answers, so nobody answered", conversation.message_ids.filtered("body").body)

        _same, unconfigured, trap = self._app_ask("And now?", conversation=conversation)
        trap.perform_enqueued_jobs()
        self.assertEqual((unconfigured.state, unconfigured.error), ("unavailable", "Claude is not configured"))

    def test_a_question_that_needs_too_many_lookups_is_told_to_narrow_it(self):
        self._set_param("claude_max_tool_rounds", "1")
        _conversation, exchange, trap = self._app_ask()
        rounds = [
            claude_turn([tool_use("fields", {"model": "res.partner"}, "toolu_%s" % index)], "tool_use")
            for index in range(2)
        ]
        with self._claude(*rounds) as claude:
            trap.perform_enqueued_jobs()
        self.assertEqual(claude.create.call_count, 2, "Haiku would meet the same limit: it is not asked")
        self.assertEqual(exchange.state, "unavailable")
        self.assertEqual(
            exchange.error, "tool round or time limit reached: the model asked for more than 1 rounds of tools"
        )
        answer_html = exchange._app_message()["answer_html"]
        self.assertIn("The question needs too many lookups; narrow it (one record, a shorter period)", answer_html)
        self.assertNotIn(UNAVAILABLE, answer_html)
        self.assertEqual(
            exchange._digest_reasons(),
            ["not answered: tool round or time limit reached: the model asked for more than 1 rounds of tools"],
        )

    def test_outcome_values_and_answer_html_cover_every_state(self):
        Exchange = self.env["ai.assistant.exchange"]
        answered = Exchange._outcome_values(ClaudeOutcome(answer="**Yes.**", model="claude-sonnet-5-5"))
        self.assertEqual(
            (answered["state"], answered["answer"], answered["not_covered"]), ("answered", "**Yes.**", False)
        )
        unavailable = Exchange._outcome_values(ClaudeOutcome())
        self.assertEqual((unavailable["state"], unavailable["error"]), ("unavailable", "Claude is not configured"))
        _conversation, exchange, _trap = self._app_ask()
        self.assertFalse(exchange._answer_html(), "nothing while it is pending")
        exchange.sudo().write({"state": "answered", "answer": "<b>bold</b> **yes**"})
        self.assertEqual(exchange._answer_html(), Markup("<p>&lt;b&gt;bold&lt;/b&gt; <b>yes</b></p>"))
        exchange.sudo().state = "unavailable"
        self.assertEqual(
            exchange._answer_html(),
            plaintext2html("The AI Assistant is not available right now. Please try again in a few minutes."),
        )
        exchange.sudo().is_refused = True
        self.assertEqual(exchange._answer_html(), plaintext2html(REFUSED))

    def test_the_transcript_follows_a_tool_limit_or_a_refusal_written_after_it_was_read(self):
        conversation, exchange, _trap = self._app_ask()
        exchange.sudo().state = "unavailable"
        self.assertIn(UNAVAILABLE, conversation.transcript)
        exchange.sudo().attempts = "claude-sonnet-5-5: tool_limit, 10 ms, 12 tool calls - more than 12 rounds"
        self.assertIn("The question needs too many lookups", conversation.transcript)
        exchange.sudo().is_refused = True
        self.assertIn(REFUSED, conversation.transcript)

    def test_deviations_are_posted_on_the_conversation_for_the_administrators(self):
        conversation, exchange, _trap = self._app_ask()
        exchange._post_outcome_deviations(ClaudeOutcome())
        self.assertFalse(conversation.message_ids.filtered("body"), "nothing went off the normal path")
        with patch.object(type(self.env["ai.claude.gateway"]), "_claude_deviations", return_value=["A deviation"]):
            exchange._post_outcome_deviations(ClaudeOutcome())
        self.assertIn("A deviation", conversation.message_ids.filtered("body").body)

    def test_administrators_review_the_transcript_with_the_other_assistants(self):
        conversation, exchange = self._app_answered()
        _same, pending, _trap = self._app_ask("A <b>tagged</b> question", conversation=conversation)
        transcript = conversation.transcript
        self.assertIn(QUESTION, transcript)
        self.assertIn("<b>Belgium</b>", transcript)
        self.assertIn("Erin Employee", transcript)
        self.assertIn("AI Assistant", transcript)
        self.assertIn("A &lt;b&gt;tagged&lt;/b&gt; question", transcript, "the question is escaped text")
        self.assertEqual(transcript.count("AI Assistant</b>"), 1, "no answer block while one is pending")
        self.assertEqual(conversation.question_count, 2)
        self.assertEqual(conversation.date_last, pending.create_date)
        self.assertIn(conversation, self.env["ai.conversation"].with_user(self.administrator).search([]))
        self.assertEqual(conversation._conversation_exchanges(), exchange | pending)
