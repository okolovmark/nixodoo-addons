import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from lxml import etree
from lxml import html as lxml_html
from markupsafe import Markup, escape
from psycopg2 import errors as pg_errors
from psycopg2.pool import PoolError

from odoo import api, models
from odoo.tools.mail import create_link, html2plaintext, html_keep_url
from odoo.tools.translate import LazyTranslate

_logger = logging.getLogger(__name__)
_lt = LazyTranslate(__name__)

# USD per million tokens: input, output, cache write, cache read.
CLAUDE_PRICES = {
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
}
# Past this many input tokens (cache included) the whole call is billed at the
# second rates; the two cache rates are scaled from the published base rates.
CLAUDE_PRICE_STEPS = {"claude-haiku-5-5": (100000, (0.50, 2.50, 0.625, 0.05))}
CLAUDE_PRICE_STEP_WARNING = 80000

# Kinds that count towards the Claude breaker; a refusal, an empty or a cut
# answer is a working API.
CLAUDE_FAILURES = ("unavailable", "config", "bad_request")
SPEND_LIMIT_CODE = "enforced_spend_limit_reached"
# A 429 whose body carries no error code still names the limit in its message.
SPEND_LIMIT_TEXT = "spend limit"
# A cut answer is never posted, whatever text it has: the cut may fall inside
# a marker the assistant reads.
CLAUDE_TRUNCATED_STOPS = ("max_tokens", "model_context_window_exceeded")
CLAUDE_KIND_LABELS = {
    "spend_limit": _lt("spend limit reached"),
    "unavailable": _lt("Anthropic unavailable"),
    "config": _lt("API key or configuration"),
    "refusal": _lt("refusal"),
    "bad_request": _lt("error / bad request"),
    "empty": _lt("empty answer"),
    "truncated": _lt("answer cut off"),
    "circuit_open": _lt("Claude circuit breaker open"),
    "tool_limit": _lt("tool round or time limit reached"),
}
CLAUDE_NOBODY_ANSWERED = _lt("%(model)s did not answer (%(reason)s); no other model answers, so nobody answered.")
# The chatter line of a Claude model that did not answer, by where the question went next.
CLAUDE_NOT_ANSWERED = {
    "next_model": _lt("%(model)s did not answer (%(reason)s); fell to %(next_model)s."),
    "none": CLAUDE_NOBODY_ANSWERED,
}
# For a fallback the table does not know: where the question went is not known either.
CLAUDE_NOT_ANSWERED_DEFAULT = _lt("%(model)s did not answer (%(reason)s).")
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
# Content blocks the API refuses a cache breakpoint on.
UNCACHEABLE_BLOCKS = ("thinking", "redacted_thinking")
# The output_config effort values the Messages API accepts.
CLAUDE_EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")
# What a parameter is read as while it is absent; its Settings field shows the same.
CLAUDE_EFFORT = "medium"
CLAUDE_MAX_TOKENS = 8000
CLAUDE_TIMEOUT_SECONDS = 120
CLAUDE_MAX_TOOL_ROUNDS = 12
CLAUDE_TOOL_BUDGET_SECONDS = 300
CIRCUIT_THRESHOLD = 3
CIRCUIT_COOLDOWN_SECONDS = 300
# A dead network costs this per model, not the whole answer timeout.
CLAUDE_CONNECT_TIMEOUT = 5
BREAKER_CONFLICTS = (pg_errors.SerializationFailure, pg_errors.DeadlockDetected, pg_errors.UniqueViolation)
TRACE_LINE = re.compile(r"^(?P<who>.+?): (?P<kind>[a-z_]+)", re.MULTILINE)
# Dropped from HTML before it is flattened to text: their payloads are no text.
MEDIA_TAGS = ("img", "picture", "source", "video", "audio", "svg", "iframe", "embed", "object")


