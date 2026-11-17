# A note's category is read from its bold first words and stored on the message

Status: accepted (2026-11-17)

The 16.0 branch decided this first. Its record, `docs/adr/0009-a-note-s-category-is-read-from-its-bold-first-words-and-stored-on-the-message.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The fields.** A message has `task_id`, the task it is posted on: stored, computed from `model` and `res_id`, empty for another model and for a task that no longer exists, emptied when the task is deleted. An internal note of a task has `note_category`, one of eight categories, stored. The category is read from the body as stored: after white space, `&nbsp;` and at most one opening `<p>` or `<span>`, a `<b>` or `<strong>` that opens with the name of a category, in any letter case, alone or followed by a dash, a colon, a slash, a plus or an opening parenthesis. A message of another subtype or model, and a tracking message, have none. Both fields are precomputed, so a message is inserted with them, and a note edited, moved, retyped or given another subtype is read again. Each column has a partial index of the rows that hold a value. Project > Task Notes lists, filters and groups the notes of tasks; the task form has a Notes button.

**Stored.** A filter or a group by category or by task, and the notes of one category of one task over the API, are one query on an indexed column.

**A pattern, not a parse.** One regular expression per category reads the opening of the Html as stored. It spells out its white space, since `\s` matches every Unicode space and the sanitizer stores a no-break space as `&nbsp;`, and each letter in both cases, since case-insensitive matching also takes letters outside ASCII that fold onto them. A category in bold inside a heading or a list, or coloured, is missed, and the names and their separators are fixed in code.

**What differs from 16.0.**

- Odoo 20 lets any user group messages: its `_read_group` searches through `_search`, which applies the access check of messages. 16.0's grouped-read override, its fixed list of group-bys and `is_task_readable`, which serve a project user there, are not here. Task Notes groups under core's check, by any field.
- The Task Notes domain keeps `model = project.task` and a set `task_id`: with one model in the domain, `_search` checks the access of the tasks in SQL, where a domain without a model is filtered over batches of messages in Python.
- In Odoo 20 a message with tracked values is of type `tracking`, and its body holds the rendered changes after any note logged with them; `tracking_value_ids` belongs to the module mail_tracking. The compute tells a tracking message by its type: it has no category and is not in Task Notes, whatever its body.
- No `group_expand`: core orders the groups of a selection in the order of its values, the notes without a category last.
- No SQL fill. The ORM computes both fields of every existing message when it adds their columns, on install and on the update that brings them, so the pattern is read by Python alone.

**Consequences.**

- Clients search `task_id` and `note_category` on `mail.message`: one search returns the notes of one category of a task. A client written for 16.0 that names `is_task_readable` fails.
- A note given a wrong category, or none, is read again when its body is edited.
- Another category or spelling is a change of the code; the notes that exist get it only where a migration recomputes them.
- The message table has two more columns, each with a partial index. Adding them locks the table until the module is loaded, and the ORM reads the body of every message meanwhile: on a large database the install or update runs with the server stopped.
- A grouped read of task notes applies core's access check of messages, whatever the user and the group-by.

Reversal cost: drop the two fields, the views and the Notes button; the update drops the two columns. The notes are untouched: their category is in their bodies and comes back with the fields.
