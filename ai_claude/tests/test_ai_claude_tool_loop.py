import os
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from anthropic.types import TextBlock, ThinkingBlock

from odoo.tests import TransactionCase, tagged

from odoo.addons.ai_claude.models.ai_claude_gateway import (
    CIRCUIT_THRESHOLD,
    NOT_COVERED,
    REACTION_FEEDBACK,
    Attempt,
    ClaudeOutcome,
    block_chars,
    block_type,
    cache_marked,
    claude_cost,
    is_question_turn,
    response_usage,
    split_not_covered,
    turn_chars,
)

from .common import (
    GATEWAY_LOGGER,
    KEY_ENV,
    PREFIX,
    AiClaudeCase,
    api_timeout,
    claude_turn,
    text,
    tool_use,
    usage,
)

TOOLS = [
    {"name": "search_read", "description": "Records.", "input_schema": {"type": "object"}},
    {"name": "fields", "description": "Fields.", "input_schema": {"type": "object"}},
]
QUESTION = {"role": "user", "content": "How many open orders?"}


@tagged("post_install", "-at_install")
class TestClaudeToolLoop(AiClaudeCase):
    def setUp(self):
        super().setUp()
        self.env["ir.config_parameter"].sudo().set_str(f"{PREFIX}.claude_models", "claude-sonnet-5-5,claude-haiku-5-5")
        self.gateway = self.env["ai.claude.gateway"]

    @contextmanager
    def _claude(self, *results):
        create = Mock(side_effect=list(results))
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with (
            patch.dict(os.environ, {KEY_ENV: "test-key"}),
            patch.object(type(self.gateway), "_claude_client", return_value=client),
        ):
            yield create

    def _chain(self, executor, turns=None, tools=TOOLS):
        return self.gateway._claude_chain(PREFIX, "system", turns or [QUESTION], tools=tools, tool_executor=executor)

    def test_claude_call_answers_after_the_tool_results(self):
        first = claude_turn(
            [text("Let me count."), tool_use("search_read", {"model": "sale.order"})], "tool_use", 1000, 50
        )
        executor = Mock(return_value=('{"records": [1, 2]}', False))
        with self._claude(first, claude_turn([text("Two open orders.")], "end_turn", 1500, 20)) as create:
            outcome = self._chain(executor)

        self.assertEqual(outcome.answer, "Two open orders.")
        executor.assert_called_once_with("search_read", {"model": "sale.order"})
        self.assertEqual(create.call_count, 2)
        self.assertEqual(create.call_args_list[0].kwargs["messages"], [QUESTION])
        self.assertEqual(
            create.call_args_list[1].kwargs["messages"],
            [
                QUESTION,
                {"role": "assistant", "content": first.content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": '{"records": [1, 2]}',
                            "is_error": False,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
            ],
            "the assistant turn goes back whole, its text included, then one turn of results",
        )
        [attempt] = outcome.attempts
        self.assertEqual((attempt.model, attempt.kind, attempt.tool_calls), ("claude-sonnet-5-5", "answered", 1))
        self.assertEqual(attempt.usage, usage(2500, 70), "the tokens of both calls")
        self.assertAlmostEqual(
            attempt.cost,
            claude_cost("claude-sonnet-5-5", usage(1000, 50)) + claude_cost("claude-sonnet-5-5", usage(1500, 20)),
        )
        self.assertIn(", 1 tool call, $", attempt.trace_line())

    def test_claude_tool_results_answer_every_tool_use_of_a_turn_in_one_user_turn(self):
        both = claude_turn(
            [
                tool_use("fields", {"model": "sale.order"}, "toolu_a"),
                tool_use("fields", {"model": "res.partner"}, "toolu_b"),
            ],
            "tool_use",
        )
        executor = Mock(side_effect=[("sale fields", False), ("denied", True)])
        with self._claude(both, claude_turn([text("Done.")], "end_turn")) as create:
            outcome = self._chain(executor)

        results = create.call_args_list[1].kwargs["messages"][-1]
        self.assertEqual(results["role"], "user")
        self.assertEqual(
            [(block["tool_use_id"], block["content"], block["is_error"]) for block in results["content"]],
            [("toolu_a", "sale fields", False), ("toolu_b", "denied", True)],
        )
        self.assertEqual(outcome.attempts[0].tool_calls, 2)
        self.assertIn(", 2 tool calls, ", outcome.trace())

    def test_claude_tool_results_keeps_the_order_and_the_ids(self):
        blocks = [tool_use("fields", {"model": "a"}, "toolu_a"), tool_use("fields", {"model": "b"}, "toolu_b")]
        results = self.gateway._claude_tool_results(blocks, lambda name, tool_input: (tool_input["model"], False))
        self.assertEqual(
            [(result["tool_use_id"], result["content"]) for result in results], [("toolu_a", "a"), ("toolu_b", "b")]
        )
        self.assertEqual({result["type"] for result in results}, {"tool_result"})

    def test_a_tool_that_raises_is_an_error_result_and_the_loop_goes_on(self):
        executor = Mock(side_effect=RuntimeError("boom"))
        with (
            self._claude(
                claude_turn([tool_use("fields", {"model": "sale.order"})], "tool_use"),
                claude_turn([text("I could not read the fields.")], "end_turn"),
            ) as create,
            self.assertLogs(GATEWAY_LOGGER, "WARNING"),
        ):
            outcome = self._chain(executor)

        [result] = create.call_args_list[1].kwargs["messages"][-1]["content"]
        self.assertTrue(result["is_error"])
        self.assertEqual(result["content"], "The tool failed: RuntimeError: boom")
        self.assertEqual(outcome.answer, "I could not read the fields.")

    def test_the_round_cap_ends_the_chain_without_trying_the_next_model(self):
        self.env["ir.config_parameter"].sudo().set_str(f"{PREFIX}.claude_max_tool_rounds", "2")
        executor = Mock(return_value=("[]", False))
        turns = [claude_turn([tool_use("fields", {"model": "x"}, f"toolu_{index}")], "tool_use") for index in range(3)]
        with self._claude(*turns) as create:
            outcome = self._chain(executor)

        self.assertEqual(create.call_count, 3)
        self.assertEqual(executor.call_count, 2, "the third round's tools never run")
        self.assertFalse(outcome.answer)
        [attempt] = outcome.attempts
        self.assertEqual(attempt.kind, "tool_limit", "a tool limit is not a model failure: Haiku is not asked")
        self.assertIn("more than 2 rounds", attempt.error)
        self.assertEqual(attempt.usage["input_tokens"], 3000, "every billed call is counted")
        self.assertEqual(attempt.tool_calls, 2)

    def test_the_time_budget_stops_the_loop_before_the_next_call(self):
        self.env["ir.config_parameter"].sudo().set_str(f"{PREFIX}.claude_tool_budget_seconds", "1")

        def slow_tool(name, tool_input):
            time.sleep(1.05)
            return "[]", False

        with self._claude(
            claude_turn([tool_use("fields", {"model": "x"})], "tool_use"), claude_turn([text("late")], "end_turn")
        ) as create:
            outcome = self._chain(slow_tool)

        self.assertEqual(create.call_count, 1)
        self.assertEqual(outcome.attempts[0].kind, "tool_limit")
        self.assertIn("1 s budget", outcome.attempts[0].error)
        self.assertEqual(len(outcome.attempts), 1)

    def test_a_tool_asked_for_past_the_budget_is_not_run(self):
        self.env["ir.config_parameter"].sudo().set_str(f"{PREFIX}.claude_tool_budget_seconds", "1")
        ran = []

        def slow_tool(name, tool_input):
            ran.append(tool_input["model"])
            time.sleep(1.05)
            return "[]", False

        both = claude_turn(
            [tool_use("fields", {"model": "a"}, "toolu_a"), tool_use("fields", {"model": "b"}, "toolu_b")], "tool_use"
        )
        with self._claude(both, claude_turn([text("late")], "end_turn")):
            outcome = self._chain(slow_tool)
        self.assertEqual(ran, ["a"], "the second tool of the round is not run once the budget is spent")
        self.assertEqual(outcome.attempts[0].kind, "tool_limit")
        results = self.gateway._claude_tool_results(
            [tool_use("fields", {"model": "c"}, "toolu_c")], Mock(), deadline=time.time() - 1
        )
        self.assertEqual(
            results,
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_c",
                    "content": "Not run: the time for this answer's tools is spent.",
                    "is_error": True,
                }
            ],
        )

    def test_on_fallback_runs_before_a_later_model_starts_over(self):
        executor = Mock(return_value=("[]", False))
        with self._claude(
            claude_turn([tool_use("fields", {"model": "x"})], "tool_use"),
            api_timeout(),
            claude_turn([text("Haiku's answer.")], "end_turn"),
        ) as create:
            calls_at_fallback = []
            outcome = self.gateway._claude_chain(
                PREFIX,
                "system",
                [QUESTION],
                tools=TOOLS,
                tool_executor=executor,
                on_fallback=lambda: calls_at_fallback.append(create.call_count),
            )
        self.assertEqual(outcome.answer, "Haiku's answer.")
        self.assertEqual(calls_at_fallback, [2], "once, after Sonnet's calls and before Haiku's")

    def test_a_truncated_round_falls_to_the_next_model_from_the_original_turns(self):
        executor = Mock(return_value=("[]", False))
        with self._claude(
            claude_turn([tool_use("fields", {"model": "x"})], "tool_use"),
            claude_turn([text("cut")], "max_tokens"),
            claude_turn([text("Haiku's answer.")], "end_turn"),
        ) as create:
            outcome = self._chain(executor)

        self.assertEqual([attempt.kind for attempt in outcome.attempts], ["truncated", "answered"])
        self.assertEqual(outcome.answer, "Haiku's answer.")
        self.assertEqual(create.call_args_list[2].kwargs["model"], "claude-haiku-5-5")
        self.assertEqual(create.call_args_list[2].kwargs["messages"], [QUESTION])

    def test_an_error_mid_loop_falls_to_the_next_model_from_the_original_turns(self):
        executor = Mock(return_value=("[]", False))
        with self._claude(
            claude_turn([tool_use("fields", {"model": "x"})], "tool_use", 4000, 10),
            api_timeout(),
            claude_turn([text("Haiku's answer.")], "end_turn"),
        ) as create:
            outcome = self._chain(executor)

        first, second = outcome.attempts
        self.assertEqual((first.kind, second.kind), ("unavailable", "answered"))
        self.assertEqual(first.usage["input_tokens"], 4000, "the round billed before the failure stays counted")
        self.assertEqual(create.call_args_list[2].kwargs["messages"], [QUESTION])

    def test_a_paused_turn_is_sent_back_with_the_cache_mark(self):
        paused = claude_turn([TextBlock(type="text", text="Still working.")], "pause_turn")
        executor = Mock()
        with self._claude(paused, claude_turn([text("Finished.")], "end_turn")) as create:
            outcome = self._chain(executor)

        marked_text = {"type": "text", "text": "Still working.", "cache_control": {"type": "ephemeral"}}
        self.assertEqual(
            create.call_args_list[1].kwargs["messages"],
            [QUESTION, {"role": "assistant", "content": [marked_text]}],
            "the block as the SDK sends it, marked like any round",
        )
        executor.assert_not_called()
        self.assertEqual(outcome.answer, "Finished.")

    def test_each_round_reads_the_rounds_before_it_from_the_cache(self):
        rounds = [claude_turn([tool_use("fields", {"model": "x"}, f"toolu_{index}")], "tool_use") for index in range(2)]
        executor = Mock(return_value=("[]", False))
        with self._claude(*rounds, claude_turn([text("Done.")], "end_turn")) as create:
            self._chain(executor)

        def marks(call):
            return [
                (turn_index, block_index)
                for turn_index, turn in enumerate(call.kwargs["messages"])
                if not isinstance(turn["content"], str)
                for block_index, block in enumerate(turn["content"])
                if isinstance(block, dict) and "cache_control" in block
            ]

        first, second, third = create.call_args_list
        self.assertEqual(marks(first), [], "a question answered in one call writes nothing to the cache")
        self.assertEqual(marks(second), [(2, 0)], "the latest tool results, and only them")
        self.assertEqual(marks(third), [(4, 0)], "the mark moves on: the earlier rounds are read from the cache")
        self.assertEqual(cache_marked([QUESTION])[0]["content"][0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(QUESTION["content"], "How many open orders?", "the caller's turns are left as they were")
        thinking = ThinkingBlock(type="thinking", thinking="Counting.", signature="sig")
        assistant_turn = {"role": "assistant", "content": [TextBlock(type="text", text="Working."), thinking]}
        marked = cache_marked([QUESTION, assistant_turn])[-1]["content"]
        self.assertEqual(marked[0], {"type": "text", "text": "Working.", "cache_control": {"type": "ephemeral"}})
        self.assertIs(marked[1], thinking, "a thinking block may not carry the mark")
        only_thinking = [QUESTION, {"role": "assistant", "content": [thinking]}]
        self.assertEqual(cache_marked(only_thinking), only_thinking)

    def test_the_last_tool_carries_the_cache_mark(self):
        tools = [dict(tool) for tool in TOOLS]
        with self._claude(claude_turn([text("ok")], "end_turn")) as create:
            self._chain(Mock(), tools=tools)

        sent = create.call_args.kwargs["tools"]
        self.assertEqual(sent[-1]["cache_control"], {"type": "ephemeral"})
        self.assertNotIn("cache_control", sent[0])
        self.assertNotIn("cache_control", tools[-1], "the caller's definitions are left as they were")

    def test_without_an_executor_a_tool_use_stop_is_an_empty_answer(self):
        with self._claude(claude_turn([tool_use("fields", {"model": "x"})], "tool_use")) as create:
            answer, attempt = self.gateway._claude_call(
                self.gateway._claude_client("test-key", 120), "claude-haiku-5-5", "system", [QUESTION], 100, "low"
            )
        self.assertEqual((answer, attempt.kind), ("", "empty"))
        self.assertEqual(create.call_count, 1)
        self.assertNotIn("tools", create.call_args.kwargs)

    def test_claude_answer_reads_the_stop_reason(self):
        cases = {
            "end_turn": ("Answer.", "answered"),
            "refusal": ("", "refusal"),
            "max_tokens": ("", "truncated"),
            "model_context_window_exceeded": ("", "truncated"),
        }
        for stop_reason, expected in cases.items():
            answer, kind, _error = self.gateway._claude_answer(claude_turn([text("Answer.")], stop_reason))
            self.assertEqual((answer, kind), expected, stop_reason)
        self.assertEqual(self.gateway._claude_answer(claude_turn([], "end_turn"))[1], "empty")

    def test_a_leading_turn_of_tool_results_does_not_open_the_messages(self):
        orphan = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_0", "content": "[]"}]}
        with self._claude(claude_turn([text("ok")], "end_turn")) as create:
            self._chain(Mock(), turns=[orphan, {"role": "assistant", "content": "Earlier answer."}, QUESTION])
        self.assertEqual(create.call_args.kwargs["messages"], [QUESTION])
        self.assertFalse(is_question_turn(orphan))
        self.assertTrue(is_question_turn({"role": "user", "content": [{"type": "text", "text": "hi"}]}))
        self.assertFalse(is_question_turn({"role": "assistant", "content": "hi"}))

    def test_turn_chars_counts_strings_and_content_blocks(self):
        self.assertEqual(turn_chars(QUESTION), len(QUESTION["content"]))
        blocks = [
            text("abc"),
            tool_use("fields", {"model": "x"}),
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "12345"},
        ]
        self.assertEqual(turn_chars({"role": "user", "content": blocks}), 3 + len('{"model": "x"}') + 5)
        self.assertEqual((block_type(blocks[0]), block_type(blocks[2])), ("text", "tool_result"))
        self.assertEqual(block_chars(SimpleNamespace(type="thinking", thinking="")), 0)

    def test_add_call_prices_each_call_on_its_own(self):
        attempt = Attempt("claude-haiku-5-5", "answered")
        for _call in range(2):
            attempt.add_call(usage(60000, 100))
        self.assertEqual(attempt.usage, usage(120000, 200))
        self.assertAlmostEqual(
            attempt.cost,
            2 * claude_cost("claude-haiku-5-5", usage(60000, 100)),
            msg="two calls under Haiku's price step stay at the base rate, though their sum is past it",
        )
        self.assertEqual(attempt.peak_prompt_tokens, 60000)
        self.assertEqual(
            self.gateway._claude_deviations(ClaudeOutcome(answer="ok", attempts=[attempt])),
            [],
            "the price-step note reads the largest call, not the sum",
        )
        self.assertEqual(response_usage(claude_turn([], "end_turn", 7, 8, 9, 10)), usage(7, 8, 9, 10))

    def test_trace_line_names_the_tool_calls(self):
        attempt = Attempt("claude-sonnet-5-5", "tool_limit", 900, "the model asked for more", tool_calls=1)
        self.assertEqual(
            attempt.trace_line(), "claude-sonnet-5-5: tool_limit, 900 ms, 1 tool call - the model asked for more"
        )

    def test_a_tool_limit_after_a_failed_model_is_no_breaker_failure(self):
        """Haiku ran out of tool rounds: Claude answered its calls, whatever Sonnet did before."""
        icp = self.env["ir.config_parameter"].sudo()
        # The breaker opened at its threshold of failures, and its cooldown is over.
        icp.set_str(f"{PREFIX}.claude_consecutive_failures", str(CIRCUIT_THRESHOLD))
        icp.set_str(f"{PREFIX}.claude_circuit_open_until", str(time.time() - 1))
        sonnet_down = Attempt("claude-sonnet-5-5", "unavailable", 10, "overloaded")
        limited = ClaudeOutcome(attempts=[sonnet_down, Attempt("claude-haiku-5-5", "tool_limit", 20)])
        self.gateway._claude_record(PREFIX, limited)
        self.assertEqual(icp.get_str(f"{PREFIX}.claude_consecutive_failures"), "0")
        self.assertEqual(icp.get_str(f"{PREFIX}.claude_circuit_open_until"), "0")

        both_down = ClaudeOutcome(attempts=[sonnet_down, Attempt("claude-haiku-5-5", "unavailable", 20)])
        self.gateway._claude_record(PREFIX, both_down)
        self.assertEqual(icp.get_str(f"{PREFIX}.claude_consecutive_failures"), "1", "a real outage counts")


@tagged("post_install", "-at_install")
class TestAssistantMarkers(TransactionCase):
    def test_split_not_covered_strips_the_marker_or_a_cut_start_of_it(self):
        self.assertEqual(split_not_covered(f"Ask IT.\n\n{NOT_COVERED}"), ("Ask IT.", True))
        self.assertEqual(split_not_covered("Covered, see [[NOT_COVERED]] above"), ("Covered, see  above", True))
        self.assertEqual(split_not_covered("Ends with the start [[NOT_COV"), ("Ends with the start", True))
        self.assertEqual(split_not_covered("A list [1] and [x]"), ("A list [1] and [x]", False))
        self.assertEqual([feedback for _emoji, feedback in REACTION_FEEDBACK], ["down", "up"], "thumbs-down first")
