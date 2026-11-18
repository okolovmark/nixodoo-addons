# A task brief computed for the reader, in Markdown

Status: accepted (2026-11-18)

The 16.0 branch decided this first. Its record, `docs/adr/0012-a-task-brief-computed-for-the-reader-in-markdown.md` there, is carried here with what differs on 20.0.

An agent that works a task through a connector needs the whole task: its attributes, related tasks, pull requests, texts, question rows, checklist, test cases, sessions, notes and attachments. Over RPC that is a read of the task, a read per kind of line and a search each for its notes and its attachments, and the texts come as Html with the markup of the editor.

**One computed field gives the whole task in one read.** From 20.0.2.14.0 `project.task` has `brief`, a Text field in Markdown, computed and not stored. One `read([id], ["brief"])` returns the header, the related tasks, the pull requests, the description, the question rows, the solution, the checklist, the test cases, the sessions, the last 10 internal notes and the attachments, in that order. Only the notes are cut. Question rows, checklist items, test cases and attachments carry their ids, so that a tool can act on them. The Html texts become Markdown through a converter of the module (`html_to_markdown`), which keeps headings, lists, tables, links and code and leaves no markup.

**It is computed as the reader.** The compute runs in the reader's environment. A reader who cannot read the task gets the access error a read gives. The lines, related tasks, notes and attachments the reader may not read, under their access rights and the companies of the context, are left out, and so are the fields whose groups the reader lacks. Names and selection values follow the language of the context. The cache is keyed on the user, the companies and the language (`depends_context`). Archived sub-tasks, blocking and blocked tasks and assignees are left out whatever `active_test` of the context (an archived parent or user in a many-to-one value still shows): the compute forces `active_test`, because `depends_context("active_test")` fails on a field that is not relational in Odoo 20 (`Environment.cache_key` reads `field.context`).

**Attachments are pointers, and urls lose their tokens.** An image or a link that names an attachment of this database becomes `![image: <name>](attachment <id>)` or `[file: <name>](attachment <id>)`: the agent reads the file it needs under its own rights, and the brief carries no image data. An inline `data:` image a save has not moved yet (ADR 0011) is a placeholder. The url of a link or an image loses its user and password and its parameters named like a token: an attachment link carries an access token that hands the file to whoever holds it (ADR 0011), and a brief is text an agent may copy into a log, a pull request or a transcript. A url standing as a word in text or code gets the same treatment on a best-effort basis; the help of the field names the forms that may keep a token. User text that would open a Markdown block is escaped, so a task's text cannot forge a section of the brief.

**A read that names no fields leaves it out.** `read()` and `search_read()` without a field list compute no brief. The field is not exportable and is on no view.

Rejected:

- A stored field refreshed on write. The brief depends on the task, its lines, its notes, its attachments and the names of other records (stages, users, projects): every change to any of them would rewrite it, and what lies outside its dependencies would leave it stale. One stored text is the same for every reader, so it would hold lines, notes and fields that some of its readers may not read, and a read of it would go around their access. It would also be in one language.
- A controller (an HTTP route). A tool on the external API calls model methods, which a route is not; a route is another entry point with its own authentication and its own access checks, which a field computed in the reader's environment takes from the read.
- A report. A QWeb report renders HTML or PDF for a person, not text for an agent.

**What differs on 20.0.**

- Odoo 20 has no `check_field_access_rights`, where 16.0 drops `brief` from a read naming no fields. `read()` and `search_read()` each expand an empty field list from `fields_get()`, and `search_read()` does not call `read()`, so the module overrides both on `project.task` to pass the expanded list without `brief`. Project's `read` on 16.0 refuses a portal user an explicit field list; Odoo 20 has no such override, and `fields_get()` already holds only the fields a portal user may read.
- The access checks are `check_access("read")` on the tasks and `has_field_access(field, "read")` on the fields. Odoo 20 filters the records of an x2many by read access as it reads them, and gives the record of a many-to-one whatever its access: the parent goes through `_filtered_access("read")`, as every related record does on 16.0. Odoo 20 also checks the groups of a field on every attribute read in a user's environment, so the brief reads only the fields the reader may read.
- The header shows the task's status (`state`), which Odoo 16 does not have. The estimate is `allocated_hours`. The deadline is a date and time, in UTC as the other times. The planned window is no core field on Odoo 20: the header shows the planned start where a module declares `planned_date_begin`, on 16.0 the window of `planned_date_start` and `planned_date_end`. The link is `/odoo/project.task/<id>`.
- The notes are those of the task's **Notes** button (`_get_task_note_domain(with_templates=True)` on its `res_id`): a tracking message is of its own type on Odoo 20 and is never a note.
- The image of a question row or a test case is an attachment of the row (ADR 0011): it is a pointer in the row's text, named from the attachment, and is not in the attachments list, which holds the task's own as its chatter does. On 16.0 every image is an attachment of the task and is listed.

**Consequences.**

- A brief is computed per read and per task: a read of many tasks naming `brief` costs one brief each. Nothing is stored or written.
- From 20.0.2.15.1 the field is declared `recursive` though it has no recursive dependency: Odoo then computes an attribute read (`task.brief`) for that task only, where it would compute the whole prefetch set, up to 1000 tasks. A `read()` costs the same either way.
- From 20.0.2.15.1 an editor checklist becomes Markdown task list items, `- [x]` when ticked, `- [ ]` when not.
- A client asks for the brief by name; a read without a field list does not return it.
- Two readers of one task may get different briefs.
- Tokens and credentials are removed from urls on a best-effort basis in plain text and code: a url split by markup, or holding a brace, `<`, `>`, or a bracket or a quote in its query, may keep its token, and a token written loose outside a url stays.
- The header shows fields other modules declare (`key`, `planned_date_begin`, `effective_hours`, `uat_user_ids`) where the model has them.

Reversal cost: drop the field, the two read overrides and the converter. Nothing is stored, so nothing is migrated; a tool that reads `brief` goes back to reading the task and its lines.
