import json
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx2
import requests

from odoo import release
from odoo.exceptions import AccessError
from odoo.modules.module import get_manifest
from odoo.tests import new_test_user, tagged

from odoo.addons.ai_assistant.models.ai_assistant_engine import (
    ASSISTANT_SYSTEM_PROMPT,
    CLAUDE_KEY_ENV,
    MODE_PROMPTS,
    AiAssistantEngine,
)
from odoo.addons.ai_assistant.models.ai_assistant_tools import (
    ASSISTANT_CLAUDE_MAX_TOKENS,
    ASSISTANT_PREFIX,
    REPORT_TOOL_NAMES,
    TOOL_NAMES,
)
from odoo.addons.ai_claude.models.ai_claude_gateway import NOT_COVERED, turn_chars

from .test_ai_assistant_tools import ASSISTANT_KEY_ENV, AssistantCase


def api_timeout():
    return anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


def tool_use(name, tool_input, tool_id="toolu_1"):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def text(value):
    return SimpleNamespace(type="text", text=value)


def claude_turn(blocks, stop_reason):
    """A Messages API response with only the attributes the gateway reads."""
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0
        ),
    )


class EngineCase(AssistantCase):
    @contextmanager
    def _claude(self, *results):
        create = Mock(side_effect=list(results))
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        gateway_cls = type(self.env["ai.claude.gateway"])
        with (
            patch.dict(os.environ, {ASSISTANT_KEY_ENV: "test-key"}),
            patch.object(gateway_cls, "_claude_client", return_value=client) as factory,
        ):
            yield SimpleNamespace(create=create, factory=factory)

    def _answer(self, user=None, question="Which country is AI Assistant Acme in?", **kwargs):
        options = {"mode": "app", "history_turns": [], **kwargs}
        return self.env["ai.assistant.engine"]._answer(user or self.employee, question, **options)


