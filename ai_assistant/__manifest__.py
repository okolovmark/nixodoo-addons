{
    "name": "AI Assistant",
    "summary": "AI Assistant: Claude answers employees from live Odoo data, read through tools as the employee",
    "description": """
The AI Assistant: Claude answers an employee's question from live Odoo data,
which it reads through read-only tools that run as that employee, with the
companies selected when the question was asked. It changes no record. The AI
deny list keeps payroll, contracts, appraisals, private employee data, private
addresses, bank details, secrets, signatures and the assistants' own records
out of reach even when the employee may read them. Every tool call is logged
for the administrators; the data read is not. Claude only: no other model
answers.

Ask AI: a button in the chatter asks the AI Assistant about that record and the
records linked to it. Question and answer are internal notes that notify
nobody; the answer is written by a queue job, in reply to the question it
quotes, and the asker rates it with a thumbs-up or thumbs-down.

The AI Assistant app: an employee's own conversations on a full page, and a
quick panel from the systray that carries the screen and record they have open.
Conversations are private to their owner, who may delete them; administrators
review them under the app's Review menu and set the assistant under its
Configuration menu.

To Review: the answers with a thumbs-down, not covered or not answered, Ask AI
and app alike, wait under Review > To Review, and in a weekly digest mail,
until an administrator marks them Fixed or Dismissed. Send new answer has the
AI Assistant answer such a question again as its asker, who is notified; the
new answer replies to the question, and later questions carry it in their
history in place of the answer it replaced.

AI reports: in the app the AI Assistant builds reports the employee keeps. The
model writes the queries, formula columns and merges, and the page in HTML and
CSS with its conclusions, binding its figures, tables and charts to the data.
Odoo runs the queries as the owner and its own renderer fills the bindings, in
a sandboxed frame that runs no script of the model's and reaches neither Odoo
nor the network. Refresh reruns the queries without the model; the owner may
share a report, whose recipients see the owner's snapshot, and anyone who sees
it may export its data to xlsx.
""",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "category": "Productivity",
    "version": "16.0.1.1.1",
    "license": "AGPL-3",
    # hr: the deny list hides the fields of hr.employee that hr.employee.public lacks.
    "depends": ["ai_claude", "hr", "mail", "queue_job", "report_xlsx"],
    "external_dependencies": {"python": ["anthropic", "httpx2"]},
    "data": [
        "security/ai_assistant_groups.xml",
        "security/ir.model.access.csv",
        "security/ai_conversation_security.xml",
        "security/ai_report_security.xml",
        "data/ir_config_parameter_data.xml",
        "data/mail_message_subtype_data.xml",
        "data/res_partner_data.xml",
        "data/queue_job_data.xml",
        "data/ir_cron_data.xml",
        "report/ai_report_reports.xml",
        "views/ai_assistant_tool_call_views.xml",
        "views/ai_assistant_exchange_views.xml",
        "views/ai_conversation_views.xml",
        "views/ai_report_views.xml",
        "views/res_config_settings_views.xml",
        "wizard/ai_report_share_views.xml",
        "views/ai_assistant_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ai_assistant/static/src/components/**/*",
            "ai_assistant/static/src/models/**/*",
            "ai_assistant/static/src/services/**/*",
        ],
        "web.assets_tests": [
            "ai_assistant/static/tests/tours/**/*",
        ],
    },
    "pre_init_hook": "pre_init_hook",
    "post_init_hook": "post_init_hook",
}