@dataclass
class Attempt:
    """One step of an answer: a model called, or skipped with its reason."""

    model: str
    kind: str
    latency_ms: int = 0
    error: str = ""
    usage: dict = field(default_factory=dict)
    called: bool = True
    cost: float = 0.0
    tool_calls: int = 0
    # The largest prompt of one call: a price step applies per call, not to the sum.
    peak_prompt_tokens: int = 0

    def add_call(self, usage):
        """Count one billed call: its tokens added to the attempt's, its cost priced on its own."""
        self.usage = {key: self.usage.get(key, 0) + usage.get(key, 0) for key in USAGE_KEYS}
        self.cost += claude_cost(self.model, usage) or 0.0
        self.peak_prompt_tokens = max(self.peak_prompt_tokens, claude_prompt_tokens(usage))

    def trace_line(self):
        """`model: kind[, ms][, N tool calls][, $cost][ - error]`, the shape claude_trace_kinds reads back."""
        line = f"{self.model}: {self.kind}"
        if self.called:
            line += f", {self.latency_ms} ms"
        if self.tool_calls:
            line += f", {self.tool_calls} tool call{'s' if self.tool_calls != 1 else ''}"
        if self.usage:
            line += f", ${self.cost:.6f}"
        if self.error:
            line += f" - {' '.join(self.error.split())[:500]}"
        return line


@dataclass
class ClaudeOutcome:
    """What the Claude chain did for one question: its answer, if any, and every step."""

    answer: str = ""
    model: str = ""
    attempts: list = field(default_factory=list)
    prompt_chars: int = 0

    @property
    def latency_ms(self):
        return sum(attempt.latency_ms for attempt in self.attempts)

    def trace(self):
        return "\n".join(attempt.trace_line() for attempt in self.attempts)

    def billing_values(self):
        """Tokens and cost of every billed attempt, the one that answered or not."""
        values = {key: sum(attempt.usage.get(key, 0) for attempt in self.attempts) for key in USAGE_KEYS}
        values["cost"] = sum(attempt.cost for attempt in self.attempts)
        return values

    def log_values(self):
        """The exchange values of a question Claude answered."""
        return {
            "provider": "claude",
            "model": self.model,
            **self.billing_values(),
            "latency_ms": self.latency_ms,
            "prompt_chars": self.prompt_chars,
            "attempts": self.trace(),
        }


def positive_int(raw_value, default):
    """`raw_value` as a positive integer; `default` for anything else."""
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def claude_prompt_tokens(usage):
    """Input tokens of one call, cache reads and writes included: what a price step is measured on."""
    return usage.get("input_tokens", 0) + usage.get("cache_read_tokens", 0) + usage.get("cache_write_tokens", 0)


def claude_cost(model, usage):
    """USD of one call, or None for a model without a price."""
    prices = CLAUDE_PRICES.get(model)
    if not prices:
        return None
    step, step_prices = CLAUDE_PRICE_STEPS.get(model, (None, None))
    if step and claude_prompt_tokens(usage) > step:
        prices = step_prices
    input_price, output_price, cache_write_price, cache_read_price = prices
    return (
        usage.get("input_tokens", 0) * input_price
        + usage.get("output_tokens", 0) * output_price
        + usage.get("cache_write_tokens", 0) * cache_write_price
        + usage.get("cache_read_tokens", 0) * cache_read_price
    ) / 1_000_000


def claude_trace_kinds(trace, skip_prefixes=()):
    """The kinds of the Claude steps of an exchange's attempts, in order: the lines of a model whose name starts
    with one of `skip_prefixes` are another model's."""
    return [match["kind"] for match in TRACE_LINE.finditer(trace or "") if not match["who"].startswith(skip_prefixes)]


def response_usage(response):
    """The tokens of one Messages API response, by USAGE_KEYS."""
    return {
        "input_tokens": response.usage.input_tokens or 0,
        "output_tokens": response.usage.output_tokens or 0,
        "cache_read_tokens": response.usage.cache_read_input_tokens or 0,
        "cache_write_tokens": response.usage.cache_creation_input_tokens or 0,
    }


def block_type(block):
    """The type of a content block, a dict we built or a block the SDK returned."""
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def block_chars(block):
    """Characters of a content block's text, tool input or tool result."""
    get = block.get if isinstance(block, dict) else lambda key: getattr(block, key, None)
    value = get("text") or get("content") or get("input") or ""
    return len(value if isinstance(value, str) else json.dumps(value, default=str))


def turn_chars(turn):
    """Characters of a turn, whose content is a string or a list of content blocks."""
    content = turn["content"]
    if isinstance(content, str):
        return len(content)
    return sum(block_chars(block) for block in content)


