Additions to project tasks for AI-driven development: task attributes,
clarifying questions with their answers and the technical solution, a
checklist and test cases per task.

**Task attributes** (``project.task``)

* **Priority** (Highest to Lowest), **T-shirt Estimate** (XS = 1h or less, S
  = 4h, M = 1d, L = 3d, XL = 1w, XXL = 2w or more), **Task Type** (Feature,
  Bug, Improvement, Investigation, Milestone, Support, Technical Debt),
  **Difficulty** and **Knowledge Area Level**, next to the tags on the task
  form. The type is also a column of the task list; its tracking messages
  name the field **AI-driven Task Type**.
* Picking a T-shirt estimate fills *Initially Planned Hours* while it is
  empty: 1, 4, 8, 24, 40 or 80 hours. The value is kept only where that field
  is on the task form, which is the case once Timesheets is installed.
* A **Technical Solution** tab with Questions, Answers and Solution, shown to
  project users on the tasks of a project flagged **AI-driven Project**.
* Assignment notifications honour the ``mail_auto_subscribe_no_notify``
  context, so an account without an email address (an RPC service account)
  can assign tasks without failing.

**Checklist** (``project.task.subtask``)

Task-level checklist items with a description, an **Assigned to** user, a
**Reviewer** (the creator), a deadline and a state: TODO, Waiting, Done,
Cancelled. Only the reviewer and the assignee may change an item's state or
description. Every creation, state, name or assignee change posts a message
to the task addressed to the reviewer and/or the assignee, under the internal
*All checklist updates* subtype (a change on an item whose reviewer and
assignee are both the current user is a plain note instead). The subtype is
not subscribed by default: other followers receive these posts only after
opting into *All checklist updates* on the task or on its project. The same
context key silences these posts for an account without an email address.
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
checklist items and test cases, keeping their states.

**Former names**

``project_software`` is the former name of this module and
``is_software_project`` the former name of its flag; the features were first
cut from the module ``kaertech_project_software``. What a database that has
either of them installed needs is under *Installation*.
