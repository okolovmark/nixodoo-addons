This module installs no parameter: each assistant ships its own, under its
prefix, and usually a Settings section for them. Read per assistant:

* ``claude_models``: the Claude models, comma-separated, asked in order;
  empty, no model is asked.
* ``claude_key_env``: the name of the environment variable of the Odoo
  service that holds the Anthropic API key; the key is never read from the
  database. While the name or the variable is empty, no model is asked.
* ``claude_effort``: low, medium, high, xhigh or max; absent or any other
  value, medium.
* ``claude_max_tokens`` (8000), ``claude_timeout_seconds`` (120),
  ``claude_max_tool_rounds`` (12), ``claude_tool_budget_seconds`` (300),
  ``circuit_threshold`` (3) and ``circuit_cooldown_seconds`` (300): absent,
  or anything but a positive whole number, the value in brackets, or the
  assistant's own default where it gives one.

The module writes the breaker and incident state of each assistant:
``claude_consecutive_failures``, ``claude_circuit_open_until`` and
``claude_incident_open``.

Give each assistant a key of an Anthropic Console workspace of its own: the
workspace's monthly spend limit caps what that assistant costs. Odoo keeps no
budget.
