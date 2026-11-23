import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import anthropic
import httpx2

from odoo.tests import TransactionCase

PREFIX = "ai_claude_test"
KEY_ENV = "AI_CLAUDE_TEST_KEY"
API_URL = "https://api.anthropic.com/v1/messages"
GATEWAY_LOGGER = "odoo.addons.ai_claude.models.ai_claude_gateway"


def claude_message(
    text, stop_reason="end_turn", input_tokens=1000, output_tokens=200, cache_read=0, cache_write=0, stop_details=None
):
    """A Messages API response with only the attributes the gateway reads."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)] if text else [],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def api_error(error_cls, status, message, body=None):
    request = httpx2.Request("POST", API_URL)
    return error_cls(message, response=httpx2.Response(status, request=request), body=body)


def api_timeout():
    return anthropic.APITimeoutError(request=httpx2.Request("POST", API_URL))


def spend_limit_429():
    return api_error(
        anthropic.RateLimitError,
        429,
        "Error code: 429 - spend limit reached",
        body={
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": "spend limit reached",
                "details": {"error_code": "enforced_spend_limit_reached"},
            },
        },
    )


def tool_use(name, tool_input, tool_id="toolu_1"):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


def text(value):
    return SimpleNamespace(type="text", text=value)


def claude_turn(blocks, stop_reason, input_tokens=1000, output_tokens=100, cache_read=0, cache_write=0):
    """A Messages API response with any content blocks."""
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        stop_details=None,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def usage(input_tokens=1000, output_tokens=100, cache_read=0, cache_write=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    }


class AiClaudeCase(TransactionCase):
    """An assistant of prefix PREFIX with one Claude model, its breaker closed and no key."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A key in the developer's environment must never reach Anthropic from a test.
        cls.startClassPatcher(patch.dict(os.environ))
        os.environ.pop(KEY_ENV, None)
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_str(f"{PREFIX}.claude_models", "claude-haiku-5-5")
        icp.set_str(f"{PREFIX}.claude_key_env", KEY_ENV)
        for key in ("claude_consecutive_failures", "claude_circuit_open_until"):
            icp.set_str(f"{PREFIX}.{key}", "0")
        cls.gateway = cls.env["ai.claude.gateway"]

    def setUp(self):
        super().setUp()
        # The breakers are written through cursors of their own; in test mode
        # they stay inside the test transaction and are rolled back with it.
        self.enterContext(self.registry_test_mode())

    def _param(self, key):
        return self.env["ir.config_parameter"].sudo().get_str(f"{PREFIX}.{key}")

    @contextmanager
    def _claude(self, *results):
        """Claude configured with a key, its client returning or raising `results` in turn."""
        create = Mock(side_effect=list(results))
        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with (
            patch.dict(os.environ, {KEY_ENV: "test-key"}),
            patch.object(type(self.gateway), "_claude_client", return_value=client) as factory,
        ):
            yield SimpleNamespace(create=create, factory=factory)

    def _ask(self, question="hi"):
        """One question through the chain, counted on the breaker as an assistant counts it."""
        outcome = self.gateway._claude_chain(PREFIX, "system", [{"role": "user", "content": question}])
        self.gateway._breaker_write("_claude_record", PREFIX, outcome)
        return outcome
