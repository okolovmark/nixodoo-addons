import json
import os
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from lxml import html

from odoo import SUPERUSER_ID, Command, api
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.ai_assistant.models.ai_assistant_exchange import ASSISTANT_GROUP
from odoo.addons.bus.models.bus import dispatch
from odoo.addons.bus.websocket import WebsocketConnectionHandler
from odoo.addons.queue_job.tests.common import trap_jobs

from .test_ai_assistant_app import AI_CONVERSATION_MODULE
from .test_ai_assistant_engine import claude_turn, text
from .test_ai_assistant_tools import ASSISTANT_KEY_ENV

ANSWER_WAIT_SECONDS = 60
# Long enough for the page to render the app_ask response, the pending question, before the answer.
PENDING_RENDER_SECONDS = 1


@tagged("post_install", "-at_install")
class TestAiAssistantAppHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login="test_app_owner", name="Olga Owner")
        cls.other = new_test_user(cls.env, login="test_app_other", name="Otto Other")
        cls.administrator = new_test_user(cls.env, login="test_app_admin", groups="base.group_user,base.group_system")
        icp = cls.env["ir.config_parameter"].sudo()
        for key, value in {
            "claude_models": "claude-sonnet-5-5,claude-haiku-5-5",
            "claude_key_env": ASSISTANT_KEY_ENV,
            "claude_consecutive_failures": "0",
            "claude_circuit_open_until": "0",
        }.items():
            icp.set_param(f"ai_assistant.{key}", value)
        # The AI Assistant's partner has no address: its notification mails go from the catchall domain's.
        icp.set_param("mail.catchall.domain", "example.com")

    @contextmanager
    def _answered_while_the_page_is_open(self, *claude_results, before_the_job=None):
        """Trap the answer jobs and run the first one from a thread once the page has asked, as the job runner
        would, with Claude's client a test double; `before_the_job(env)` runs first in the job's transaction.

        The registry's test mode refuses websockets and drops the commit's notification to the bus: websockets are
        allowed here and the thread wakes them itself, as bus.bus's own websocket tests do. Yields the trap."""
        create = Mock(side_effect=list(claude_results))
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        errors = []

        def answer_first_job(trap):
            try:
                deadline = time.monotonic() + ANSWER_WAIT_SECONDS
                while not trap.enqueued_jobs and time.monotonic() < deadline:
                    time.sleep(0.1)
                if not trap.enqueued_jobs:
                    return
                # The test cursor's lock: taken once the request that asked has ended.
                with self.registry.cursor():
                    pass
                time.sleep(PENDING_RENDER_SECONDS)
                with self.registry.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    if before_the_job:
                        before_the_job(env)
                    env["ai.assistant.exchange"].browse(trap.enqueued_jobs[0].recordset.ids)._app_answer()
                for websockets in list(dispatch._channels_to_ws.values()):
                    for websocket in list(websockets):
                        websocket.trigger_notification_dispatching()
            except Exception as error:  # noqa: BLE001 -- reported by the test, not lost in the thread
                errors.append(error)

        with (
            trap_jobs() as trap,
            patch.object(WebsocketConnectionHandler, "websocket_allowed", return_value=True),
            patch.dict(os.environ, {ASSISTANT_KEY_ENV: "test-key"}),
            patch.object(type(self.env["ai.claude.gateway"]), "_claude_client", return_value=client),
        ):
            thread = threading.Thread(target=answer_first_job, args=(trap,), daemon=True)
            thread.start()
            try:
                yield trap
            finally:
                thread.join(ANSWER_WAIT_SECONDS)
        self.assertFalse(thread.is_alive(), "the answer job ended within %s seconds" % ANSWER_WAIT_SECONDS)
        self.assertFalse(errors, "the answer job ran without an error")

    def _rpc(self, model, method, args, kwargs=None):
        response = self.url_open(
            f"/web/dataset/call_kw/{model}/{method}",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {"model": model, "method": method, "args": args, "kwargs": kwargs or {}},
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    @mute_logger("odoo.http")
    def test_another_employee_reaches_nothing_of_a_conversation_over_rpc(self):
        with trap_jobs():
            result = self.env["ai.conversation"].with_user(self.owner).app_ask("Where is our office?")
        conversation_id, exchange_id = result["conversation"]["id"], result["message"]["id"]

        self.authenticate(self.other.login, self.other.login)
        for method, args, kwargs in (
            ("read", [[conversation_id], ["name"]], {}),
            ("app_messages", [[conversation_id]], {}),
            ("app_feedback", [[conversation_id], exchange_id, "down"], {}),
            ("app_ask", ["Mine now?"], {"conversation_id": conversation_id}),
            ("unlink", [[conversation_id]], {}),
        ):
            with self.subTest(method), trap_jobs() as trap:
                error = self._rpc("ai.conversation", method, args, kwargs).get("error")
                self.assertEqual(error and error["data"]["name"], "odoo.exceptions.AccessError")
                trap.assert_jobs_count(0)
        search = self._rpc("ai.conversation", "search_read", [[["id", "=", conversation_id]], ["name"]])
        self.assertEqual(search["result"], [])
        self.assertEqual(self._rpc("ai.conversation", "app_conversations", [])["result"], [])
        exchange_read = self._rpc("ai.assistant.exchange", "read", [[exchange_id], ["question"]])
        self.assertEqual(exchange_read["error"]["data"]["name"], "odoo.exceptions.AccessError")

        self.authenticate(self.owner.login, self.owner.login)
        messages = self._rpc("ai.conversation", "app_messages", [[conversation_id]])["result"]
        self.assertEqual([message["question"] for message in messages], ["Where is our office?"])
        self.assertTrue(self._rpc("ai.conversation", "unlink", [[conversation_id]])["result"])
        self.assertFalse(self.env["ai.conversation"].browse(conversation_id).exists())

    def test_the_app_shows_a_new_question_waiting_for_its_answer(self):
        # Trapped: the answer job never runs, so Claude is never called.
        with trap_jobs() as trap:
            self.start_tour("/web", "ai_assistant_app_tour", login=self.owner.login)
        trap.assert_jobs_count(1)
        conversation = self.env["ai.conversation"].search([("assistant", "=", "app"), ("user_id", "=", self.owner.id)])
        self.assertEqual(conversation.name, "How many contacts do we have in Belgium?")
        self.assertEqual((conversation.assistant_exchange_ids.state, conversation.res_model), ("pending", False))

    def test_the_answer_replaces_the_pending_question_and_the_composer_takes_the_next(self):
        answer = claude_turn([text("AI Assistant Tour Acme is in Belgium.")], "end_turn")
        with self._answered_while_the_page_is_open(answer) as trap:
            self.start_tour("/web", "ai_assistant_app_answer_tour", login=self.owner.login)
        trap.assert_jobs_count(2)
        conversation = self.env["ai.conversation"].search([("assistant", "=", "app"), ("user_id", "=", self.owner.id)])
        self.assertEqual(
            conversation.assistant_exchange_ids.sorted("id").mapped("state"),
            ["answered", "pending"],
            "the next question waits for its own job",
        )

    def test_an_answer_that_comes_before_its_question_returns_is_kept(self):
        # Trapped: the tour brings the answers itself, client side, before app_ask returns.
        with trap_jobs() as trap:
            self.start_tour("/web", "ai_assistant_app_early_answer_tour", login=self.owner.login)
        trap.assert_jobs_count(2)

    def test_a_refused_asker_sees_the_refusal_in_place_and_no_error(self):
        def leave_the_group(env):
            env.ref(ASSISTANT_GROUP).write({"users": [Command.unlink(self.owner.id)]})

        with self._answered_while_the_page_is_open(before_the_job=leave_the_group) as trap:
            self.start_tour("/web", "ai_assistant_app_refused_tour", login=self.owner.login)
        trap.assert_jobs_count(1)
        exchange = self.env["ai.assistant.exchange"].search([("user_id", "=", self.owner.id)])
        self.assertEqual((exchange.state, exchange.is_refused), ("unavailable", True))

    def _new_answer_posted(self):
        """The owner's conversation with a new answer, and the post that notifies them of it."""
        conversation = self.env["ai.conversation"].sudo().create({"assistant": "app", "user_id": self.owner.id})
        common = {"kind": "app", "user_id": self.owner.id, "conversation_id": conversation.id, "state": "answered"}
        Exchange = self.env["ai.assistant.exchange"].sudo()
        original = Exchange.create(
            {**common, "question": "Which country is AI Assistant Tour Acme in?", "answer": "In France."}
        )
        reply = Exchange.create(
            {**common, "question": original.question, "answer": "The right answer.", "reply_to_id": original.id}
        )
        reply._app_post_new_answer()
        [post] = conversation.message_ids.filtered("partner_ids")
        return conversation, post

    def test_the_link_of_a_new_answer_opens_its_conversation_with_the_question_quoted(self):
        _conversation, post = self._new_answer_posted()
        [link] = html.fromstring(post.body).xpath("//a/@href")
        url = urlsplit(link)
        self.start_tour(f"{url.path}#{url.fragment}", "ai_assistant_app_reply_tour", login=self.owner.login)

    def test_the_inbox_opens_a_new_answer_s_conversation_in_the_app(self):
        self.owner.notification_type = "inbox"
        self._new_answer_posted()
        self.start_tour("/web#action=mail.action_discuss", "ai_assistant_inbox_tour", login=self.owner.login)

    def test_a_mail_s_view_button_opens_the_app_for_its_owner_and_the_form_for_an_administrator(self):
        conversation, _post = self._new_answer_posted()
        view_url = f"/mail/view?model=ai.conversation&res_id={conversation.id}"
        app_url = urlsplit(conversation._app_url())

        self.authenticate(self.owner.login, self.owner.login)
        location = urlsplit(self.url_open(view_url, allow_redirects=False).headers["Location"])
        self.assertEqual((location.path, location.fragment), (app_url.path, app_url.fragment))

        self.authenticate(self.administrator.login, self.administrator.login)
        location = urlsplit(self.url_open(view_url, allow_redirects=False).headers["Location"])
        self.assertEqual(location.path, "/web")
        self.assertIn("model=ai.conversation", location.fragment.split("&"))
        self.assertIn(f"id={conversation.id}", location.fragment.split("&"))

    def test_a_link_to_a_deleted_conversation_opens_the_app_on_its_list(self):
        conversation = self.env["ai.conversation"].sudo().create({"assistant": "app", "user_id": self.owner.id})
        url = urlsplit(conversation._app_url())
        conversation.unlink()
        self.start_tour(f"{url.path}#{url.fragment}", "ai_assistant_app_gone_tour", login=self.owner.login)

    def test_a_link_to_a_conversation_older_than_the_list_opens_it(self):
        conversations = (
            self.env["ai.conversation"]
            .sudo()
            .create([{"assistant": "app", "user_id": self.owner.id} for _index in range(2)])
        )
        self.env["ai.assistant.exchange"].sudo().create(
            [
                {
                    "kind": "app",
                    "user_id": self.owner.id,
                    "conversation_id": conversation.id,
                    "state": "answered",
                    "question": question,
                    "answer": "An answer.",
                }
                for conversation, question in zip(conversations, ("Older question?", "Newer question?"))
            ]
        )
        url = urlsplit(conversations[0]._app_url())
        with patch(f"{AI_CONVERSATION_MODULE}.APP_LIST_LIMIT", 1):
            self.start_tour(f"{url.path}#{url.fragment}", "ai_assistant_app_older_tour", login=self.owner.login)

    def test_the_panel_carries_the_record_on_screen(self):
        partner = self.env["res.partner"].create({"name": "AI Assistant Tour Partner"})
        with trap_jobs() as trap:
            self.start_tour(
                f"/web#model=res.partner&id={partner.id}&view_type=form",
                "ai_assistant_panel_tour",
                login=self.owner.login,
            )
        trap.assert_jobs_count(1)
        exchange = self.env["ai.assistant.exchange"].search([("user_id", "=", self.owner.id)])
        self.assertEqual(
            (exchange.kind, exchange.question, exchange.res_model, exchange.res_id),
            ("app", "Where is this company?", "res.partner", partner.id),
        )
        self.assertEqual(
            (exchange.conversation_id.res_model, exchange.conversation_id.res_id), ("res.partner", partner.id)
        )
