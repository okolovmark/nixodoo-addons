Install the Python packages ``anthropic`` and ``httpx2``;
``test-requirements.txt`` at the root of the repository pins the versions
the tests run with. A server whose environment lost ``anthropic`` after the
install still loads; the chain then asks no model and records the attempt
*config*.

The module holds no API key. Each assistant names, in its
``claude_key_env`` parameter, the environment variable of the Odoo service
that holds its Anthropic API key: set that variable in the service's
environment and restart the service (see Configuration).
