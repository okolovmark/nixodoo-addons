import json
import logging
import os
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from lxml import html

from odoo import SUPERUSER_ID, Command, api
from odoo.tests import HttpCase, TransactionCase, new_test_user, no_retry, tagged
from odoo.tests.tag_selector import TagsSelector
from odoo.tools import config, mute_logger

from .test_ai_assistant_app import AI_CONVERSATION_MODULE
from .test_ai_assistant_engine import claude_turn, text
from .test_ai_assistant_tools import ASSISTANT_KEY_ENV
from odoo.addons.ai_assistant.models.ai_assistant_exchange import ASSISTANT_GROUP
from odoo.addons.queue_job.tests.common import trap_jobs
from odoo.addons.web.tests import test_js

MODULE = "ai_assistant"
APP_URL = "/odoo/action-ai_assistant.ai_assistant_app_action"
ANSWER_WAIT_SECONDS = 60
# Long enough for the page to render the app_ask response, the pending question, before the answer.
PENDING_RENDER_SECONDS = 1


class AiAssistantAppCase(HttpCase):
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
            icp.set_str(f"ai_assistant.{key}", value)
        # The AI Assistant's partner has no address: its notification mails go from the catchall domain's.
        icp.set_str("mail.catchall.domain", "example.com")

    @contextmanager
    def _answered_while_the_page_is_open(self, *claude_results, before_the_job=None):
        """Trap the answer jobs and run the first one from a thread once the page has asked, as the job runner
        would, with Claude's client a test double; `before_the_job(env)` runs first in the job's transaction.

        The job's commit runs the bus's hooks, whose NOTIFY the bus dispatcher hands to the page's websocket.
        Yields the trap."""
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
            except Exception as error:  # noqa: BLE001 -- reported by the test, not lost in the thread
                errors.append(error)

        with (
            trap_jobs() as trap,
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

    @staticmethod
    def _path_and_query(url):
        url = urlsplit(url)
        return f"{url.path}?{url.query}"

    def _conversations(self, *questions):
        """The owner's conversations, one answered question each, the first one the oldest."""
        conversations = (
            self.env["ai.conversation"]
            .sudo()
            .create([{"assistant": "app", "user_id": self.owner.id} for _question in questions])
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
                for conversation, question in zip(conversations, questions, strict=True)
            ]
        )
        return conversations