def cache_marked(turns):
    """A copy of the turns with a cache breakpoint on the last block of the last one that may carry it (a
    thinking block may not): one such mark at a time, beside the system's and the tools'. An SDK block, as
    a paused turn sends back, is marked on the dict the SDK would send for it."""
    last_turn = turns[-1]
    content = last_turn["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    index = next(
        (index for index in reversed(range(len(blocks))) if block_type(blocks[index]) not in UNCACHEABLE_BLOCKS),
        None,
    )
    if index is None:
        return turns
    block = blocks[index] if isinstance(blocks[index], dict) else blocks[index].to_dict(mode="json")
    blocks[index] = {**block, "cache_control": {"type": "ephemeral"}}
    return [*turns[:-1], {**last_turn, "content": blocks}]


class ThreadErrorFilter(logging.Filter):
    """Drops the records of the thread that made it whose arguments hold one of `errors`."""

    def __init__(self, errors):
        super().__init__()
        self.errors = errors
        self.thread = threading.get_ident()

    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        return not (record.thread == self.thread and any(isinstance(arg, self.errors) for arg in args))


@contextmanager
def quiet_sql_errors(*errors):
    """Keep odoo.sql_db from logging a query of this thread that fails with one of `errors`: a conflict the
    caller expects and handles. Any other failure, and other threads, still log."""
    sql_logger = logging.getLogger("odoo.sql_db")
    error_filter = ThreadErrorFilter(errors)
    sql_logger.addFilter(error_filter)
    try:
        yield
    finally:
        sql_logger.removeFilter(error_filter)


def is_question_turn(turn):
    """Whether a turn may open the messages: a user turn without tool results, whose tool use would come before it."""
    if turn["role"] != "user":
        return False
    content = turn["content"]
    return isinstance(content, str) or all(block_type(block) != "tool_result" for block in content)


# The few marks the model writes, matched on already-escaped text. Slash
# emphasis opens only after a space, "(" or "*", so URLs and paths keep theirs.
CODE_SPAN = re.compile(r"`([^`\n]+)`")
EMPHASIS = re.compile(r"(?<![^\s(*])/(?=\S)([^/\n]*?\S)/(?![\w/])")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# Applied after BOLD; never next to a word or a star (2*3*4).
STAR_EMPHASIS = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]*?\S)\*(?![\w*])")
BULLET_LINE = re.compile(r"^\s*[*\-\u2022]\s+(.*)$")
NUMBERED_LINE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
HEADING_LINE = re.compile(r"^\s*#{1,6}\s+(.*)$")
# An absolute URL, or a path into the web client, in escaped text; the punctuation that ends
# a sentence is no part of it.
ANSWER_URL = re.compile(r"""https?://[^\s<"']+|(?<![\w/.:@-])/(?:web|odoo)(?![\w.-])(?:[#?/][^\s<"']*)?""")
URL_TRAILING_PUNCTUATION = re.compile(r"[.,;:!?)]+$")


def _odoo_links(text, base_url):
    """The escaped text with a link for each URL into Odoo, at `base_url`'s host or a path of the web client;
    any other URL stays text."""
    base_host = urlsplit(base_url).netloc

    def link(match):
        url = match.group(0)
        trailing = URL_TRAILING_PUNCTUATION.search(url)
        if trailing:
            url = url[: trailing.start()]
        try:
            is_odoo_url = url.startswith("/") or urlsplit(url).netloc == base_host
        except ValueError:  # urlsplit refuses a bracketed non-IP host and full-width delimiters (CJK text)
            is_odoo_url = False
        if is_odoo_url:
            return create_link(url, url) + match.group(0)[len(url) :]
        return match.group(0)

    return ANSWER_URL.sub(link, text)


def _answer_inline(line, base_url=None):
    parts = CODE_SPAN.split(line)
    for index, part in enumerate(parts):
        if index % 2:
            parts[index] = f"<code>{part}</code>"
        else:
            marked = STAR_EMPHASIS.sub(r"<i>\1</i>", BOLD.sub(r"<b>\1</b>", EMPHASIS.sub(r"<i>\1</i>", part)))
            parts[index] = html_keep_url(marked) if base_url is None else _odoo_links(marked, base_url)
    return "".join(parts)


def _answer_line(line):
    """(block kind, number of a numbered item, content) of one line."""
    match = BULLET_LINE.match(line)
    if match:
        return "ul", None, match.group(1)
    match = NUMBERED_LINE.match(line)
    if match:
        return "ol", int(match.group(1)), match.group(2)
    match = HEADING_LINE.match(line)
    if match:
        heading = match.group(1).strip()
        inner = heading[2:-2]
        if heading.startswith("**") and heading.endswith("**") and inner and "**" not in inner:
            heading = inner  # a heading is rendered bold already
        return "h", None, heading
    return "p", None, line


