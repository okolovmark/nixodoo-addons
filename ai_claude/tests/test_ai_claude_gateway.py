import logging
import threading
import time
from typing import get_args, get_type_hints
from unittest.mock import patch

import anthropic
import httpx2
from psycopg2 import errors as pg_errors
from psycopg2.pool import PoolError

from odoo.modules.module import get_manifest
from odoo.tests import tagged

from odoo.addons.ai_claude.models.ai_claude_gateway import (
    CLAUDE_EFFORT,
    CLAUDE_EFFORT_VALUES,
    Attempt,
    ClaudeOutcome,
    claude_cost,
    claude_trace_kinds,
    quiet_sql_errors,
)

from .common import (
    API_URL,
    GATEWAY_LOGGER,
    PREFIX,
    AiClaudeCase,
    api_error,
    api_timeout,
    claude_message,
    spend_limit_429,
)

QUESTION = {"role": "user", "content": "How many open orders?"}
UNAVAILABLE = "claude-sonnet-5-5 did not answer (Anthropic unavailable: overloaded)"


@tagged("post_install", "-at_install")
class TestClaudeGateway(AiClaudeCase):
    def test_the_manifest_declares_the_anthropic_sdk_and_httpx2(self):
        manifest = get_manifest("ai_claude")
        python = manifest["external_dependencies"]["python"]
        self.assertEqual(set(python), {"anthropic", "httpx2"}, "httpx2 builds the API errors of the Claude tests")
        self.assertEqual(manifest["depends"], ["mail"], "nothing an assistant brings")

    def test_a_non_positive_or_non_integer_parameter_reads_as_its_default(self):
        icp = self.env["ir.config_parameter"].sudo()
        for stored in ("-5", "0", "1.5", "abc"):
            icp.set_param(f"{PREFIX}.claude_max_tokens", stored)
            self.assertEqual(self.gateway._assistant_int_param(PREFIX, "claude_max_tokens", 500), 500, stored)
        icp.set_param(f"{PREFIX}.claude_max_tokens", "120")
        self.assertEqual(self.gateway._assistant_int_param(PREFIX, "claude_max_tokens", 500), 120)

    def test_no_key_means_no_claude_call(self):
        with patch.object(type(self.gateway), "_claude_client") as factory:
            outcome = self.gateway._claude_chain(PREFIX, "system", [QUESTION])
        factory.assert_not_called()
        self.assertEqual((outcome.answer, outcome.trace()), ("", ""), "Claude not configured is no attempt")

    def test_a_cut_answer_is_no_answer_and_no_outage(self):
        cut = claude_message("We do not list those publicly. [[NOT_COV", stop_reason="max_tokens")
        with self._claude(cut):
            outcome = self._ask()
        self.assertFalse(outcome.answer)
        self.assertTrue(outcome.trace().startswith("claude-haiku-5-5: truncated, "))
        self.assertEqual(self._param("claude_consecutive_failures"), "0", "a cut answer is no outage")

    def test_the_breaker_is_written_outside_the_askers_transaction(self):
        icp_cls = type(self.env["ir.config_parameter"])
        original = icp_cls.set_param
        writes = []

        def spy(records, key, value):
            writes.append((key, records.env.cr))
            return original(records, key, value)

        with patch.object(icp_cls, "set_param", spy), self._claude(api_timeout()):
            self._ask()
        self.assertIn(f"{PREFIX}.claude_consecutive_failures", {key for key, _cursor in writes})
        self.assertEqual(
            [key for key, cursor in writes if cursor is self.env.cr],
            [],
            "a conflict in the asker's transaction would replay the request, Claude's bill included",
        )
        self.assertEqual(self._param("claude_consecutive_failures"), "1")

    def test_three_failures_open_the_breaker_and_the_fourth_question_skips_claude(self):
        with self._claude(api_timeout(), api_timeout(), api_timeout()) as claude:
            for _i in range(4):
                outcome = self._ask()
        self.assertEqual(claude.create.call_count, 3)
        self.assertGreater(float(self._param("claude_circuit_open_until")), time.time())
        self.assertTrue(outcome.trace().startswith("Claude: circuit_open"))

    def test_an_answer_resets_the_failure_count(self):
        self.env["ir.config_parameter"].sudo().set_param(f"{PREFIX}.claude_consecutive_failures", "2")
        with self._claude(claude_message("ok")):
            self._ask()
        self.assertEqual(self._param("claude_consecutive_failures"), "0")

    def test_claude_is_asked_again_once_the_cooldown_has_passed(self):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param(f"{PREFIX}.claude_consecutive_failures", "3")
        icp.set_param(f"{PREFIX}.claude_circuit_open_until", str(time.time() - 1))
        with self._claude(claude_message("ok")) as claude:
            self.assertEqual(self._ask().answer, "ok")
        claude.create.assert_called_once()
        self.assertEqual(
            (self._param("claude_consecutive_failures"), self._param("claude_circuit_open_until")), ("0", "0")
        )

    def test_the_spend_limit_opens_the_breaker_and_skips_the_models_after_it(self):
        self.env["ir.config_parameter"].sudo().set_param(
            f"{PREFIX}.claude_models", "claude-sonnet-5-5,claude-haiku-5-5"
        )
        with self._claude(spend_limit_429()) as claude:
            outcome = self._ask()
            self.assertTrue(self.gateway._claude_circuit_open(PREFIX), "a spend limit opens the breaker at once")
            self.assertTrue(self._ask("second?").trace().startswith("Claude: circuit_open"))
        claude.create.assert_called_once()
        self.assertEqual(outcome.trace().splitlines()[1], "claude-haiku-5-5: skipped", "they share the limit")
        lines = self.gateway._claude_deviations(outcome)
        self.assertTrue(any("spend limit reached" in line for line in lines))
        self.assertIn(
            "The spend limit opened the Claude breaker: the next questions skip Claude until it closes.", lines
        )

    def test_an_expected_breaker_conflict_logs_no_bad_query(self):
        """The conflict is caught and costs one count; odoo.sql_db's ERROR line for it only alarms
        whoever reads the log."""

        def conflicting_write(gateway, prefix, outcome):
            gateway.env.cr.execute(
                "INSERT INTO ir_config_parameter (key, value) SELECT key, value FROM ir_config_parameter"
                " WHERE key = %s",
                (f"{prefix}.claude_consecutive_failures",),
            )

        with (
            patch.object(type(self.gateway), "_claude_record", conflicting_write),
            self.assertNoLogs("odoo.sql_db", "ERROR"),
            # The int: Odoo names its RUNBOT level 25 "INFO", so the string would miss _logger.info.
            self.assertLogs(GATEWAY_LOGGER, logging.INFO) as logs,
        ):
            self.gateway._breaker_write("_claude_record", PREFIX, ClaudeOutcome())
        self.assertIn("ai claude: _claude_record met a concurrent breaker write and was dropped", logs.output[0])

    def test_a_full_connection_pool_drops_the_breaker_write(self):
        down = ClaudeOutcome(attempts=[Attempt("claude-haiku-5-5", "unavailable", 10)])
        with (
            patch.object(self.registry, "cursor", side_effect=PoolError("The Connection Pool Is Full")),
            self.assertLogs(GATEWAY_LOGGER, "WARNING") as logs,
        ):
            self.gateway._breaker_write("_claude_record", PREFIX, down)
        self.assertIn("ai claude: _claude_record found no free database connection and was dropped", logs.output[0])
        self.assertEqual(self._param("claude_consecutive_failures"), "0")

    def test_a_model_that_did_not_answer_is_one_whole_sentence_by_where_the_question_went(self):
        unavailable = Attempt("claude-sonnet-5-5", "unavailable", 10, "overloaded")
        nobody = [f"{UNAVAILABLE}; no other model answers, so nobody answered."]
        self.assertEqual(self.gateway._claude_deviations(ClaudeOutcome(attempts=[unavailable])), nobody)
        self.assertEqual(self.gateway._claude_deviations(ClaudeOutcome(attempts=[unavailable]), "none"), nobody)
        answered = ClaudeOutcome(answer="ok", attempts=[unavailable, Attempt("claude-haiku-5-5", "answered", 10)])
        self.assertEqual(self.gateway._claude_deviations(answered), [f"{UNAVAILABLE}; fell to claude-haiku-5-5."])

    def test_a_fallback_the_table_does_not_know_says_only_what_is_known(self):
        outcome = ClaudeOutcome(attempts=[Attempt("claude-sonnet-5-5", "unavailable", 10, "overloaded")])
        self.assertEqual(self.gateway._claude_deviations(outcome, fallback="queued"), [f"{UNAVAILABLE}."])

    def test_a_plain_answer_has_no_deviation(self):
        with self._claude(claude_message("ok")):
            self.assertEqual(self.gateway._claude_deviations(self._ask()), [])

    def test_a_haiku_prompt_near_its_price_step_is_noted(self):
        with self._claude(claude_message("ok", input_tokens=85000)):
            [line] = self.gateway._claude_deviations(self._ask())
        self.assertIn("85000 input tokens, close to its 100000-token price step", line)

    def test_a_haiku_prompt_past_its_price_step_is_noted_as_past(self):
        with self._claude(claude_message("ok", input_tokens=120000)):
            [line] = self.gateway._claude_deviations(self._ask())
        self.assertIn("120000 input tokens, past its 100000-token price step", line)
        self.assertNotIn("close to", line)

    def test_a_model_without_a_price_costs_nothing_and_is_noted(self):
        self.env["ir.config_parameter"].sudo().set_param(f"{PREFIX}.claude_models", "claude-future-9")
        with self._claude(claude_message("ok")):
            outcome = self._ask()
        self.assertEqual((outcome.model, outcome.billing_values()["cost"]), ("claude-future-9", 0))
        self.assertIn(
            "No price is known for claude-future-9: its cost is recorded as 0.",
            self.gateway._claude_deviations(outcome),
        )

    def test_the_incident_mail_says_another_model_answered_or_nothing_did(self):
        body = str(self.env["ai.exchange.mixin"]._claude_incident_body())
        self.assertIn("over the last %(minutes)s minutes: another model answered them, or nothing did.</p>", body)
        self.assertNotIn("office", body)

    @staticmethod
    def _usage(input_tokens=0, output_tokens=0, cache_read=0, cache_write=0):
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
        }

    def _billed(self, model, kind, latency_ms, usage):
        return Attempt(model, kind, latency_ms, usage=usage, cost=claude_cost(model, usage))

    def test_every_error_is_given_its_kind(self):
        gateway = self.env["ai.claude.gateway"]
        cases = {
            "usage limits": api_error(
                anthropic.BadRequestError, 400, "You have reached your specified API usage limits."
            ),
            "other 400": api_error(anthropic.BadRequestError, 400, "messages: field required"),
            "402": api_error(anthropic.APIStatusError, 402, "billing", body={"error": {"type": "billing_error"}}),
            "429 spend limit": spend_limit_429(),
            "429 spend limit, no error code": api_error(
                anthropic.RateLimitError,
                429,
                "Error code: 429 - spend limit reached",
                body={"type": "error", "error": {"type": "rate_limit_error", "message": "spend limit reached"}},
            ),
            "429 spend limit, a text body": api_error(
                anthropic.RateLimitError, 429, "Workspace spend limit reached", body="Workspace spend limit reached"
            ),
            "429": api_error(anthropic.RateLimitError, 429, "rate limited"),
            "401": api_error(anthropic.AuthenticationError, 401, "invalid x-api-key"),
            "403": api_error(anthropic.PermissionDeniedError, 403, "forbidden"),
            "500": api_error(anthropic.InternalServerError, 500, "internal"),
            "529": api_error(anthropic.OverloadedError, 529, "overloaded"),
            "404": api_error(anthropic.NotFoundError, 404, "model not found"),
            "timeout": api_timeout(),
            "connection": anthropic.APIConnectionError(request=httpx2.Request("POST", API_URL)),
            "unexpected": ValueError("unexpected"),
        }
        expected = {
            "usage limits": "spend_limit",
            "other 400": "bad_request",
            "402": "spend_limit",
            "429 spend limit": "spend_limit",
            "429 spend limit, no error code": "spend_limit",
            "429 spend limit, a text body": "spend_limit",
            "429": "unavailable",
            "401": "config",
            "403": "config",
            "500": "unavailable",
            "529": "unavailable",
            "404": "bad_request",
            "timeout": "unavailable",
            "connection": "unavailable",
            "unexpected": "bad_request",
        }
        self.assertEqual({case: gateway._claude_error_kind(error) for case, error in cases.items()}, expected)

    def test_the_cost_of_a_call(self):
        def usage(input_tokens, output_tokens, cache_read, cache_write):
            return {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
            }

        below = usage(50000, 1000, 20000, 30000)
        self.assertAlmostEqual(
            claude_cost("claude-haiku-5-5", below),
            (50000 * 0.10 + 1000 * 0.50 + 30000 * 0.125 + 20000 * 0.01) / 1_000_000,
            msg="100,000 input tokens is still the base rate",
        )
        above = usage(60000, 1000, 30000, 20000)
        self.assertAlmostEqual(
            claude_cost("claude-haiku-5-5", above),
            (60000 * 0.50 + 1000 * 2.50 + 20000 * 0.625 + 30000 * 0.05) / 1_000_000,
            msg="past 100,000 input tokens, cache included, the whole call is at the second rates",
        )
        self.assertAlmostEqual(
            claude_cost("claude-sonnet-5-5", above),
            (60000 * 2.00 + 1000 * 10.00 + 20000 * 2.50 + 30000 * 0.20) / 1_000_000,
        )
        self.assertAlmostEqual(
            claude_cost("claude-opus-5-5", below),
            (50000 * 4.00 + 1000 * 20.00 + 30000 * 5.00 + 20000 * 0.20) / 1_000_000,
        )
        self.assertIsNone(claude_cost("claude-future-9", below))

    def test_an_effort_the_api_rejects_reads_as_the_default(self):
        effort_hint = get_type_hints(anthropic.types.OutputConfigParam)["effort"]
        accepted = next(get_args(argument) for argument in get_args(effort_hint) if get_args(argument))
        self.assertEqual(set(CLAUDE_EFFORT_VALUES), set(accepted), "the values the SDK sends")
        gateway = self.env["ai.claude.gateway"]
        icp = self.env["ir.config_parameter"].sudo()
        for stored, expected in (
            ("xhigh", "xhigh"),
            (" max ", "max"),
            ("turbo", CLAUDE_EFFORT),
            ("High", CLAUDE_EFFORT),
        ):
            icp.set_param(f"{PREFIX}.claude_effort", stored)
            self.assertEqual(gateway._claude_effort(PREFIX), expected, stored)
        icp.set_param(f"{PREFIX}.claude_effort", "turbo")
        with self._claude(claude_message("ok")) as claude:
            self._ask()
        self.assertEqual(claude.create.call_args.kwargs["output_config"], {"effort": CLAUDE_EFFORT})

    def test_quiet_sql_errors_keeps_other_failures_and_other_threads(self):
        sql_logger = logging.getLogger("odoo.sql_db")
        conflict = pg_errors.SerializationFailure("could not serialize access")
        with self.assertLogs("odoo.sql_db", "ERROR") as logs, quiet_sql_errors(pg_errors.SerializationFailure):
            sql_logger.error("bad query: %s\nERROR: %s", "UPDATE mine", conflict)
            sql_logger.error("bad query: %s\nERROR: %s", "SELEC mine", pg_errors.SyntaxError("syntax error"))
            other_thread = threading.Thread(
                target=sql_logger.error, args=("bad query: %s\nERROR: %s", "UPDATE theirs", conflict)
            )
            other_thread.start()
            other_thread.join()
        self.assertEqual([record.args[0] for record in logs.records], ["SELEC mine", "UPDATE theirs"])

    def test_the_client_gives_up_connecting_after_five_seconds(self):
        client = self.env["ai.claude.gateway"]._claude_client("test-key", 120)
        self.assertEqual((client.timeout.connect, client.timeout.read), (5, 120))
        self.assertEqual(client.max_retries, 0)

    def test_trace_line_shows_time_cost_and_one_line_of_error(self):
        answered = self._billed("claude-haiku-5-5", "answered", 812, self._usage(1000, 200, 3000, 400))
        self.assertEqual(answered.trace_line(), "claude-haiku-5-5: answered, 812 ms, $0.000280")
        failed = Attempt("claude-sonnet-5-5", "unavailable", 120000, "Request timed out\n  or   interrupted")
        self.assertEqual(
            failed.trace_line(),
            "claude-sonnet-5-5: unavailable, 120000 ms - Request timed out or interrupted",
            "an unbilled call shows no cost, and its error is one line",
        )
        self.assertEqual(
            Attempt("claude-haiku-5-5", "skipped", called=False).trace_line(),
            "claude-haiku-5-5: skipped",
            "a model never called has no time",
        )
        long_error = Attempt("claude-haiku-5-5", "bad_request", 3, "x" * 2000).trace_line()
        self.assertEqual(len(long_error), len("claude-haiku-5-5: bad_request, 3 ms - ") + 500)

    def test_trace_lists_every_attempt_in_order(self):
        outcome = ClaudeOutcome(
            attempts=[
                Attempt("claude-sonnet-5-5", "spend_limit", 80, "Error code: 429"),
                Attempt("claude-haiku-5-5", "skipped", called=False),
            ]
        )
        self.assertEqual(
            outcome.trace(), "claude-sonnet-5-5: spend_limit, 80 ms - Error code: 429\nclaude-haiku-5-5: skipped"
        )
        self.assertEqual(claude_trace_kinds(outcome.trace()), ["spend_limit", "skipped"])
        self.assertEqual(ClaudeOutcome().trace(), "")

    def test_billing_values_sum_every_billed_attempt_cache_included(self):
        refusal_usage = self._usage(20000, 10, 5000, 1000)
        answer_usage = self._usage(1000, 200, 3000, 400)
        outcome = ClaudeOutcome(
            attempts=[
                Attempt("claude-opus-5-5", "unavailable", 120000, "timed out"),
                self._billed("claude-sonnet-5-5", "refusal", 900, refusal_usage),
                self._billed("claude-haiku-5-5", "answered", 700, answer_usage),
            ]
        )
        billing = outcome.billing_values()
        self.assertEqual(
            {key: billing[key] for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")},
            {"input_tokens": 21000, "output_tokens": 210, "cache_read_tokens": 8000, "cache_write_tokens": 1400},
        )
        self.assertAlmostEqual(
            billing["cost"],
            claude_cost("claude-sonnet-5-5", refusal_usage) + claude_cost("claude-haiku-5-5", answer_usage),
            msg="the refused call was billed too",
        )
        self.assertEqual(
            ClaudeOutcome().billing_values(),
            {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "cost": 0},
        )


@tagged("post_install", "-at_install")
class TestStripMedia(AiClaudeCase):
    def test_media_go_and_the_text_stays(self):
        stripped = self.env["ai.claude.gateway"]._strip_media(
            "<div><p>Opening hours</p><img src='data:image/png;base64,iVBORw0KGgo='/>"
            "<picture><source srcset='a.webp'/></picture><svg><circle r='1'/></svg>"
            "<iframe src='https://example.com/map'></iframe><video src='a.mp4'></video></div>"
        )
        self.assertIn("Opening hours", stripped)
        for tag in ("<img", "<picture", "<source", "<svg", "<iframe", "<video", "base64"):
            self.assertNotIn(tag, stripped)

    def test_a_data_link_loses_its_payload_and_a_web_link_keeps_its_address(self):
        stripped = self.env["ai.claude.gateway"]._strip_media(
            "<p><a href='DATA:text/plain;base64,SGVsbG8='>file</a> <a href='https://example.com/jobs'>jobs</a></p>"
        )
        self.assertNotIn("base64", stripped)
        self.assertIn("file</a>", stripped)
        self.assertIn('href="https://example.com/jobs"', stripped)

    def test_an_unparsable_page_passes_through_whole(self):
        with self.assertLogs(GATEWAY_LOGGER, "WARNING") as logs:
            self.assertEqual(self.env["ai.claude.gateway"]._strip_media("   "), "   ")
        self.assertIn("could not parse a page to strip its media", logs.output[0])
        self.assertFalse(self.env["ai.claude.gateway"]._strip_media(False))