@tagged("post_install", "-at_install")
class TestAiAssistantAppHttp(AiAssistantAppCase):
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
            self.start_tour("/odoo", "ai_assistant_app_tour", login=self.owner.login)
        trap.assert_jobs_count(1)
        conversation = self.env["ai.conversation"].search([("assistant", "=", "app"), ("user_id", "=", self.owner.id)])
        self.assertEqual(conversation.name, "How many contacts do we have in Belgium?")
        self.assertEqual((conversation.assistant_exchange_ids.state, conversation.res_model), ("pending", False))

    def test_the_answer_replaces_the_pending_question_and_the_composer_takes_the_next(self):
        answer = claude_turn([text("AI Assistant Tour Acme is in Belgium.")], "end_turn")
        with self._answered_while_the_page_is_open(answer) as trap:
            self.start_tour("/odoo", "ai_assistant_app_answer_tour", login=self.owner.login)
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
            self.start_tour("/odoo", "ai_assistant_app_early_answer_tour", login=self.owner.login)
        trap.assert_jobs_count(2)

    def test_a_refused_asker_sees_the_refusal_in_place_and_no_error(self):
        def leave_the_group(env):
            # Every employee has the group through base.group_user, which no user's own groups take away.
            env.ref(ASSISTANT_GROUP).write({"implied_by_ids": [Command.clear()]})

        with self._answered_while_the_page_is_open(before_the_job=leave_the_group) as trap:
            self.start_tour("/odoo", "ai_assistant_app_refused_tour", login=self.owner.login)
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
        self.start_tour(self._path_and_query(link), "ai_assistant_app_reply_tour", login=self.owner.login)

    def test_a_notification_of_a_new_answer_opens_its_conversation_in_the_app(self):
        self.owner.notification_type = "inbox"
        self._new_answer_posted()
        self.start_tour("/odoo", "ai_assistant_inbox_tour", login=self.owner.login)

    def test_a_mail_s_view_button_opens_the_app_for_its_owner_and_the_form_for_an_administrator(self):
        conversation, _post = self._new_answer_posted()
        view_url = f"/mail/view?model=ai.conversation&res_id={conversation.id}"

        self.authenticate(self.owner.login, self.owner.login)
        location = self.url_open(view_url, allow_redirects=False).headers["Location"]
        self.assertEqual(self._path_and_query(location), self._path_and_query(conversation._app_url()))

        self.authenticate(self.administrator.login, self.administrator.login)
        location = urlsplit(self.url_open(view_url, allow_redirects=False).headers["Location"])
        self.assertEqual(location.path, f"/odoo/ai.conversation/{conversation.id}")

    def test_a_link_to_a_deleted_conversation_opens_the_app_on_its_list(self):
        conversation = self.env["ai.conversation"].sudo().create({"assistant": "app", "user_id": self.owner.id})
        url = self._path_and_query(conversation._app_url())
        conversation.unlink()
        self.start_tour(url, "ai_assistant_app_gone_tour", login=self.owner.login)

    def test_a_link_to_a_conversation_older_than_the_list_opens_it(self):
        conversations = self._conversations("Older question?", "Newer question?")
        url = self._path_and_query(conversations[0]._app_url())
        with patch(f"{AI_CONVERSATION_MODULE}.APP_LIST_LIMIT", 1):
            self.start_tour(url, "ai_assistant_app_older_tour", login=self.owner.login)

    def test_the_panel_carries_the_record_on_screen(self):
        partner = self.env["res.partner"].create({"name": "AI Assistant Tour Partner"})
        with trap_jobs() as trap:
            self.start_tour(f"/odoo/res.partner/{partner.id}", "ai_assistant_panel_tour", login=self.owner.login)
        trap.assert_jobs_count(1)
        exchange = self.env["ai.assistant.exchange"].search([("user_id", "=", self.owner.id)])
        self.assertEqual(
            (exchange.kind, exchange.question, exchange.res_model, exchange.res_id),
            ("app", "Where is this company?", "res.partner", partner.id),
        )
        self.assertEqual(
            (exchange.conversation_id.res_model, exchange.conversation_id.res_id), ("res.partner", partner.id)
        )


@tagged("post_install", "-at_install")
class TestAiAssistantAppPhone(AiAssistantAppCase):
    browser_size = "375x667"
    touch_enabled = True

    def test_the_app_shows_its_list_and_a_conversation_in_turn_on_a_phone(self):
        self._conversations("Phone question?")
        self.start_tour(APP_URL, "ai_assistant_app_phone_tour", login=self.owner.login)

    def test_the_panel_fits_a_phone(self):
        partner = self.env["res.partner"].create({"name": "AI Assistant Tour Partner"})
        self.start_tour(f"/odoo/res.partner/{partner.id}", "ai_assistant_panel_phone_tour", login=self.owner.login)


class HootSummary(logging.Handler):
    """The count of passed tests in the summary Hoot logs at the end of a run, which reports a filter
    that selects no test as a success."""

    passed_count = 0

    def emit(self, record):
        if match := re.match(r"\[HOOT\] Passed (\d+) tests", record.getMessage()):
            self.passed_count = int(match[1])


@tagged("post_install", "-at_install")
class TestHootSuite(test_js.HootCommon):
    """web's own check of the Hoot files runs in a run of web's tests only: this one runs core's on
    this module's files."""

    def _get_bundle_assets(self, bundle):
        return [
            asset
            for asset in super()._get_bundle_assets(bundle)
            if asset["filename"].endswith(".test.js") and test_js.RE_ASSET_ADDON.match(asset["url"])[1] == MODULE
        ]

    def test_check_suite(self):
        self.assertTrue(
            self._get_bundle_assets("web.assets_unit_tests"), f"the unit test bundle has the Hoot files of {MODULE}"
        )
        self._check_forbidden_statements("web.assets_unit_tests")


