# The AI Assistant is `ai_assistant` on `ai_claude`, made an organisation's own by a module that extends it

Status: accepted (2026-11-23)

An employee AI Assistant (Claude answering from live Odoo data through read-only tools that run as the employee, Ask AI in the chatter, an app with a systray panel, answers to review, AI reports) and a website chat assistant were written in one project's own modules. The website chat module held the generic Claude layer: the Anthropic client with its chain of models, tool loop, per-assistant parameters and circuit breaker, the cost of every call, answer formatting, the AI conversation, the fields of an exchange log and the check that mails when Claude stops answering. The employee assistant was a module on top of it, with the organisation's name, its deny list entries, its prompt and its choice of documented modules written into constants. Another project could take neither without the website chat and the first project's words.

**The decision.** Two shared modules.

- `ai_claude`, on `mail`: `ai.claude.gateway`, `ai.exchange.mixin`, `ai.conversation` with its views and access, and `answer_to_html` / `answer_from_html`. Every method takes the assistant's prefix and reads `<prefix>.<name>`, so each assistant keeps its own models, key, limits, breaker and incident state. It ships no parameter, menu or cron.
- `ai_assistant`, on `ai_claude`, `hr`, `queue_job` and `report_xlsx`: the whole employee assistant, its settings under `ai_assistant.*`, and a cron of its own that runs the Claude incident check for it every 15 minutes. It cannot ride on another assistant's cron, as it did inside the project, since it depends on no other assistant.

A website chat, or any other assistant, depends on `ai_claude` alone.

**A module of the organisation's own extends it.** The organisation keeps a module, the holder, that depends on `ai_assistant` and overrides, calling `super()`, the methods that carry what is its own: on `ai.assistant.tools` `_deny_list_models`, `_deny_list_fields`, `_employee_work_fields` (fields of `hr.employee` that stay readable), `_request_models` (employee requests whose reason is private), `_tool_texts`, `_model_modules`, `_module_docs_modules` and `_docs_floor_module_names`; on `ai.assistant.engine` `_system_prompt_parts` (the organisation, the paragraph on models that are not installed, whom to ask) and `_default_claude_key_env`; on `ai.assistant.exchange` `_incident_recipients`. The holder adds to what the shared module ships and repeats none of it: its deny patterns go in right after the shipped pattern each follows (`with_patterns_after`), so a pattern the shared module adds later reaches every holder, in order. `_default_claude_key_env` is read only by `_seed_claude_key_env`, which the holder calls from a noupdate `<function>`: it applies at the holder's install, while the parameter still holds the shipped variable, and never overwrites a value set since.

The texts the shared modules ship name no organisation and no other model: the system prompt says *our* Odoo and suggests asking *an administrator*, the tools name *Odoo* and *modules*; the chatter line of a question no Claude model answered ends *no other model answers, so nobody answered*, and the incident mail says *another model answered them, or nothing did*. An assistant with a model of its own after Claude gives its words through `_claude_not_answered_sentence` and `_claude_incident_body`.

**A masked name is masked in its copies, by the current names.** Where the deny list masks a display name, `ai_assistant` masks Odoo's copies of it: a message's `record_name`, the `res_name` of attachments, activities and ratings, a rating's `parent_res_name`, a tracked link's old and new values, and each occurrence, in a message's or a mail's texts, of the current name of the message's record and of the records it links directly (by a stored many2one, or by a record link in its HTML). A filter on a copy or on such a text is decided in SQL on the rows of the call, on the safe side: it never finds a row whose copy or text the masking changes or may change. A sort, group or aggregate is refused by the rows the call reads as the employee, after the check of their read access, so no refusal tells of a row the call does not read. Matching the current name of the record and of its direct links only leaves the limits `ai_assistant`'s ROADMAP lists (former names, former links, names reached through lines, copies Odoo keeps elsewhere), and they are accepted.

**The records keep their ids.** A database may hold all of this under the project's modules. As in ADR 0015, each shared module's `pre_init_hook` finds its holder by the external id of one of its models, never by name (`ai_claude`: `ai.exchange.mixin`; `ai_assistant`: any of its models), and before any data file is loaded takes from it the external ids its data files declare, those of its models, of the fields and selection values its classes declare, and of the constraints, many2many tables and foreign keys registered for them, and reads the adopted views from its own files. Every record keeps its database id. What the holder declares on a shared model itself, an assistant value or a field of its own, stays with it. `ai_assistant` also:

- renames the holder's settings `<holder>.<name>` to `ai_assistant.<name>` with their values, leaving a key that is taken alone;
- gives each setting of its data file the row of its key, which a setting emptied and filled again has without an external id;
- keeps the rows of the adopted noupdate records and writes them back in its `post_init_hook`, since a data file loaded at install writes even noupdate records, and removes the `forcecreate="0"` settings the holder no longer had, which that file creates;
- renames the holder's xlsx report model to `report.ai_assistant.ai_report_xlsx`, with its fields, under the same ids.

Only a holder that had records of `ai_assistant`'s data files hands over its settings and its report model.

Rejected: one shared module for both. A website chat, or any assistant without tools, would install the employee assistant's tools, app, queue jobs, xlsx reports and `hr` to reach a client of the Messages API.

Rejected: the holder as a copy, the organisation keeping its own copy of the assistant with its words in place. Every fix to the tools, the deny list or the hooks would be made once per copy, and the copies drift apart.

Rejected: the generic layer staying in the website chat module, with the employee assistant on it. Every database with the employee assistant would install the website live chat, the blog, CRM and recruitment.

**Consequences.**

- The holder's update installs both shared modules, with the server stopped, once per database; on a database that holds no such records, or holds them under the shared modules already, the hooks change nothing.
- Settings keys change with the module: whatever read `<holder>.<name>` reads `ai_assistant.<name>`. So do the names that carry the module, the client action tag `ai_assistant.app` and the systray item `ai_assistant.AiAssistantSystray`; a holder may register its former tag for open tabs and bookmarks.
- Two lines of the AI Assistant read in the neutral words above, and its Claude incident mails come from its own cron, whatever other assistant is installed.
- A holder that overrides `_default_claude_key_env` and never calls `_seed_claude_key_env` changes nothing.
- A holder and the settings only add to the deny list; narrowing it is a change of `ai_assistant`.
- `module_docs` reads the READMEs a holder chooses; `ai_assistant` and `ai_claude` document the models they define or extend, so a holder that keeps only its own modules' READMEs names these two as well.

Reversal cost: fold the two modules back into a project's module, which then needs a hook of its own to take the records back, as these hooks took them.
