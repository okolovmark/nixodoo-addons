# The stage history of a task is a log written with its stage, not read from the chatter

Status: accepted (2026-11-13)

The history of a task's stages used to exist only as tracking in the chatter, and readers such as a Gantt drew it from there. Tracking is optional by design. A caller switches it off with a context key (`tracking_disable`, `mail_notrack`), and automation that moves tasks quietly does exactly that. Tracking is also finalised once per transaction, so several moves collapse and a round trip leaves nothing; the stage a task was created in is never recorded; and the stage change of a predecessor is posted on every dependent task as if it were its own. On a database where most moves are made by automation, most recent tasks had no stage history at all.

From 16.0.2.2.0 the module keeps a log, `project.task.stage.log`: one row when a task is created with a stage and one row on every change of its stored stage, for every task, whatever the context. The rows are written by SQL where the task's row is written, in `_create` and `_write` of `project.task`: it is the one point every change of the column passes through, a stage recomputed by a project change included.

Rejected: making tracking mandatory, by stripping the context keys or by posting the tracking anyway. Callers switch tracking off on purpose, to keep the chatter and the notifications quiet, and the other gaps (transaction-level collapse, no creation stage, the predecessor rows) would remain.

Rejected: logging in the public `write` and `create`. A stage recomputed when the project changes never passes through `write`, and neither does a project change another module writes in a call of its own.

**The past is filled from what the database still knows, and nothing is guessed.** A task with stage tracking gets its tracked history, the stage it was created in read from the old value of its first tracking row. When that history does not end at the task's current stage, one row is added for the current stage at the date of the last stage update. A task without tracking gets one row: at its last stage update when that is 2 seconds or more after its creation, otherwise at its creation. A task without a stage and without tracking gets nothing. Each row names its source, so a reader can tell a recorded change from a derived one, and breaks in a tracked chain are kept as they are.

**Access.** Only system administrators read the log, and nobody edits it. It holds the stages of every task, including tasks a user cannot read, so other code reads it through the task.

**Consequences.**

- A row exists once the change reaches the database: several changes of one task within one flush are one row, from the stored stage to the last one, and a change undone within one flush leaves none. A search of the log flushes pending stage changes first.
- A stage written by raw SQL is not seen.
- The user of a row is the task's writer as the update records it, or the user of the environment that flushes the change. In an ordinary request that is the acting user.
- During an in-place update a worker still running the previous code moves tasks without a row, and the fill does not revisit a task that has rows: run the update with the server stopped.
- The chatter is left alone: tracking keeps working where it is on, and nothing is removed from it.
- The 20.0 branch gets the same change when this work is forward-ported to it.

Reversal cost: readers go back to the chatter and the model is dropped. The moves recorded since this version exist nowhere else.
