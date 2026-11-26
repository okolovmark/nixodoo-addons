# A task records its actual hours and the coefficient its estimate applied, and a project marks the stages that need actual hours

Status: accepted (2026-11-26)

An estimate made with an agent is priced from a table of measured anchors (a shape of work and the focused hours it took). How far the anchors sat from the real work was measured outside Odoo: after a task closed, its actual time became a row of a Markdown table in the tooling's repository, one pull request per row. Only a fraction of the tasks was ever measured, the table could not be filtered by the task's tags or type, and the actual time of a task was nowhere on the task.

From 16.0.2.31.0 the task carries both sides, and Odoo turns them into the correction:

- `actual_hours` (Actual Hours): the focused hours the task took to done, waits on other people left out. It sits next to the Estimate on the form, in projects flagged AI-driven. It is never negative and never NaN or infinite, in any stage. A copy starts without it, except a copy that keeps the source's stage (a project duplication), which takes it as it takes the Resolution (ADR 0002).
- `estimate_coefficient` (Estimate Coefficient): the factor the estimate multiplied the anchor price by, default 1.0, finite and above zero. It is not on the form: the tooling that estimates writes it with the hours. The next occurrence of a recurring task takes it with the hours.
- `actual_hours_required_states` (Stages Requiring Actual Hours) on the project: a task enters these stages only with `actual_hours` above zero. The rule runs where the rules of ADR 0002 and ADR 0004 run (a create into the stage other than a recurrence's next occurrence, a change of stage, a change of project, also one that keeps the stage), in projects flagged AI-driven, whoever makes the change. A task already in the stage keeps every other edit, and its actual hours may be cleared there.
- `get_estimate_coefficient(tag_ids, task_type, user_ids)` on `project.task`: over the closed tasks of those assignees with both hours above zero, the first of four levels with at least five tasks (every given tag and the type, at least one given tag and the type, the type, all; a level that needs tags or a type is skipped when none is given), its thirty most recently closed, the median of `actual_hours * estimate_coefficient / planned_hours`. Fewer than five in all give 1.0 and no level. It returns the coefficient, the number of tasks, the level and their keys, and reads with the caller's rights.

**The applied coefficient is stored.** A coefficient measured as actual over the corrected estimate drifts back to 1 once the corrected estimates start to hit, and the next estimates miss again by the full correction. Multiplying by the coefficient each estimate applied measures the actual against the price before the correction, so a correction that works keeps itself. The default 1.0 makes every task estimated before this version right as it stands.

Rejected: timesheet lines for the actual time. They are the labour record of the people on the task, at a human pace and with waits; a line logged to feed an estimate would land in that record, and the hours a person logs would be read as the agent's focused time.

Rejected: the time a task spent between stages, from the stage log (ADR 0003). It counts calendar time, waits and nights included.

Rejected: accepting timesheet hours in place of the actual hours to enter a marked stage. Either source would satisfy the rule, and the coefficient would read the tasks that have only one of them as having none.

Rejected: a frozen first estimate next to the hours. The hours are raised when the scope grows, and the coefficient is meant to price the scope that was done; the first value stays in the tracking of the hours.

**Existing tasks.** No task is written: the new column of the coefficient reads 1.0, Actual Hours stay empty until someone writes them, and no stage is marked until a project lists one.

**Consequences.**

- Where a project lists its closing stage, every task closed there needs actual hours, also for people who log timesheets: closing a task takes one more number.
- A task created straight into a marked stage needs the hours in the same create: a mail alias, a kanban quick create in that column, a task created from a sales order line, a copy by a project duplication from a source without them is refused there.
- A stage listed for a Resolution, for a type and an estimate and for actual hours refuses for the Resolution first, then for the type and the estimate, then for the actual hours.
- The coefficient is per assignee: a task assigned to several people counts in the coefficient of each of them, and once in a call that names several.
- Tasks closed into a folded stage count, a cancelled task with actual hours included.
- A project duplication copies its closed tasks with their actual hours, and core gives each copy the time of the copy as its last stage change: the copies become the most recently closed tasks of their assignees and count beside their sources until thirty newer tasks close.
- The 20.0 branch gets the same change with `allocated_hours` in place of `planned_hours` when this work is forward-ported to it.

Reversal cost: drop the two fields, the project setting and the method. The actual hours written are lost with the column unless exported first.
