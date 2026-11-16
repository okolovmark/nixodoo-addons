# The size of a task follows its estimate in hours, and a project marks the stages that need a type and an estimate

Status: accepted (2026-11-16)

The 16.0 branch decided this first. Its record, `docs/adr/0004-the-size-of-a-task-follows-its-estimate-in-hours.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The hours are the estimate.** On this branch they are core's `allocated_hours` (Allocated Time), the field Odoo 20 has in place of `planned_hours`. The size (`tshirt_estimate`, labelled Size) is stored and computed from them: up to 2 h XS, up to 6 h S, up to 12 h M, up to 32 h L, up to 60 h XL, above that XXL; hours not above zero, no size. Neither the type nor the size has a default. A size sent in a `create` or a `write` is ignored and the call goes through. The hours must be a finite number: a NaN or an infinity is refused in any project.

**A project marks the stages that need a type and an estimate** (`estimate_required_states`, table `project_estimate_required`, the names of 16.0). A task of a flagged project enters such a stage only with a task type and an estimate above zero. The rule runs where the closing rule of ADR 0002 runs, after it: on a create, on a change of stage and on a change of project that moves the stage. An edit of a task that already sits in such a stage is not checked, and templates (a task template, its sub-tasks, the tasks of a project template) are outside the rule, as they are outside the closing rule.

**The priority star.** Flagged projects rank tasks by Priority Level, so the core star is hidden on their tasks: the form, the kanban card, the list, the lists inside the form and the calendar popover. Its stored value is not touched, and projects that are not flagged keep it, as does the portal.

**What differs from 16.0.**

- No migration and no install hook recompute the sizes of existing tasks (ADR 0001: no database that has to be kept is known to run this branch). A database that has an earlier 20.0 version of this module installed keeps the sizes stored there until the hours of a task change.
- The next occurrence of a recurring task is built by core from a copy of the task, so it carries the type and the hours without an override.
- 16.0 sets hours that are NaN or infinite to zero by a migration. Here a task that got such hours under an earlier 20.0 version keeps them, and the constraint refuses its copies and its next occurrence until they are corrected.
- The three fields 16.0 deletes with a pre-migration (`difficulty`, `knowledge_area_level`, `tester_id`) are deleted here by their declarations alone: the update of the module drops them with their columns.

**Consequences.**

- A task created straight into a marked stage needs its type and its hours in the same create: the kanban quick create, a mail alias and a task created from a sales order line are refused there, and so is a copy that keeps the stage of a source sitting there without them.
- A recurring task that has no type or no hours cannot be set Done or Cancelled where the first stage of its project is marked: its next occurrence would enter that stage without them.
- A stage listed for a Resolution and for a type and an estimate refuses for the Resolution first.
- The star hidden on a task still orders the lists core sorts by it, and core's priority filters remain.
- With Task Logs (`hr_timesheet`) installed, timesheet users also see the same hours as Allocated Time on the tasks of a project that uses timesheets; the two inputs are one field.

Reversal cost: drop the compute and the rule and make the size editable again.
