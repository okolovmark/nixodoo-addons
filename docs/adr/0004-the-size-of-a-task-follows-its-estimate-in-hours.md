# The size of a task follows its estimate in hours, and a project marks the stages that need a type and an estimate

Status: accepted (2026-11-13)

A task carried two estimates: the hours (`planned_hours`) and a T-shirt size picked by hand, with an onchange that filled empty hours from the size. The size and the task type both had a default, so a value in either field did not say whether anyone had chosen it, and the two estimates drifted apart: a task could read XL next to four hours.

From 16.0.2.4.0 the hours are the estimate. The size (`tshirt_estimate`, labelled Size) is stored and computed from them: up to 2 h XS, up to 6 h S, up to 12 h M, up to 32 h L, up to 60 h XL, above that XXL; no hours, or zero, no size. Neither the type nor the size has a default. A size sent in a `create` or a `write` is ignored and the call goes through: tooling written before the change sends both values, and refusing the call would break it for no gain.

A project lists the stages a task enters only with a task type and an estimate above zero (`estimate_required_states`, "Stages Requiring a Type and an Estimate"). The rule runs where the closing rule of ADR 0002 runs: on a create, on a change of stage and on a change of project that moves the stage, in projects flagged AI-driven, whoever makes the change. An edit of a task that already sits in such a stage is not checked, so marking a stage does not lock the tasks that were there before.

Rejected: keeping the size editable and checking it against the hours. Two fields a person fills are two sources, and the check only moves the disagreement to the moment of saving.

Rejected: `required` on the two fields, or a constraint. A request arrives without an estimate and may never get one, and the tasks of projects that are not flagged have no reason to carry either.

Rejected: filling the hours of existing tasks from their size. A size says less than a number of hours, and a number made up from it would look like an estimate somebody gave.

**Existing tasks.** Their type and hours are left alone. One SQL statement, run by the migration and by the install hook, sets the size of every task from its hours without writing the tasks. A task without hours ends with no size, the former default included.

**The priority star.** Flagged projects rank tasks by Priority Level, so the core star is hidden on their tasks: the form, the kanban card, the list, the lists inside the form and the calendar popover. Its stored value is not touched, and projects that are not flagged keep it.

**Consequences.**

- A size someone picked by hand on a task without hours is gone after the update, and one that disagreed with the hours is replaced by theirs. Nothing keeps the former value.
- A task created straight into a marked stage needs its type and its hours in the same create. What cannot supply them is refused there: the kanban quick create, a mail alias, a task created from a sales order line, a task copied by a project duplication from a source that sat in the stage without them. A next occurrence of a recurring task carries the type of its source.
- A stage listed for a Resolution and for a type and an estimate refuses for the Resolution first.
- A task whose stage is recomputed through its parent, and the other occurrences a recurrence write moves along, are not checked, as with the closing rule.
- With Timesheets installed the same hours also show on its tab as Allocated Hours (Allocated Days where the company encodes timesheets in days); the two inputs are one field.
- The star hidden on a task still orders the lists core sorts by it, and core's High Priority and Low Priority filters remain.
- A module installed on a database that has tasks and no size column computes the size through the ORM, which writes those tasks once.
- The 20.0 branch gets the same change with `allocated_hours` in place of `planned_hours` when this work is forward-ported to it.

Reversal cost: drop the compute and the rule and make the size editable again. The sizes picked by hand before this version are not recoverable from the database.
