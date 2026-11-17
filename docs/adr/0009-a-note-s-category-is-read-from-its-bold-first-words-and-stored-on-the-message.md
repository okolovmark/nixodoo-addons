# A note's category is read from its bold first words and stored on the message

Status: accepted (2026-11-17)

The internal notes of a task record its requirement changes, design decisions, fixes, feedback, progress and deploys, each opening with that word in bold. Nothing else told one kind from another: finding the design decisions of a task meant reading its chatter, a tool working the task over the API read every message of the task, and no list showed the notes of several tasks. Odoo lets only administrators group messages, so a project user could not group them by task either.

From 16.0.2.8.0 a message has `task_id`, the task it is posted on: stored, set from `model` and `res_id`, emptied when the task is deleted. An internal note of a task has `note_category`, one of eight categories, stored. The category is read from the body as stored: after white space, `&nbsp;` and at most one opening `<p>` or `<span>`, a `<b>` or `<strong>` that opens with the name of a category, in any letter case, alone or followed by a dash, a colon, a slash, a plus or an opening parenthesis. A message of another subtype or model, or one that tracks changed fields, has none. Both fields are precomputed, so a message is inserted with them, and a note edited, moved or given another subtype is read again. `is_task_readable` searches the messages of the tasks the user can read, as SQL. Project > Task Notes lists, filters and groups the notes; the task form has a Notes button.
  - 2026-11-17: 16.0.2.8.1 accepts one opening `<span>` too, in place of the `<p>` (the Html sanitizer stores a body of more than one node that opens with an inline tag and holds no block tag, as a note posted over the API as `<b>Progress</b> - in review`, inside a `<span>`); its update reads only the task notes without a category that open with a `<span>`.

**Stored.** A filter or a group by category, and the notes of one category of one task over the API, are one query on an indexed column. The task is stored for the same reason, and so that the group of a task bears its current name, where `record_name` keeps the name the task had when the note was posted. Each column has a partial index, of the rows that hold a value.

**Filled by SQL.** `mail_message` holds every message of the database, and Odoo computes a stored field it adds over every row of its table, through the ORM. The pre-migration and the pre-init hook create the two columns and their indexes before Odoo sets up the fields, so it finds them made and computes nothing, then fill them with one statement each that applies the patterns of the compute and clears any value a message should not hold. A second run changes nothing. The columns are analyzed last: until the next automatic analysis the planner would otherwise read every note to list those of one task.

**A pattern, not a parse.** One regular expression per category is read by Python in the compute and by PostgreSQL in the fill, so both give a note the same category; the tests hold the fill to the computes. It spells out its white space, which `\s` matches differently in the two, and each letter in both cases rather than folding case, which Python, PostgreSQL and its collations do differently outside ASCII. The cost is that it reads only the opening of the Html as stored: a category in bold inside a heading or a list, or coloured, is missed, and the names and their separators are fixed in code. A missed category leaves the note listed without one; a wider rule, a category word anywhere in the first line, would give a category to notes that only mention progress or a deploy.

Rejected: a category computed and not stored. Odoo groups only by a stored field, and a search of it would match the pattern against every note at each query.

Rejected: reading the category with an Html parser in Python. The fill would load every internal note of the database into Python, and the compute and the fill would be two readings that can disagree.

**Project users group task notes.** Core refuses a grouped read of messages to users who are not administrators: it skips the document check by which `_search` drops the messages of records the user cannot read. Task Notes groups by category and by task, so a project user may group messages on a domain that holds an `is_task_readable` leaf. The override builds, as the user, the query of that domain with `is_task_readable = True` and no message to one user added, under the user's access rights and record rules on messages, and groups the messages of that query as the superuser. It does not leak:

- The groups hold only messages of tasks the user can read, whatever else the domain says: the condition is added to the caller's domain, not read from it, so `is_task_readable = False` or an `|` around the leaf gives nothing more.
- Messages to one user, which core shows only to their author and recipients, are left out.
- The domain of each group is the caller's domain with the condition of the group. The client sends it back to open the group, and that search goes through `_search` and its document check.
- The groups are by a fixed list of fields: category, task, author and date, which the notes list shows, and model, message type and subtype. Grouped as the superuser, a many2many such as the recipients would show partners the partner rules hide, so any other group-by still gets core's refusal.
- Any other domain, and a user who is not a project user, still get core's refusal.

Rejected: grouping as the superuser on the caller's domain. A group would count the notes of tasks the user cannot read.

**Consequences.**

- Clients search `task_id` and `note_category` on `mail.message`: one search returns the notes of one category of a task.
- A note given a wrong category, or none, is read again when its body is edited.
- Another category or spelling is a change of the code; `fill_task_messages`, run by a migration, gives the notes that exist the category the new patterns read.
- The message table has two more columns, each with a partial index. Adding them and the foreign key of `task_id` locks the table until the module is loaded: on a large database the update runs with the server stopped.
- A grouped read of task notes by a project user applies the record rules on messages and on tasks. A document check that core or another module adds to `mail.message._search` in Python does not apply to these groups.
- The breadcrumb of a note's form shows `record_name`, the name the task had when the note was posted.

Reversal cost: drop the three fields, the grouped-read override, the views and the Notes button; the update drops the two columns. The notes are untouched: their category is in their bodies and comes back with the fields.
