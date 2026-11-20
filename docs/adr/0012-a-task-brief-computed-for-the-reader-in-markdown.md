# A task brief computed for the reader, in Markdown

Status: accepted (2026-11-18)

An agent that works a task through a connector needs the whole task: its attributes, related tasks, pull requests, texts, question rows, checklist, test cases, sessions, notes and attachments. Over RPC that is a read of the task, a read per kind of line and a search each for its notes and its attachments, and the texts come as Html with the markup of the editor.

**One computed field gives the whole task in one read.** From 16.0.2.14.0 `project.task` has `brief`, a Text field in Markdown, computed and not stored. One `read([id], ["brief"])` returns the header, the related tasks, the pull requests, the description, the question rows, the solution, the checklist, the test cases, the sessions, the last 10 internal notes and the attachments, in that order. Only the notes are cut. Question rows, checklist items, test cases and attachments carry their ids, so that a tool can act on them. The Html texts become Markdown through a converter of the module (`html_to_markdown`), which keeps headings, lists, tables, links and code and leaves no markup.

**It is computed as the reader.** The compute runs in the reader's environment. A reader who cannot read the task gets the access error a read gives. The lines, related tasks, notes and attachments the reader may not read, under access rights, record rules and the companies of the context, are left out, and so are the fields whose groups the reader lacks. Names and selection values follow the language of the context. The cache is keyed on the user, the companies and the language (`depends_context`).

**Attachments are pointers, and urls lose their tokens.** An image or a link that names an attachment of this database (its id written as the editor writes it, at most ten ASCII digits) becomes `![image: <name>](attachment <id>)` or `[file: <name>](attachment <id>)`: the agent reads the file it needs under its own rights, and the brief carries no image data. An inline `data:` image a save has not moved yet (ADR 0011) is a placeholder. The url of a link or an image loses its user and password and its parameters named like a token: an attachment link carries an access token that hands the file to whoever holds it (ADR 0011), and a brief is text an agent may copy into a log, a pull request or a transcript. A url standing as a word in text or code gets the same treatment on a best-effort basis; the help of the field names the forms that may keep a token. User text that would open a Markdown block is escaped, so a task's text cannot forge a section of the brief.

**A read that names no fields leaves it out.** `check_field_access_rights` drops `brief` from a read without a field list, so `read()` or `search_read()` without fields computes no brief. It is dropped there and not in `read`: project's `read` refuses a portal user an explicit field list that it lets through as `None`. The field is not exportable and is on no view.

Rejected:

- A stored field refreshed on write. The brief depends on the task, its lines, its notes, its attachments and the names of other records (stages, users, projects): every change to any of them would rewrite it, and what lies outside its dependencies would leave it stale. One stored text is the same for every reader, so it would hold lines, notes and fields that some of its readers may not read, and a read of it would go around their access. It would also be in one language.
- A controller (an HTTP route). A tool on the external API calls model methods, which a route is not; a route is another entry point with its own authentication and its own access checks, which a field computed in the reader's environment takes from the read.
- A report. Its render methods are private in 16.0, so RPC cannot call them, and a QWeb report renders HTML or PDF for a person, not text for an agent.

**Consequences.**

- A brief is computed per read and per task: a read of many tasks naming `brief` costs one brief each. Nothing is stored or written.
- A client asks for the brief by name; a read without a field list does not return it.
- Two readers of one task may get different briefs.
- Tokens and credentials are removed from urls on a best-effort basis in plain text and code: a url split by markup, or holding a brace, `<`, `>`, or a bracket or a quote in its query, may keep its token, and a token written loose outside a url stays.
- The header shows fields other modules declare (`key`, `planned_date_start` and `planned_date_end`, `effective_hours`, `uat_user_ids`) where the model has them.

Reversal cost: drop the field and the converter. Nothing is stored, so nothing is migrated; a tool that reads `brief` goes back to reading the task and its lines.