def _answer_block(kind, items, start):
    if kind == "p":
        return "<p>%s</p>" % "<br/>".join(items)
    if kind == "h":
        return "".join(f"<p><b>{item}</b></p>" for item in items)
    opening = f'<ol start="{start}">' if kind == "ol" and start != 1 else f"<{kind}>"
    return "%s%s</%s>" % (opening, "".join(f"<li>{item}</li>" for item in items), kind)


def answer_to_html(text, base_url=None):
    """The model's answer as HTML: everything escaped, then only a few marks rendered.

    Bold, emphasis, code spans, links, headings (as a bold line), bullet and
    numbered lists, paragraphs and line breaks. Anything else the model writes,
    HTML included, stays text. A numbered list split by a blank line keeps the
    model's numbers, and an indented line under an item stays in that item.

    With `base_url`, only URLs into that Odoo become links, and paths of its
    web client: an answer steered by text injected into a record cannot hand
    the employee a one-click link to anywhere else.
    """
    html = []
    for block in re.split(r"\n\s*\n", str(escape(str(text or "").strip()))):
        kind, start, items = None, None, []
        for line in block.split("\n"):
            line_kind, number, content = _answer_line(line)
            if line_kind == "p" and kind in ("ul", "ol") and line[:1].isspace():
                items[-1] += "<br/>" + _answer_inline(content.strip(), base_url)
                continue
            if line_kind != kind and items:
                html.append(_answer_block(kind, items, start))
                items = []
            if not items:
                start = number
            kind = line_kind
            items.append(_answer_inline(content, base_url))
        if items:
            html.append(_answer_block(kind, items, start))
    return Markup("".join(html))


INLINE_MARKS = {"b": "**", "i": "*", "code": "`"}
INLINE_TAG = re.compile(r"</?(b|i|code)>")
LIST = re.compile(r"<(ul|ol)\b([^>]*)>(.*?)</\1>", re.DOTALL)
LIST_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", re.DOTALL)
LIST_START = re.compile(r'\bstart="(\d+)"')
BARE_LINK = re.compile(r"<a\b[^>]*\bhref=\"([^\"]+)\"[^>]*>\1</a>")


def _list_lines(match):
    kind, attributes, body = match.groups()
    items = LIST_ITEM.findall(body)
    if kind == "ul":
        return "<p>%s</p>" % "<br/>".join(f"- {item}" for item in items)
    start = LIST_START.search(attributes)
    first = int(start.group(1)) if start else 1
    return "<p>%s</p>" % "<br/>".join(f"{number}. {item}" for number, item in enumerate(items, first))


def answer_from_html(body):
    """A posted answer as text, with the marks and links answer_to_html renders written back.

    It goes back to the model in the history, and the model copies the style of
    its earlier turns: html2plaintext would show bold as a single star and run
    list items into one line.
    """
    html = BARE_LINK.sub(r"\1", LIST.sub(_list_lines, str(body or "")))
    return html2plaintext(INLINE_TAG.sub(lambda match: INLINE_MARKS[match.group(1)], html)).strip()


# The employees' assistants end an answer with it when their sources do not cover the question.
NOT_COVERED = "[[NOT_COVERED]]"
# Thumbs-down first: when both are on an answer, it is the one worth a review.
REACTION_FEEDBACK = (("\U0001f44e", "down"), ("\U0001f44d", "up"))


def split_not_covered(answer):
    """(text, has_marker): the marker removed, or a start of it cut off by the answer budget."""
    if NOT_COVERED in answer:
        return answer.replace(NOT_COVERED, "").strip(), True
    text = answer.rstrip()
    for size in range(len(NOT_COVERED) - 1, 1, -1):
        if text.endswith(NOT_COVERED[:size]):
            return text[:-size].strip(), True
    return answer, False


