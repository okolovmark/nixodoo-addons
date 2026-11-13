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

**Pull requests** (``project.pull.request``)

A pull request is a record of its own, shared by every task it belongs to:

* **URL**, required and unique, stored in canonical form: scheme and host in
  lower case, then the path as written, up to and including the number.
  ``/files``, a query, a fragment or a closing slash after the number are
  dropped.
* **Repository** and **Number**, read from the URL. The record is named
  ``<repository>#<number>``, for example ``owner/repo#12``, and is found by
  that name or by a part of its URL.
* **State**: Open, Merged or Closed. A new record is Open; one created by
  the conversion described under *Installation* has no state.
* **Merge Date**, **Deploy Date** and the **Tasks** it is linked to.

A pull request link is an http(s) URL with a repository path followed by
``/pull/N``, ``/merge_requests/N`` (also ``/-/merge_requests/N``),
``/pull-requests/N`` or ``/pulls/N``, N being a number of at most nine
digits.

Internal users read pull requests; **Project / User** also creates and
edits them. No access right allows deleting one, and portal users have no
access.

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
* **PR (old value)** (``pr_legacy``), read-only: the whole text ``pr_uri``
  held before the conversion described under *Installation*, where that text
  was anything but one plain pull request link.
* A duplicated task starts without pull requests, Resolution and old value.
  A copy that keeps the stage of its source, as every task and subtask of a
  duplicated project does, takes the Resolution and the pull requests of its
  source.

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
sitting in a marked stage stays editable. No context key skips the rule.

**The stage log** (``project.task.stage.log``)

One row when a task is created with a stage and one row on every change of
its stored stage, for every task of the database, in an AI-driven project
or not. The rows are written by SQL where the row of the task is written,
so no context (``tracking_disable``, ``mail_notrack``,
``mail_create_nolog``) and no ``sudo()`` goes around the log, and a stage
recomputed by a change of project is logged too, on the task and on the
subtasks moved with it.

* **Task**, **Date** and **User**: the task, when its stage changed and who
  changed it. The user is empty when it is not known. Deleting a task
  deletes its rows.
* **Stage** and **Stage Name**: the stage the task entered. The stage is
  empty when the task left its stage, and when the stage was deleted since;
  a deleted stage keeps its name.
* **Previous Stage**: the stage the task left. It is empty when there was
  none, when it is not known and when it was deleted since.
* **Source**: *Recorded* for a row written with the change; *From the
  chatter*, *From the last stage update* or *From the creation date* for a
  row of the past, filled as described under *Installation*.

A task created without a stage gets no row. A task that loses its stage
gets a row with an empty stage. A write that carries the stage the task
already has writes no row.

Only system administrators read the log, and no access right allows
creating, changing or deleting a row. The log has no view and no menu. A
search of the log first flushes the stage changes that are pending.

**Former names**

``project_software`` is the former name of this module and
``is_software_project`` the former name of its flag; the features were first
cut from the module ``kaertech_project_software``. What a database that has
either of them installed needs is under *Installation*.
