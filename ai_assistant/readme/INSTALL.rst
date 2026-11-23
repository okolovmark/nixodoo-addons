Install, from the OCA, ``queue_job`` (``OCA/queue``) and ``report_xlsx``
(``OCA/reporting-engine``), both of branch 20.0, and the Python packages
``anthropic`` and ``httpx2``. ``ai_claude``, of this repository, is installed
with the module. ``test-requirements.txt`` at the root of the repository
lists them as pip requirements, with the versions the tests run with.

Answers run as jobs of ``queue_job``: run its job runner as its README says,
and give the job channel a capacity (see Configuration).

Put an Anthropic API key in the environment of the Odoo service, in the
variable ``ai_assistant.claude_key_env`` names (``ANTHROPIC_API_KEY`` as
installed), and restart the service. The key is never stored in the
database; while the variable is unset or empty, the AI Assistant does not
answer.