class AiClaudeGateway(models.AbstractModel):
    _name = "ai.claude.gateway"
    _description = "Client for the Claude Messages API"

    @api.model
    def _breaker_write(self, method, *args):
        """Run a breaker write in a short transaction of its own, never in the asker's.

        The asker's transaction stays open through the model calls, and a
        conflict in it makes Odoo replay the whole request, Claude's bill
        included. Here a conflict loses one count, which the next question
        makes up; so does a full connection pool.
        """
        try:
            with quiet_sql_errors(*BREAKER_CONFLICTS), self.env.registry.cursor() as cr:
                fresh_gateway = self.with_env(self.env(cr=cr))
                getattr(fresh_gateway, method)(*args)
        except BREAKER_CONFLICTS as error:
            _logger.info("ai claude: %s met a concurrent breaker write and was dropped: %s", method, error)
        except PoolError as error:
            _logger.warning("ai claude: %s found no free database connection and was dropped: %s", method, error)

    # Each assistant reads its own parameters, by prefix, and keeps its own Claude breaker.
    @api.model
    def _assistant_param(self, prefix, suffix, default=""):
        # Read as text and parsed by the callers: get_int and get_float log a warning on each malformed value.
        return self.env["ir.config_parameter"].sudo().get_str(f"{prefix}.{suffix}", default)

    @api.model
    def _assistant_int_param(self, prefix, suffix, default):
        """The parameter as a positive integer; `default` while it is absent or holds anything else."""
        return positive_int(self._assistant_param(prefix, suffix, default), default)

    @api.model
    def _claude_effort(self, prefix):
        """The assistant's effort; CLAUDE_EFFORT for a value the API would reject."""
        effort = self._assistant_param(prefix, "claude_effort", CLAUDE_EFFORT).strip()
        return effort if effort in CLAUDE_EFFORT_VALUES else CLAUDE_EFFORT

    @api.model
    def _claude_models(self, prefix):
        return [name.strip() for name in self._assistant_param(prefix, "claude_models").split(",") if name.strip()]

    @api.model
    def _claude_key(self, prefix):
        """The key from the environment variable claude_key_env names, never from the database,
        which travels with every prod dump to develop and the test boxes."""
        env_name = self._assistant_param(prefix, "claude_key_env")
        return os.environ.get(env_name, "") if env_name else ""

    @api.model
    def _claude_configured(self, prefix):
        return bool(self._claude_models(prefix) and self._claude_key(prefix))

    @api.model
    def _claude_circuit_open(self, prefix):
        try:
            return time.time() < float(self._assistant_param(prefix, "claude_circuit_open_until", 0) or 0)
        except ValueError:
            return False

    @api.model
    def _claude_circuit_open_now(self, prefix):
        """The breaker as committed now, past this transaction's snapshot and the
        parameter cache: another question may have opened it during a call. With
        no free connection, the breaker as this transaction saw it."""
        try:
            with self.env.registry.cursor() as cr:
                cr.execute(
                    "SELECT value FROM ir_config_parameter WHERE key = %s", (f"{prefix}.claude_circuit_open_until",)
                )
                row = cr.fetchone()
        except PoolError:
            return self._claude_circuit_open(prefix)
        try:
            return time.time() < float((row and row[0]) or 0)
        except ValueError:
            return False

    @api.model
    def _claude_usable(self, prefix):
        return self._claude_configured(prefix) and not self._claude_circuit_open(prefix)

    @api.model
    def _claude_record(self, prefix, outcome):
        """Count one question's Claude calls on the assistant's breaker; a spend limit opens it at once.

        A question Claude answered, or whose last model ran out of tool rounds
        or time (Claude answered its calls), resets the count, whatever the
        models before it did.
        """
        calls = [attempt for attempt in outcome.attempts if attempt.called]
        if not calls:
            return
        icp = self.env["ir.config_parameter"].sudo()
        cooldown = self._assistant_int_param(prefix, "circuit_cooldown_seconds", CIRCUIT_COOLDOWN_SECONDS)
        if any(attempt.kind == "spend_limit" for attempt in calls):
            icp.set_str(f"{prefix}.claude_circuit_open_until", str(time.time() + cooldown))
            _logger.info("%s: the Claude spend limit opened the breaker for %ss", prefix, cooldown)
            return
        claude_worked = outcome.answer or calls[-1].kind == "tool_limit"
        if claude_worked or not any(attempt.kind in CLAUDE_FAILURES for attempt in calls):
            # Written only on a change: a write clears every worker's parameter cache.
            if self._assistant_int_param(prefix, "claude_consecutive_failures", 0):
                icp.set_str(f"{prefix}.claude_consecutive_failures", "0")
                icp.set_str(f"{prefix}.claude_circuit_open_until", "0")
            return
        failures = self._assistant_int_param(prefix, "claude_consecutive_failures", 0) + 1
        icp.set_str(f"{prefix}.claude_consecutive_failures", str(failures))
        if failures >= self._assistant_int_param(prefix, "circuit_threshold", CIRCUIT_THRESHOLD):
            icp.set_str(f"{prefix}.claude_circuit_open_until", str(time.time() + cooldown))
            _logger.info("%s: the Claude breaker opened for %ss after %s failures", prefix, cooldown, failures)

    @api.model
    def _claude_client(self, api_key, timeout):
        """The Anthropic client, in a method of its own for the tests to replace."""
        # Installing or updating the module needs the package; imported here so a
        # server whose environment lost it still loads, and the chain skips Claude.
        import anthropic  # pylint: disable=import-outside-toplevel

        return anthropic.Anthropic(
            api_key=api_key, timeout=anthropic.Timeout(timeout, connect=CLAUDE_CONNECT_TIMEOUT), max_retries=0
        )

    @api.model
    def _claude_error_kind(self, exc):
        import anthropic  # pylint: disable=import-outside-toplevel

        if isinstance(exc, anthropic.APIConnectionError):
            return "unavailable"
        if not isinstance(exc, anthropic.APIStatusError):
            return "bad_request"
        status = exc.status_code
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        details = error.get("details") or body.get("details")
        if status == 402 or exc.type == "billing_error":
            return "spend_limit"
        if status == 429:
            message = str(exc)
            is_spend_limit = (
                (isinstance(details, dict) and details.get("error_code") == SPEND_LIMIT_CODE)
                or SPEND_LIMIT_CODE in message
                or SPEND_LIMIT_TEXT in message.lower()
            )
            return "spend_limit" if is_spend_limit else "unavailable"
        if status == 400:
            return "spend_limit" if "usage limits" in str(exc).lower() else "bad_request"
        if status in (401, 403):
            return "config"
        return "unavailable" if status >= 500 else "bad_request"

    @api.model
    def _claude_call(
        self,
        client,
        model,
        system,
        turns,
        max_tokens,
        effort,
        tools=None,
        tool_executor=None,
        max_tool_rounds=CLAUDE_MAX_TOOL_ROUNDS,
        tool_budget_seconds=CLAUDE_TOOL_BUDGET_SECONDS,
        output_format=None,
    ):
        """(answer, attempt) of one model. Never raises.

        Without a tool executor, one Messages API call. With one, the tool
        loop: a tool_use turn is answered with all its tool results in one
        user turn, a pause_turn is sent back as it is, and the model is called
        again until it ends its turn. A tool turn past max_tool_rounds, or a
        call past tool_budget_seconds since the first, ends the attempt as
        tool_limit; a tool asked for past the budget is not run. The attempt
        adds up the tokens and cost of every call. An `output_format` (a
        json_schema format) makes the answer that JSON.

        No temperature (these models refuse a non-default one) and no thinking
        (adaptive is their default): effort is the only dial.
        """
        output_config = {"effort": effort}
        if output_format:
            output_config["format"] = output_format
        request = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": output_config,
        }
        if tools:
            # Tools render before the system prompt: the mark caches them for every question.
            request["tools"] = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        started = time.time()
        attempt = Attempt(model, "answered")
        answer, messages, rounds = "", turns, 0
        try:
            while True:
                if rounds and time.time() - started >= tool_budget_seconds:
                    attempt.kind = "tool_limit"
                    attempt.error = f"the tool loop ran past its {tool_budget_seconds} s budget"
                    break
                # From the second call on, the latest tool results carry a cache mark: each call reads the
                # rounds before it from the cache; a question answered in one call writes none.
                response = client.messages.create(**request, messages=cache_marked(messages) if rounds else messages)
                attempt.add_call(response_usage(response))
                tool_uses = [block for block in response.content if block.type == "tool_use"]
                is_tool_turn = response.stop_reason == "pause_turn" or (
                    response.stop_reason == "tool_use" and bool(tool_uses)
                )
                if not (tool_executor and is_tool_turn):
                    answer, attempt.kind, attempt.error = self._claude_answer(response)
                    break
                if rounds >= max_tool_rounds:
                    attempt.kind = "tool_limit"
                    attempt.error = f"the model asked for more than {max_tool_rounds} rounds of tools"
                    break
                rounds += 1
                messages = [*messages, {"role": "assistant", "content": response.content}]
                if tool_uses:
                    results = self._claude_tool_results(tool_uses, tool_executor, started + tool_budget_seconds)
                    messages.append({"role": "user", "content": results})
                    attempt.tool_calls += len(tool_uses)
        except Exception as exc:  # noqa: BLE001 -- any failure means "next model", never "break the chat"
            attempt.kind, attempt.error = self._claude_error_kind(exc), str(exc)
        attempt.latency_ms = int((time.time() - started) * 1000)
        return answer, attempt

    @api.model
    def _claude_answer(self, response):
        """(answer, kind, error) of a response that ends the model's turn."""
        answer = "".join(block.text for block in response.content if block.type == "text").strip()
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            reason = filter(None, [getattr(details, "category", None), getattr(details, "explanation", None)])
            return "", "refusal", " ".join(reason)
        if response.stop_reason in CLAUDE_TRUNCATED_STOPS:
            return "", "truncated", f"stop reason {response.stop_reason}"
        if not answer:
            return "", "empty", f"no text, stop reason {response.stop_reason}"
        return answer, "answered", ""

    @api.model
    def _claude_tool_results(self, tool_uses, tool_executor, deadline=None):
        """One tool_result block per tool_use block, in order: `tool_executor(name, input)` gives (content, is_error).
        Past the `deadline` (a time.time()), a tool is not run and its result says so."""
        results = []
        for block in tool_uses:
            if deadline is not None and time.time() >= deadline:
                content, is_error = "Not run: the time for this answer's tools is spent.", True
            else:
                try:
                    content, is_error = tool_executor(block.name, block.input)
                except Exception as exc:  # noqa: BLE001 -- a broken tool is an error result the model can work around
                    _logger.warning("ai claude: the tool %s raised", block.name, exc_info=True)
                    content, is_error = f"The tool failed: {type(exc).__name__}: {exc}"[:500], True
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})
        return results

    @api.model
    def _claude_chain(
        self,
        prefix,
        system,
        turns,
        tools=None,
        tool_executor=None,
        on_fallback=None,
        max_tokens=None,
        effort=None,
        timeout=None,
        output_format=None,
    ):
        """The first answer of the assistant's Claude models, in their order. Never raises, writes nothing.

        Skipped as a whole without a key (silently), with the breaker open or
        without the anthropic package; a spend limit skips the models after
        it, which share the workspace's limit. Any other model is the
        caller's: each assistant reaches it its own way. The outcome's
        prompt_chars counts the system prompt and the turns sent.
        `max_tokens`, `effort` and `timeout` replace the assistant's own
        parameters for this call; `output_format` goes to `_claude_call`.

        With a tool executor each model runs the tool loop of `_claude_call`
        under the assistant's claude_max_tool_rounds and
        claude_tool_budget_seconds, from the original turns. A model that hits
        either limit ends the chain without an answer: the next model would
        meet the same question and the same limit. `on_fallback()` runs before
        a later model starts over, for the caller to set aside what the tools
        of the one before did.
        """
        outcome = ClaudeOutcome()
        if not self._claude_configured(prefix):
            return outcome
        if self._claude_circuit_open(prefix):
            return self._claude_circuit_open_outcome()
        try:
            client = self._claude_client(
                self._claude_key(prefix),
                timeout or self._assistant_int_param(prefix, "claude_timeout_seconds", CLAUDE_TIMEOUT_SECONDS),
            )
        except ImportError:
            outcome.attempts.append(
                Attempt("Claude", "config", error="the anthropic package is not installed", called=False)
            )
            return outcome
        # The API wants a user question first; an assistant's greeting may open the chat.
        while turns and not is_question_turn(turns[0]):
            turns = turns[1:]
        outcome.prompt_chars = len(system) + sum(turn_chars(turn) for turn in turns)
        max_tokens = max_tokens or self._assistant_int_param(prefix, "claude_max_tokens", CLAUDE_MAX_TOKENS)
        effort = effort or self._claude_effort(prefix)
        tool_loop = {}
        if tool_executor:
            tool_loop = {
                "tool_executor": tool_executor,
                "max_tool_rounds": self._assistant_int_param(prefix, "claude_max_tool_rounds", CLAUDE_MAX_TOOL_ROUNDS),
                "tool_budget_seconds": self._assistant_int_param(
                    prefix, "claude_tool_budget_seconds", CLAUDE_TOOL_BUDGET_SECONDS
                ),
            }
        models = self._claude_models(prefix)
        for index, model in enumerate(models):
            if index and self._claude_circuit_open_now(prefix):
                outcome.attempts.append(Attempt("Claude", "circuit_open", called=False))
                break
            if index and on_fallback:
                on_fallback()
            answer, attempt = self._claude_call(
                client, model, system, turns, max_tokens, effort, tools, output_format=output_format, **tool_loop
            )
            outcome.attempts.append(attempt)
            if answer:
                outcome.answer, outcome.model = answer, model
                break
            if attempt.kind == "tool_limit":
                break
            if attempt.kind == "spend_limit":
                outcome.attempts.extend(Attempt(name, "skipped", called=False) for name in models[index + 1 :])
                break
        return outcome

    @api.model
    def _claude_circuit_open_outcome(self):
        return ClaudeOutcome(attempts=[Attempt("Claude", "circuit_open", called=False)])

    @api.model
    def _claude_deviations(self, outcome, fallback="none"):
        """The lines a conversation's chatter gets for one question; none for a plain Claude answer.

        `fallback` says where a question no Claude model answered went, in the
        words of `_claude_not_answered_sentence`.
        """
        lines = []
        for index, attempt in enumerate(outcome.attempts):
            if attempt.usage and attempt.model in CLAUDE_PRICE_STEPS:
                tokens = attempt.peak_prompt_tokens or claude_prompt_tokens(attempt.usage)
                step = CLAUDE_PRICE_STEPS[attempt.model][0]
                values = {"model": attempt.model, "tokens": tokens, "step": step}
                if tokens > step:
                    lines.append(
                        self.env._(
                            "The prompt to %(model)s was %(tokens)s input tokens, past its %(step)s-token price step.",
                            **values,
                        )
                    )
                elif tokens > CLAUDE_PRICE_STEP_WARNING:
                    lines.append(
                        self.env._(
                            "The prompt to %(model)s was %(tokens)s input tokens, close to its %(step)s-token price step.",
                            **values,
                        )
                    )
            if attempt.usage and attempt.model not in CLAUDE_PRICES:
                lines.append(self.env._("No price is known for %s: its cost is recorded as 0.", attempt.model))
            if attempt.kind in ("answered", "skipped"):
                continue
            later_model = next((later.model for later in outcome.attempts[index + 1 :] if later.called), None)
            label = CLAUDE_KIND_LABELS.get(attempt.kind)
            reason = self.env._(label) if label else attempt.kind
            if attempt.error:
                reason = f"{reason}: {' '.join(attempt.error.split())[:500]}"
            sentence = self._claude_not_answered_sentence("next_model" if later_model else fallback)
            lines.append(self.env._(sentence) % {"model": attempt.model, "reason": reason, "next_model": later_model})
            if attempt.kind == "spend_limit":
                lines.append(
                    self.env._(
                        "The spend limit opened the Claude breaker: the next questions skip Claude until it closes."
                    )
                )
        return lines

    @api.model
    def _claude_not_answered_sentence(self, fallback):
        """The sentence, with %(model)s, %(reason)s and %(next_model)s, of a Claude model that did not answer:
        "next_model" when a later Claude model was asked, "none" when no other model answers. An assistant with
        a model of its own adds the words of its fallbacks."""
        return CLAUDE_NOT_ANSWERED.get(fallback, CLAUDE_NOT_ANSWERED_DEFAULT)

    @api.model
    def _strip_media(self, raw_html):
        """Drop media payloads before the HTML is flattened to text.

        html2plaintext does not discard images: it turns every <img> into a
        placeholder and appends its src to a link index at the end of the text.
        An image pasted into the editor is stored inline as a base64 data: URI,
        so the whole binary would arrive in the prompt. Links are kept, except
        when the href is itself a data: URI, a payload wearing a link's clothes.
        """
        if not raw_html:
            return raw_html
        try:
            tree = lxml_html.fromstring(raw_html)
        except (etree.ParserError, etree.XMLSyntaxError, ValueError):
            _logger.warning("ai claude: could not parse a page to strip its media, passing it through whole")
            return raw_html
        for element in list(tree.iter(*MEDIA_TAGS)):
            element.drop_tree()
        for link in list(tree.iter("a")):
            if (link.get("href") or "").lower().startswith("data:"):
                del link.attrib["href"]
        return lxml_html.tostring(tree, encoding="unicode")
