# A create of an existing task and session updates its row

Status: accepted (2026-11-18)

The 16.0 branch decided this first. Its record, `docs/adr/0010-a-create-of-an-existing-task-and-session-updates-its-row.md` there, is carried here with what differs on 20.0.

A task does not say which agent sessions worked on it, who ran them, when, or what each did. From 20.0.2.12.0 a session is a row of `project.task.session`, held by the task field `session_ids`: the task, the agent session id, the date the session last wrote its row, the user who worked it and a one-line outcome. A task lists a session once, by a unique constraint on the task and the session. No link is stored: where the Entire CLI records sessions, `entire session info <id>` opens one. The writer is a tool: an agent writes its row on each task it worked when its session closes, over RPC, and a session that closes again writes again.

**A create of a known pair updates its row.** `create` of a task and session that already have a row writes the values given onto that row and returns it, at the position of those values in the batch. The date becomes now unless the values give one; what the values do not give, the user and the outcome included, stays. A batch that names a pair twice keeps the later values, as two creates one after the other would. The task and the session are read from the values or from the defaults, so `Command.create` in a task's `session_ids` and a row saved on the task form take the same path. `write` stamps nothing: a person's edit changes only what it names, and the date stays.

The reason is one call: a tool working through a connector sends one `create` per task when its session closes, without first reading whether its row exists, as a write of a link to `pr_uri` finds the pull request or creates it (ADR 0002).

Rejected: the unique constraint alone, with each caller searching for its row and writing it, or creating it when there is none. Every tool repeats the same two calls, and one that skips the search fails on the constraint the second time its session closes.

**The access check comes before the lookup.** A row follows the access of its task (ADR 0007), so the rows of a task the caller cannot read are not found. A create on such a task would then insert, and the unique constraint would tell the caller whether that task lists the session. `create` therefore checks read access on every task the values name, defaults included, before it looks for rows: a create on a task the caller cannot read raises an access error whether or not the pair exists there. A write of `task_id` checks read access on the task it names first, then write access, since core checks a write only against the task a row leaves. `sudo()` finds and updates the row of any task.

**What differs on 20.0.**

- The access of a row is the domains of its two access rights, `access_task_session_read` (`[('task_id', 'access', 'read')]`) and `access_task_session_write` (`[('task_id', 'access', 'write')]`), as for the other lines (ADR 0007), not `project.task.access.mixin`. A row is read with its task and created, changed and deleted with write access to it. A create that finds the row of its pair updates it only where the caller may write the task; on 16.0 reading the task is enough.
- Odoo 20 checks a create's access after its insert, so the read check before the lookup is what keeps the unique constraint from answering for a task the caller cannot read. A write of `task_id` also checks write access on the task it names (`check_task_line_move`, ADR 0007).
- The unique constraint is a `models.Constraint`, `project_task_session_task_session_unique`; the name of a row is its `display_name`, computed from the outcome or the session.
- 16.0 drops a project given with the values of a row found where `project_member_access` is installed; that module has no 20.0 version, and this module keeps no such rule.

**Consequences.**

- A `create` may return a record that existed before the call. A batch returns one record per values, in their order, the same record where a pair repeats.
- An update through `create` keeps the user who worked the row unless the values give another.
- A user who reads a task but may not change it gets an access error from a create on it, whether the create would add a row or update one.
- Two first creates of the same new pair in concurrent transactions both find no row: the later one fails on the unique constraint and is not retried.
- A write that moves a row onto a task and session that have a row fails on the unique constraint.
- Rows have no chatter: the values an update replaces, the date included, are not kept.
- A copy of a task starts without sessions.

Reversal cost: drop the `create` override; every writer then searches for its row before writing it. No data changes either way.