class HootModuleCase(test_js.HootCommon):
    """Runs this module's Hoot tests in a run that installs or updates it, as CI's: web's own suites
    only run in a run that loads web's tests too, and there they run these ones as well."""

    web_suite_class = None
    web_suite_method = ""
    url = ""

    def _web_suite_runs_this_module(self):
        """web's suite is in a run that loads web's tests: one that installs or updates web or base,
        which updates every module, or one that installs and updates nothing. A Hoot parameter that
        selects tests turns the module filter of web's suite off (`HootCommon._get_hoot_filters`)."""
        requested_modules = {*config["init"], *config["update"], *config["reinit"]}
        if requested_modules and not requested_modules & {"base", "web"}:
            return False
        web_suite = self.web_suite_class(self.web_suite_method)
        return (
            TagsSelector(config["test_tags"], sorted(self.registry._init_modules)).check(web_suite)
            and MODULE in web_suite._test_modules
            and not any(sign == "+" for sign, _part in test_js._get_filters(web_suite._test_params))
        )

    def _run_hoot(self):
        if self._web_suite_runs_this_module():
            self.skipTest(f"{self.web_suite_class.__name__} runs the Hoot tests of {MODULE} in this run")
        summary = HootSummary()
        browser_logger = self._logger.getChild("browser")
        browser_logger.addHandler(summary)
        self.addCleanup(browser_logger.removeHandler, summary)
        if not browser_logger.isEnabledFor(logging.RUNBOT):
            self.addCleanup(browser_logger.setLevel, browser_logger.level)
            browser_logger.setLevel(logging.RUNBOT)
        self.browser_js(
            f"{self.url}&id={self._generate_hash(f'@{MODULE}')}",
            "",
            "",
            login="admin",
            timeout=1800,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=test_js.unit_test_error_checker,
        )
        self.assertGreater(summary.passed_count, 0, f"Hoot finds no test of {MODULE} under its filter")


@tagged("hoot", "post_install", "-at_install")
class TestHootDesktop(HootModuleCase):
    web_suite_class = test_js.WebSuite
    web_suite_method = "test_unit_desktop"
    url = "/web/tests?headless&loglevel=2&preset=desktop&timeout=15000"

    @no_retry
    def test_hoot_desktop(self):
        self._run_hoot()


@tagged("hoot", "post_install", "-at_install")
class TestHootMobile(HootModuleCase):
    browser_size = "375x667"
    touch_enabled = True
    web_suite_class = test_js.MobileWebSuite
    web_suite_method = "test_unit_mobile"
    url = "/web/tests?headless&loglevel=2&preset=mobile&tag=-headless&timeout=15000"

    @no_retry
    def test_hoot_mobile(self):
        self._run_hoot()


