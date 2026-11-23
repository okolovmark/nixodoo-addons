The Claude layer an Odoo assistant answers through, and the record an
administrator reviews its conversations on. It has no menu, parameter or
cron of its own: each assistant module ships its parameters, puts
*Conversations* in a menu and runs the incident check from a cron of its
own (see Usage). ``ai_assistant`` is one such assistant.

Every method takes the assistant's prefix and reads its parameters as
``<prefix>.<name>`` (see Configuration): each assistant has its own models,
key, limits, Claude breaker and incident state, and its calls count towards
no other assistant's. ``readme/claude_layer.puml`` draws the models and what
an assistant adds to them.

**The Claude chain** (``ai.claude.gateway``)

``_claude_chain(prefix, system, turns, ...)`` returns a ``ClaudeOutcome``:
the first answer of the assistant's ``claude_models``, asked in order through
the Anthropic Messages API, and every attempt. It never raises and writes
nothing. It asks no model when the models or the key are missing (no
attempt), when the assistant's breaker is open (an attempt *circuit_open*)
or when the ``anthropic`` package is missing (an attempt *config*). Leading
turns that are not a user question are dropped: the API wants a question
first.

Each call has the assistant's ``claude_effort``, ``claude_max_tokens`` and
``claude_timeout_seconds`` (a caller may give its own for one chain), 5
seconds to connect, no retries, no temperature and no thinking setting. An
``output_format`` (a JSON schema) makes the answer that JSON. The system
prompt carries a prompt-cache mark.

Each model tried ends in one kind, kept in its attempt:

* *answered*: the chain ends;
* *refusal*, *empty* or *truncated* (stopped at the token cap or the context
  window; a cut answer is never returned): the next model is asked;
* *unavailable* (no connection, a timeout, a rate limit, a server error),
  *config* (the key refused, 401 or 403) or *bad_request* (any other error):
  the next model is asked, and the call is a failure for the breaker;
* *spend_limit* (a 402 or billing error, a 429 whose error code or message
  names the spend limit, a 400 on usage limits): the models after it are
  *skipped*, since they share the workspace's limit;
* *tool_limit* (with tools only): the chain ends without an answer, since
  the next model would meet the same limit;
* *circuit_open*: the breaker, read again from the database before every
  model after the first, was opened by another question meanwhile.

**The tool loop**

With ``tools`` and a ``tool_executor(name, input)``, which returns the
content and whether it is an error, each model runs a loop. Every tool use
of a response is answered in one user turn, one result each; a tool that
raises becomes an error result. A ``pause_turn`` is sent back as it is. The
model is called again until it ends its turn, within
``claude_max_tool_rounds`` rounds and ``claude_tool_budget_seconds`` from its
first call; a tool asked for past the budget is not run, and its result says
so. A later model starts over from the original turns, after
``on_fallback()``, for the caller to set aside what the tools of the model
before did. The last tool definition and, from a loop's second call on, the
latest tool results carry a cache mark. The attempt counts the tool calls.

**The breaker**

``_claude_record(prefix, outcome)`` counts one question on the assistant's
breaker. An assistant runs it through ``_breaker_write``, in a short
transaction of its own, never the asker's; a write that meets a concurrent
one, or finds no free database connection, is dropped with a log line. A
*spend_limit* opens the breaker at once for ``circuit_cooldown_seconds``. A
question Claude answered, whose last call ended in *tool_limit*, or none of
whose calls failed, resets the count; any other question with a call adds
one, and ``circuit_threshold`` in a row open the breaker for the cooldown.

**Cost and deviations**

An attempt adds up the input, output, cache-read and cache-write tokens and
the cost of each billed call, from the price table ``CLAUDE_PRICES``: USD per
million tokens for ``claude-haiku-5-5``, ``claude-sonnet-5-5`` and
``claude-opus-5-5``; a ``claude-haiku-5-5`` call past 100,000 input tokens,
cache included, is billed whole at its higher rates. A model without a price
costs 0.

``_claude_deviations(outcome, fallback)`` gives the lines of what went off
the normal path: a model that did not answer, why, and where the question
went (the next Claude model, or else the words of
``_claude_not_answered_sentence(fallback)``: *no other model answers, so
nobody answered*); a spend limit that opened the breaker; one call's prompt
to a model with a price step over 80,000 input tokens, close to the step, or
past it; a model without a price.

**Answers as HTML**

``answer_to_html(text, base_url=None)`` escapes a model's answer, then
renders only bold, emphasis, code spans, links, headings (as a bold line),
bullet and numbered lists, paragraphs and line breaks; HTML the model writes
stays text. Without ``base_url`` every URL becomes a link; with it, only URLs
at its host and paths of the web client (``/web``, ``/odoo``), and a URL
whose host cannot be read stays text.
``answer_from_html`` turns a posted answer back into text with those marks,
for the history. ``_strip_media`` drops images, video, audio, SVG, frames,
embedded objects and the target of a ``data:`` link from HTML before it is
flattened into a prompt.

**Exchanges** (``ai.exchange.mixin``)

The fields of an assistant's exchange log: its AI conversation, the provider
(*Claude* when a Claude model answered), the model, the four token counts,
the cost of every billed Claude call, and *Attempts*, one line per model
tried with its kind, latency, tool calls, cost and error.

``_claude_incident_check(prefix, exchanges, assistant_name, recipients)``
mails the recipients once when Claude answered none of at least 3 of the
exchanges given, with the reasons found in their attempts and a link to one
of the conversations (``/odoo/ai.conversation/<id>``), and once more when
Claude answers again. The mail says
*another model answered them, or nothing did*. An exchange whose last Claude
step ended in *tool_limit* counts for neither; no exchange is silence. With
Claude not configured, an open incident closes without a mail. Without
recipients the mail is only logged.

**AI conversation** (``ai.conversation``)

One conversation with an assistant: its name, *Assistant*, last activity,
number of questions, fallbacks (questions whose first Claude model did not
answer them), models used, cost and transcript, and a chatter of deviations,
one note by OdooBot per question that left the normal path. Settings
administrators read and delete conversations and post notes on them; nobody
creates or edits one in the interface. The *Conversations* list, the newest
activity first, highlights the conversations with a fallback, which *With
fallbacks* filters; *Assistant* groups them.