@tagged("post_install", "-at_install")
class TestAiAssistantEngine(EngineCase):
    def test_answer_reads_through_the_tools_as_the_employee(self):
        lookup = claude_turn(
            [
                tool_use(
                    "search_read",
                    {
                        "model": "res.partner",
                        "domain": json.dumps([["id", "=", self.partner.id]]),
                        "fields": ["country_id"],
                        "limit": 1,
                        "order": "",
                    },
                )
            ],
            "tool_use",
        )
        with self._claude(lookup, claude_turn([text("AI Assistant Acme is in Belgium.")], "end_turn")) as claude:
            outcome = self._answer(allowed_company_ids=self.company.ids)

        self.assertEqual(outcome.answer, "AI Assistant Acme is in Belgium.")
        self.assertEqual(outcome.model, "claude-sonnet-5-5")
        claude.factory.assert_called_once_with("test-key", 120)
        first, second = claude.create.call_args_list
        request = first.kwargs
        self.assertEqual(tuple(tool["name"] for tool in request["tools"]), TOOL_NAMES + REPORT_TOOL_NAMES, "app mode")
        self.assertEqual(request["tools"][-1]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(request["system"][0]["text"], self.env["ai.assistant.engine"]._system_prompt("app"))
        self.assertEqual(request["max_tokens"], 16000)
        question = request["messages"][0]["content"]
        self.assertTrue(question.startswith("<context>\nEmployee: Erin Employee\n"), question)
        self.assertIn(f"Companies: {self.company.name}\n", question)
        self.assertTrue(question.endswith("\n\nWhich country is AI Assistant Acme in?"))
        [result] = second.kwargs["messages"][-1]["content"]
        self.assertFalse(result["is_error"])
        self.assertIn("Belgium", result["content"])

        log = self.env["ai.assistant.tool.call"].search([], limit=1)
        self.assertEqual((log.user_id, log.tool, log.outcome), (self.employee, "search_read", "ok"))
        self.assertEqual(outcome.attempts[0].tool_calls, 1)
        self.assertEqual(
            outcome.prompt_chars,
            len(request["system"][0]["text"]) + sum(turn_chars(turn) for turn in request["messages"]),
            "the size of the first request, as the gateway counts it",
        )

    def test_a_question_never_sent_to_claude_stores_no_prompt_size(self):
        not_configured = self._answer()
        self._set_param("claude_circuit_open_until", "1999999999.5")
        with self._claude() as claude:
            breaker_open = self._answer()
        claude.factory.assert_not_called()
        Exchange = self.env["ai.assistant.exchange"]
        for label, outcome in (("not configured", not_configured), ("breaker open", breaker_open)):
            with self.subTest(label):
                self.assertEqual(outcome.prompt_chars, 0)
                self.assertEqual(
                    (Exchange._outcome_values(outcome)["state"], Exchange._outcome_values(outcome)["prompt_chars"]),
                    ("unavailable", 0),
                    "the row counts no prompt for a question Claude never got",
                )

    def test_without_its_parameter_an_answer_may_take_the_installed_tokens(self):
        self.env["ir.config_parameter"].sudo().search([("key", "=", f"{ASSISTANT_PREFIX}.claude_max_tokens")]).unlink()
        with self._claude(claude_turn([text("Belgium.")], "end_turn")) as claude:
            self._answer()
        self.assertEqual(claude.create.call_args.kwargs["max_tokens"], ASSISTANT_CLAUDE_MAX_TOKENS)
        self.assertEqual(ASSISTANT_CLAUDE_MAX_TOKENS, 16000, "the value the module installs")

    def test_every_python_package_the_module_and_its_tests_import_is_declared(self):
        python = get_manifest("ai_assistant")["external_dependencies"]["python"]
        self.assertEqual(set(python), {"anthropic", "httpx2"}, "httpx2 builds the API errors of these tests")

    def test_assistant_tools_run_as_the_employee_in_the_companies_asked(self):
        seen = []
        tools_cls = type(self.env["ai.assistant.tools"])
        original = tools_cls._execute

        def spy(tools, name, arguments):
            seen.append((tools.env.uid, tools.env.su, tools.env.companies))
            return original(tools, name, arguments)

        with (
            self._claude(
                claude_turn([tool_use("fields", {"model": "res.partner"})], "tool_use"),
                claude_turn([text("Done.")], "end_turn"),
            ),
            patch.object(tools_cls, "_execute", spy),
        ):
            self._answer(allowed_company_ids=self.other_company.ids)
        self.assertEqual(seen, [(self.employee.id, False, self.other_company)])

    def test_a_question_no_claude_model_answers_never_reaches_another_model(self):
        with self._claude(api_timeout(), api_timeout()) as claude, patch.object(requests, "post") as other_model:
            outcome = self._answer()
        self.assertFalse(outcome.answer)
        self.assertEqual([attempt.kind for attempt in outcome.attempts], ["unavailable", "unavailable"])
        self.assertEqual(claude.create.call_count, 2)
        other_model.assert_not_called()
        icp = self.env["ir.config_parameter"].sudo()
        self.assertEqual(icp.get_param(f"{ASSISTANT_PREFIX}.claude_consecutive_failures"), "1", "the breaker counts it")

    def test_without_a_key_nothing_is_asked(self):
        gateway_cls = type(self.env["ai.claude.gateway"])
        with patch.object(gateway_cls, "_claude_client") as factory, patch.object(requests, "post") as other_model:
            outcome = self._answer()
        factory.assert_not_called()
        other_model.assert_not_called()
        self.assertEqual((outcome.answer, outcome.attempts), ("", []))

    def test_answer_sends_the_history_before_the_question(self):
        history = [
            {"role": "user", "content": "Who is our biggest customer?"},
            {"role": "assistant", "content": "AI Assistant Acme."},
        ]
        with self._claude(claude_turn([text("Belgium.")], "end_turn")) as claude:
            self._answer(question="Where are they?", history_turns=history)
        messages = claude.create.call_args.kwargs["messages"]
        self.assertEqual(messages[:2], history)
        self.assertTrue(messages[2]["content"].endswith("Where are they?"))

    def test_only_the_ai_assistant_group_may_ask(self):
        self.assertTrue(self.employee.has_group("ai_assistant.group_ai_assistant"), "every employee is in it")
        portal = new_test_user(self.env, login="test_assistant_portal", groups="base.group_portal")
        with self.assertRaises(AccessError):
            self._answer(user=portal)
        with self.assertRaises(ValueError):
            self._answer(mode="report")

    def test_record_mode_names_the_record_and_its_scope(self):
        with self._claude(claude_turn([text("Belgium.")], "end_turn")) as claude:
            self._answer(mode="record", context_model="res.partner", res_id=self.partner.id)
        request = claude.create.call_args.kwargs
        self.assertTrue(request["system"][0]["text"].endswith(MODE_PROMPTS["record"]))
        self.assertIn("AI Assistant app answers those", MODE_PROMPTS["record"])
        self.assertIn(
            f"Record: Contact (res.partner) id {self.partner.id}: AI Assistant Acme\n</context>",
            request["messages"][0]["content"],
        )

    def test_question_context_says_what_the_employee_may_not_see(self):
        tools = self.env["ai.assistant.engine"]._assistant_tools(self.employee)
        cron = self.env["ir.cron"].search([], limit=1)
        self.assertIn(f"id {cron.id}: not visible to the employee", tools._question_context("ir.cron", cron.id))
        bank = self.env["res.partner.bank"].create({"acc_number": "BE71096123456769", "partner_id": self.partner.id})
        line = tools._context_record_line("res.partner.bank", bank.id)
        self.assertTrue(line.endswith("on the AI deny list"))
        self.assertNotIn("BE71", line)
        self.assertEqual(tools._context_record_line("res.partner", False), "Screen: Contact (res.partner)")
        self.assertEqual(tools._context_record_line("no.such.model", 1), "Screen: no.such.model")

    def test_find_models_is_offered_in_both_modes(self):
        for mode in ("record", "app"):
            with self._claude(claude_turn([text("Belgium.")], "end_turn")) as claude:
                self._answer(mode=mode, context_model="res.partner", res_id=self.partner.id)
            self.assertIn("find_models", [tool["name"] for tool in claude.create.call_args.kwargs["tools"]], mode)

    def test_the_prompt_names_find_models_and_says_a_model_that_is_not_installed_is_no_refusal(self):
        self.assertIn(
            "Find your way before you query: `find_models` names the models that hold a kind of data, `fields` lists"
            " a model's fields, `module_docs` explains how a feature works here",
            ASSISTANT_SYSTEM_PROMPT,
        )
        engine = self.env["ai.assistant.engine"]
        # The shared default, past a module of the organisation's own that gives its own words.
        self.assertIn(
            "A model that is not installed is not a refusal: some data is kept in models of custom modules instead of"
            " Odoo's apps, so look for it with `find_models` before you say you cannot see it.",
            AiAssistantEngine._system_prompt_parts(engine)["not_installed"],
        )
        self.assertIn(engine._system_prompt_parts()["not_installed"], engine._system_prompt("record"))
        self.assertIn(
            "because it is on the AI deny list or outside the employee's access rights", ASSISTANT_SYSTEM_PROMPT
        )

    def test_find_menus_is_offered_in_both_modes_and_not_strict(self):
        for mode in ("record", "app"):
            with self._claude(claude_turn([text("Belgium.")], "end_turn")) as claude:
                self._answer(mode=mode, context_model="res.partner", res_id=self.partner.id)
            tools = {tool["name"]: tool for tool in claude.create.call_args.kwargs["tools"]}
            self.assertIn("find_menus", tools, mode)
            self.assertNotIn("strict", tools["find_menus"], mode)

    def test_the_prompt_names_find_menus_and_forbids_guessing_a_model(self):
        self.assertIn(
            "`find_menus` says where a screen, menu or report is and whether the employee can open it",
            ASSISTANT_SYSTEM_PROMPT,
        )
        self.assertIn(
            "Never guess a model: find it with find_models, or with find_menus when the question is where something is"
            " in Odoo, before you read its documentation or its data.",
            ASSISTANT_SYSTEM_PROMPT,
        )

    def test_the_rendered_prompt_names_this_odoo_and_an_administrator_to_ask(self):
        engine = self.env["ai.assistant.engine"]
        with patch.object(type(engine), "_system_prompt_parts", AiAssistantEngine._system_prompt_parts):
            for mode in MODE_PROMPTS:
                prompt = engine._system_prompt(mode)
                self.assertTrue(
                    prompt.startswith("You are the AI Assistant of our Odoo %s, answering" % release.version_info[0]),
                    mode,
                )
                self.assertIn("suggest asking an administrator, and end the reply with", prompt)
                for leftover in ("%(", "{"):
                    self.assertNotIn(leftover, prompt, mode)

    def test_a_fresh_install_names_a_module_s_key_variable_and_keeps_one_already_set(self):
        engine = self.env["ai.assistant.engine"]
        self.assertEqual(AiAssistantEngine._default_claude_key_env(engine), CLAUDE_KEY_ENV)
        key = f"{ASSISTANT_PREFIX}.claude_key_env"
        parameters = self.env["ir.config_parameter"].sudo()
        with patch.object(type(engine), "_default_claude_key_env", return_value="AI_ASSISTANT_OWN_KEY"):
            parameters.set_param(key, CLAUDE_KEY_ENV)
            engine._seed_claude_key_env()
            self.assertEqual(parameters.get_param(key), "AI_ASSISTANT_OWN_KEY", "the shipped variable is replaced")
            parameters.set_param(key, "AI_ASSISTANT_SET_KEY")
            engine._seed_claude_key_env()
            self.assertEqual(parameters.get_param(key), "AI_ASSISTANT_SET_KEY", "a variable set since is kept")

    def test_the_prompt_asks_for_the_not_covered_marker_only_when_nothing_answers(self):
        self.assertIn(f"end the reply with {NOT_COVERED}", ASSISTANT_SYSTEM_PROMPT)
        self.assertIn("Never add that marker to a reply that answers", ASSISTANT_SYSTEM_PROMPT)

    def test_asker_company_ids_keep_only_the_employee_s_companies(self):
        engine = self.env["ai.assistant.engine"]
        foreign = self.env["res.company"].create({"name": "AI Assistant Foreign Company"})
        self.assertEqual(
            engine._asker_company_ids(self.employee, [self.other_company.id, foreign.id]), self.other_company.ids
        )
        self.assertEqual(engine._asker_company_ids(self.employee, [foreign.id]), self.employee.company_id.ids)
        self.assertEqual(engine._asker_company_ids(self.employee), self.employee.company_id.ids)
