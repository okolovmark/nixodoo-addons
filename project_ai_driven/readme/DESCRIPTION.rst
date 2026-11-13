Additions to project tasks for AI-driven development: task attributes,
clarifying questions with their answers and the technical solution, a
checklist and test cases per task.

**Task attributes** (``project.task``)

* **Priority** (Highest to Lowest), **T-shirt Estimate** (XS = 1h or less, S
  = 4h, M = 1d, L = 3d, XL = 1w, XXL = 2w or more), **Task Type** (Feature,
  Bug, Improvement, Investigation, Milestone, Support, Technical Debt),
  **Difficulty** and **Knowledge Area Level**, next to the tags on the task
  form. The type is also a column of the task list.
* Picking a T-shirt estimate fills *Allocated Time* while it is empty: 1, 4,
  8, 24, 40 or 80 hours. The task form always carries that field, so the
  value is saved with the task; the field is displayed once Timesheets is
  installed, on the tasks of a project that uses timesheets.
* A **Technical Solution** tab with Questions, Answers and Solution, shown to
  project users on the tasks of a project flagged **AI-driven Project**.

**Checklist** (``project.task.subtask``)

Task-level checklist items with a description, an **Assigned to** user, a
**Reviewer** (the creator), a deadline and a state: TODO, Waiting, Done,
Cancelled. Only the reviewer and the assignee may change an item's state or
description. Every creation, state, name or assignee change posts a message
to the task addressed to the reviewer and/or the assignee, under the internal
*All checklist updates* subtype (a change on an item whose reviewer and
assignee are both the current user is a plain note instead). The subtype is
not subscribed by default: other followers receive these posts only after
opting into *All checklist updates* on the task or on its project. The
``mail_auto_subscribe_no_notify`` context key silences these posts, the way
it silences the assignment notification of a task in Odoo itself: an
integration writes tasks and checklist items with it and nobody is notified.
The task's kanban card shows the current user's completion percentage over
their non-cancelled items and the number of their TODO and Waiting items. The
**Checklist** menu opens on your own TODO items (the *My* and *TODO* filters
are on by default); remove those filters to see all items.

**Test cases** (``project.task.testcase``)

Ordered test cases per task with Steps to execute, Expected results,
Comments, a Passed flag and the same four states. The task carries a
**Tester**, general comments and a completion percentage over non-cancelled
cases. The **Test Cases** menu opens filtered on the cases still in *TODO*;
remove the *TODO* filter to see them all. Copying a task also copies its
checklist items and test cases, keeping their names and states.

**Coming from 16.0**

Models, fields, methods and selection values are the ones of the 16.0
branch. What differs on 20.0:

* The 16.0 branch takes over the records of ``kaertech_project_software``,
  the module these features were cut from, when it is installed on a
  database that has it. No 20.0 database ever had that module, so this
  branch has no such step: no ``pre_init_hook``, nothing to adopt.
* The hours a T-shirt estimate fills are the task's ``allocated_hours``
  (*Allocated Time*), the name ``planned_hours`` has in Odoo 20. In 16.0 the
  value was kept only with Timesheets installed, because only then was the
  field on the task form; in 20.0 it is always saved.
* Odoo 20 itself skips the assignment notification under
  ``mail_auto_subscribe_no_notify``, so the module no longer overrides it,
  and Odoo 20 no longer refuses a message from an account without an email
  address: without the key such an account's writes go through and the
  notification is queued without a sender address.
