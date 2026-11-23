An assistant module depends on ``ai_claude`` and:

* ships its parameters under a prefix of its own, and seeds its breaker
  state, so that two first failures at once update one row;
* asks with ``self.env["ai.claude.gateway"]._claude_chain(prefix, system,
  turns, ...)``, then counts the outcome with
  ``_breaker_write("_claude_record", prefix, outcome)``;
* logs its questions on a model that inherits ``ai.exchange.mixin``: the
  outcome's ``log_values()`` for an answer, its ``billing_values()`` and
  ``trace()`` without one;
* adds its value to ``ai.conversation.assistant``, returns the exchanges of
  its conversations from ``_conversation_exchanges`` and their messages from
  ``_transcript_messages``, passing other conversations to ``super()``, and
  adds the depends of its exchanges to ``_compute_stats``, whose stored
  figures have none here;
* posts ``_claude_deviations(outcome)`` on the conversation with
  ``_post_deviations``;
* puts the action ``ai_claude.ai_conversation_action`` in a menu, and its own
  filter before the separator of the search view;
* runs ``_claude_incident_check`` from a cron of its own, over its last 30
  minutes of questions (``CLAUDE_INCIDENT_WINDOW_MINUTES``).

An assistant with a model of its own, asked after Claude, gives the words of
that fallback in ``_claude_not_answered_sentence`` and
``_claude_incident_body``, names that model's lines of *Attempts* in
``_claude_trace_skip_prefixes``, and leaves what is no question out of the
counts in ``_visitor_questions``.

Administrators read an assistant's conversations under the menu it gives:
the transcript, the *Answers* page the assistant fills, and the chatter of
deviations.