@tagged("post_install", "-at_install")
class TestHootRunners(TransactionCase):
    RUNNERS = ((TestHootDesktop, "test_hoot_desktop"), (TestHootMobile, "test_hoot_mobile"))

    def test_the_runners_carry_the_tags_of_web_suites(self):
        for runner_class, _test_method in self.RUNNERS:
            with self.subTest(runner=runner_class.__name__):
                self.assertIn("hoot", runner_class.test_tags)
                self.assertGreaterEqual(runner_class.test_tags, runner_class.web_suite_class.test_tags)

    def test_a_runner_leaves_the_module_to_web_suite_only_when_that_suite_runs_it(self):
        modules = ["web", MODULE]
        for runner_class, test_method in self.RUNNERS:
            runner = runner_class(test_method)
            runner.registry = self.registry
            for test_tags, is_left_to_web in (
                (f"/web,/{MODULE}", True),
                (f"/web,/{MODULE},/web[@web/core]", False),
                (f"/web,/{MODULE},-/web[@web/core]", True),
            ):
                with (
                    self.subTest(runner=runner_class.__name__, test_tags=test_tags),
                    patch.dict(config.options, {"test_tags": test_tags, "update": dict.fromkeys(modules, True)}),
                ):
                    self.assertEqual(runner._web_suite_runs_this_module(), is_left_to_web)

    def test_a_runner_reads_from_the_modules_a_run_installs_or_updates_whether_web_suites_run(self):
        """The tests of an update run read a registry reloaded after the update, whose updated modules are none."""
        for runner_class, test_method in self.RUNNERS:
            runner = runner_class(test_method)
            runner.registry = self.registry
            for case, init, update, test_tags, is_left_to_web in (
                ("database created and installed in one run", [MODULE, "base"], [], f"/{MODULE}", True),
                ("same, CI without tags", [MODULE, "base"], [], "+standard", True),
                ("CI after the dependencies", [MODULE], [], "+standard", False),
                ("install after the dependencies", [MODULE], [], f"/{MODULE}", False),
                ("update of the module", [], [MODULE], f"/{MODULE}", False),
                ("update of all modules", [], ["base"], f"/{MODULE}", True),
                ("tests of an installed database", [], [], f"/{MODULE}", True),
            ):
                with (
                    self.subTest(runner=runner_class.__name__, case=case),
                    patch.dict(
                        config.options,
                        {
                            "test_tags": test_tags,
                            "init": dict.fromkeys(init, True),
                            "update": dict.fromkeys(update, True),
                            "reinit": [],
                        },
                    ),
                    patch.object(self.registry, "updated_modules", []),
                ):
                    self.assertEqual(runner._web_suite_runs_this_module(), is_left_to_web)

    def _run_with_browser_log(self, runner_class, test_method, browser_message):
        """Run the runner on a browser that logs browser_message alone, and return its browser logger."""
        runner = runner_class(test_method)
        runner._logger = logging.getLogger(f"{__name__}.{self._testMethodName}.{runner_class.__name__}")
        browser_logger = runner._logger.getChild("browser")
        browser_logger.setLevel(logging.WARNING)
        browser_logger.propagate = False

        def browser_js(*args, **kwargs):
            browser_logger.log(logging.RUNBOT, browser_message)

        with (
            ExitStack() as runner_cleanups,
            patch.object(runner, "addCleanup", runner_cleanups.callback),
            patch.object(runner, "_web_suite_runs_this_module", return_value=False),
            patch.object(runner, "browser_js", browser_js),
        ):
            runner._run_hoot()
        return browser_logger

    def test_a_run_gives_the_browser_logger_back_as_it_found_it(self):
        for runner_class, test_method in self.RUNNERS:
            with self.subTest(runner=runner_class.__name__):
                browser_logger = self._run_with_browser_log(runner_class, test_method, "[HOOT] Passed 3 tests")
                self.assertEqual(browser_logger.level, logging.WARNING)
                self.assertFalse(browser_logger.handlers)

    def test_a_run_whose_filter_selects_no_test_fails(self):
        for runner_class, test_method in self.RUNNERS:
            with (
                self.subTest(runner=runner_class.__name__),
                self.assertRaisesRegex(AssertionError, f"Hoot finds no test of {MODULE} under its filter"),
            ):
                self._run_with_browser_log(
                    runner_class, test_method, "[HOOT] Passed 0 tests (0 assertions, total time: 2ms)"
                )

    def test_the_suite_check_reads_the_module_of_test_files_only(self):
        module_test_file = {"url": f"/{MODULE}/static/tests/ai_assistant_app.test.js", "filename": "app.test.js"}
        assets = [
            module_test_file,
            {"url": f"/{MODULE}/static/src/components/ai_assistant_app/app.js", "filename": "app.js"},
            {"url": "/web/static/tests/core.test.js", "filename": "core.test.js"},
            {"url": "lib.min.js", "filename": "lib.min.js"},
        ]
        with patch.object(test_js.HootCommon, "_get_bundle_assets", return_value=assets):
            self.assertEqual(
                TestHootSuite("test_check_suite")._get_bundle_assets("web.assets_unit_tests"), [module_test_file]
            )
