# The stage history of a task is a log written with its stage, not read from the chatter

Status: accepted (2026-11-13)

The module keeps a log, `project.task.stage.log`: one row when a task is created with a stage and one row on every change of its stored stage, for every task, whatever the context. The 16.0 branch decided this first. Its record, `docs/adr/0003-the-stage-history-of-a-task-is-a-log-written-with-its-stage.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**Why a log, on Odoo 20 as well.** Without one, the history of a task's stages exists only as tracking in the chatter. Tracking is optional by design: a caller switches it off with a context key (`tracking_disable`, `mail_notrack`), and automation that moves tasks quietly does exactly that. On Odoo 20 a tracked change is, in addition, kept as a record only where the module `mail_tracking` is installed, which `project` does not depend on; elsewhere it is text in a message. Odoo 20 also keeps on each task the time it spent in each stage (`duration_tracking`). That is a total per stage, not a history: it has no order of the moves, no dates and no author.

**The rows are written where the row of the task is written.** By SQL, in `_create` and in `_write_multi` of `project.task`. On Odoo 20 the flush hands the values of each record to `_write_multi`, and `_write` only passes its values on to it: an override of `_write`, which is where 16.0 logs, would miss every flush. A stage recomputed by a project change passes there as a written one does, and no context and no `sudo()` goes around it.

**No fill of the past.** 16.0 fills the log once, from the chatter and the dates of the tasks, for databases that had tasks before the log. This branch ships no fill. A task that existed before the module was installed has no row until its stage changes, and that first row names the stage it left as the previous one. The `source` values the fill writes on 16.0 stay in the selection, so the rows of a database upgraded from 16.0 keep theirs; on this branch every row written is a recorded change.

**Access.** Only system administrators read the log, and nobody edits it. It holds the stages of every task, including tasks a user cannot read, so other code reads it through the task.

**Consequences.**

- A row exists once the change reaches the database: several changes of one task within one flush are one row, from the stored stage to the last one, and a change undone within one flush leaves none. A search of the log flushes pending stage changes first.
- A stage written by raw SQL is not seen.
- The user of a row is the task's writer as the update records it, or the user of the environment that flushes the change. In an ordinary request that is the acting user.
- A reader that needs the stages of a task from before the module was installed finds nothing in the log for it.
- The chatter is left alone: tracking keeps working where it is on, and nothing is removed from it.

Reversal cost: readers go back to the chatter and the model is dropped. The moves recorded since the module was installed exist nowhere else.
