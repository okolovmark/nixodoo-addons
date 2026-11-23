*Internal User* implies the hidden group *AI Assistant*
(``ai_assistant.group_ai_assistant``): every internal user has the
app, the systray panel and *Ask AI*. The app's *Review* and *Configuration*
menus, the *Fixed*, *Dismiss* and *Send new answer* buttons, every
conversation and every AI report are for Settings administrators.

*AI Assistant > Configuration > Settings* opens the *AI Assistant* block of
the Settings app, also listed there. Its section *AI Assistant* sets the
parameters below, prefix ``ai_assistant.``. A value in brackets is installed
with the module only: an update neither brings back an emptied parameter
nor adds one new to the data file, which takes a migration. An emptied
text field or effort removes its parameter; its default then applies and
the form shows it: the installed value, but nothing for ``claude_models``
and ``claude_key_env``. A number must be above zero: the form refuses 0 or
less, and an emptied number field is 0. A number parameter set in *System
Parameters* to 0 or less, or to anything but a whole number (any number for
``tool_seconds``), is read as its default, and a ``claude_effort`` the API
does not accept as medium; the form shows what is read. Saving any Settings
page writes the default shown for an absent parameter.

* *Claude*: ``claude_models`` (``claude-sonnet-5-5,claude-haiku-5-5``), the
  Claude models, comma-separated, asked in order; empty, the AI Assistant
  does not answer. ``claude_key_env`` (``ANTHROPIC_API_KEY``), the
  name of the environment variable of the Odoo service that holds the
  Anthropic API key; the key is never stored in the database. While the
  variable is unset or empty, the AI Assistant does not answer.
  ``claude_effort`` (medium; low, medium, high, xhigh or max),
  ``claude_max_tokens`` (16000) and ``claude_timeout_seconds`` (120): per
  Claude call. ``claude_max_tokens`` caps the whole reply, an AI report's
  page and bindings included.
* *Tools*: ``claude_max_tool_rounds`` (12) and
  ``claude_tool_budget_seconds`` (300), one model's tool loop;
  ``tool_seconds`` (15), the statement timeout of one tool call, fractions
  allowed; ``max_rows`` (200), the most rows or groups one tool call
  returns, and the most a report dataset holds.
* *Circuit breaker*: ``circuit_threshold`` and ``circuit_cooldown_seconds``,
  the AI Assistant's Claude breaker, not installed; absent, 3 failed
  questions in a row and 300 seconds.
* *Answers and AI reports*: ``pending_stale_seconds`` (1800), a question
  still pending this long is closed as *Unavailable*;
  ``report_max_tokens`` (200000), the most data, in estimated tokens, the
  report tools hand the model.
* *AI deny list*: ``deny_models_extra`` and ``deny_fields_extra`` (empty),
  comma-separated fnmatch patterns added to the AI deny list, models as
  ``x_salary*`` and fields as ``model.field``, such as
  ``sale.order.x_margin*``. They only add to the list.
* *Notifications*: ``incident_emails`` (empty), recipients of the mails on
  Claude not answering the AI Assistant, empty: nobody is mailed
  (``ai.assistant.exchange._incident_recipients`` may name others);
  ``digest_recipients`` (empty),
  recipients of the weekly digest, empty: it is only written to the server
  log. Both comma-separated.

The breaker and incident state, ``claude_consecutive_failures``,
``claude_circuit_open_until`` and ``claude_incident_open``, is written by
the module and is in *Settings > Technical > Parameters > System
Parameters* (developer mode) only.

Set the variable ``claude_key_env`` names in the environment of the Odoo
service to a key of a workspace of the Anthropic Console of its own. That
workspace's monthly spend limit caps what the AI Assistant costs; Odoo keeps
no budget of its own. A module that installs after this one may name
another variable in ``ai.assistant.engine._default_claude_key_env``. Only
``ai.assistant.engine._seed_claude_key_env`` reads it, and sets the
parameter to it while the parameter still holds ``ANTHROPIC_API_KEY``: it
takes effect only where that module calls ``_seed_claude_key_env``, for
example from a noupdate ``<function>`` of its data, which runs at its
install only. Like ``ai_claude``, this module needs the ``anthropic`` and
``httpx2`` Python packages to install.

Answers run as queue jobs on the channel ``root.ai_assistant``. Give it a
capacity in the job runner's ``channels`` line of ``odoo.conf``, for example
``root.ai_assistant:2``: it caps how many answers wait on Claude at once.

A neutralized database has ``claude_models``, ``digest_recipients`` and
``incident_emails`` blanked, so a copy of production never calls Claude,
whatever key its host's environment holds, and mails nobody.
