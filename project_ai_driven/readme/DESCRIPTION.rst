Additions to project tasks for AI-driven development: task attributes,
clarifying questions with their answers and the technical solution, a
checklist and test cases per task, pull requests as records, and a
Resolution that a task needs to enter the stages its project marks. The
module also logs the stage changes of every task of the database.

**Task attributes** (``project.task``)

* **Priority** (Highest to Lowest), **T-shirt Estimate** (XS = 1h or less, S
  = 4h, M = 1d, L = 3d, XL = 1w, XXL = 2w or more), **Task Type** (Feature,
  Bug, Improvement, Investigation, Milestone, Support, Technical Debt),
  **Difficulty** and **Knowledge Area Level**, next to the tags on the task
  form. The type is also a column of the task list; its tracking messages
  name the field **AI-driven Task Type**.
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

**Pull requests** (``project.pull.request``)

A pull request is a record of its own, shared by every task it belongs to:

* **URL**, required and unique, stored in canonical form: scheme and host in
  lower case, then the path as written, up to and including the number.
  ``/files``, a query, a fragment or a closing slash after the number are
  dropped.
* **Repository** and **Number**, read from the URL. The record is named
  ``<repository>#<number>``, for example ``owner/repo#12``, and is found by
  that name or by a part of its URL.
* **State**: Open, Merged or Closed. A new record is Open.
* **Merge Date**, **Deploy Date** and the **Tasks** it is linked to.

A pull request link is an http(s) URL with a repository path followed by
``/pull/N``, ``/merge_requests/N`` (also ``/-/merge_requests/N``),
``/pull-requests/N`` or ``/pulls/N``, N being a number of at most nine
digits.

Internal users read pull requests; users with the **User** access of
*Project* also create and edit them. No access right allows deleting one,
and portal users have no access.

**Pull requests and Resolution on a task** (``project.task``)

* **Pull Requests** (``pull_request_ids``): the pull requests of the task,
  on a tab of their own on the tasks of an AI-driven project.
* **Resolution** (``resolution``): how the task was closed: Code, Data,
  Configuration or No change. It is shown on the tasks of an AI-driven
  project, and its changes are tracked in the chatter.
* **First Pull Request** (``pr_uri``) reads the link of the task's first
  pull request, the one created first, and is empty when the task has none.
  It is on no view of this module. It is stored, so it can be searched, its
  changes are tracked in the chatter, and it is writable for the tools that
  write it over RPC. Writing one pull request link finds the record with
  that link or creates it, and adds it to the task; a link the task already
  has changes nothing, and neither does an empty value. Anything that is not
  exactly one pull request link (other text, a link without ``http(s)://``,
  a commit or branch link, two links) is refused: a task closed without a
  pull request takes the Resolution Data, Configuration or No change
  instead.
* **PR (old value)** (``pr_legacy``), read-only: text that a database
  upgraded from 16.0 holds there. Nothing on this branch writes it.
* A copy that keeps the stage of its source takes the Resolution and the
  pull requests of its source, unless the copy is given its own. That is a
  plain Duplicate, and every task of a duplicated project. A copy sent to
  another stage and the copy of a task without a stage take nothing. The
  next occurrence of a recurring task takes nothing either, unless it starts
  in the stage of the task it repeats. The old value is never copied.

**The closing rule**

On an AI-driven project, **Stages Requiring a Resolution**
(``pr_required_states``) marks stages. A task of that project enters a
marked stage only with a Resolution:

==============================  ============  ===============================
Resolution                      Pull request  Result
==============================  ============  ===============================
empty                           none          refused: choose a Resolution
empty                           present       let through, Resolution = Code
Code                            none          refused: add a pull request
Code                            present       let through
Data, Configuration, No change  none          let through
Data, Configuration, No change  present       let through, pull request kept
==============================  ============  ===============================

The rule is checked when the stage of the task changes: on creation, on a
write of the stage and on a change of project that moves the task to another
stage. The Resolution and the pull request may come in the same write as the
stage. Any other stage, a project that is not flagged AI-driven and a write
that leaves the stage as it is let everything through, so a task already
sitting in a marked stage stays editable. The ``state`` of a task (Done,
Cancelled) is not part of the rule: a task set Done outside the marked
stages needs no Resolution. No context key skips the rule.

**The stage log** (``project.task.stage.log``)

One row when a task is created with a stage and one row on every change of
its stored stage, for every task of the database, in an AI-driven project
or not. The rows are written by SQL where the row of the task is written, in
``_create`` and ``_write_multi`` of ``project.task``. No context
(``tracking_disable``, ``mail_notrack``, ``mail_create_nolog``) and no
``sudo()`` goes around the log, and a stage recomputed by a change of
project is logged too, on the task and on the subtasks moved with it.

* **Task**, **Date** and **User**: the task, when its stage changed and who
  changed it. Deleting a task deletes its rows.
* **Stage** and **Stage Name**: the stage the task entered. The stage is
  empty when the task left its stage, and when the stage was deleted since;
  a deleted stage keeps its name.
* **Previous Stage**: the stage the task left. It is empty when there was
  none and when it was deleted since.
* **Source**: *Recorded* on every row this branch writes. *From the
  chatter*, *From the last stage update* and *From the creation date* are
  on the rows of a database upgraded from 16.0.

A task created without a stage gets no row. A task that loses its stage
gets a row with an empty stage. A write that carries the stage the task
already has writes no row. A task that existed before the module was
installed has no row until its stage changes; that first row names the
stage it left as the previous one.

Only system administrators (``base.group_system``) read the log, and no
access right allows creating, changing or deleting a row. The log has no
view and no menu. A search of the log first flushes the stage changes that
are pending.

**Coming from 16.0**

The module's models, fields and selection values carry the names of the
16.0 branch, so a caller written for one branch works on the other. What
differs on 20.0:

* The module has no install hook and no migration. The 16.0 branch takes
  over the records of the module's former name, turns old ``pr_uri`` text
  into pull request records and fills the stage log with the past of the
  tasks. This branch does none of it.
* A task opens on Description; Checklist, Technical Solution, Test Cases
  and Pull Requests follow it. On 16.0 Checklist is the first tab.
* **AI-driven Project** and **Stages Requiring a Resolution** are settings
  under *Tasks Management* on the project form.
* A plain Duplicate keeps the stage of its source on Odoo 20, so it takes
  the Resolution and the pull requests as described above. On 16.0 only the
  tasks of a duplicated project keep their stage.
* A task has a ``state`` next to its stage on Odoo 20. The closing rule
  does not read it.
* The rows of the stage log are written in ``_create`` and
  ``_write_multi``, on 16.0 in ``_create`` and ``_write``.
* The hours a T-shirt estimate fills are the task's ``allocated_hours``
  (*Allocated Time*), the name ``planned_hours`` has in Odoo 20. On 16.0 the
  value is kept only with Timesheets installed, where the field is on the
  task form; on 20.0 it is always saved.
* Odoo 20 itself skips the assignment notification under
  ``mail_auto_subscribe_no_notify``; on 16.0 the module adds that. Odoo 20
  does not refuse a message from an account without an email address:
  without the key such an account's writes go through and the notification
  is queued without a sender address.
