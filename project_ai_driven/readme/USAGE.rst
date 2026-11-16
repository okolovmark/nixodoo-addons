On the task form:

* Set **Priority**, **Estimate** (in hours) and **Task Type** next to the
  tags. **Size** shows the size of the estimate and cannot be edited.
* *Checklist* tab: add items, each with an **Assigned to** user and an
  optional deadline. The assignee or the reviewer moves an item with the
  state icons (TODO, Waiting, Done, Cancelled). The task's kanban card shows
  your own completion percentage, only when you have non-cancelled items
  assigned to you.
* *Test Cases* tab: add ordered cases with steps and expected results, then
  give each case its result with the icons of its row; a row offers the
  states other than its own (Passed, Failed, Not run, Waiting, Not
  applicable). **Verified by** and **Verified on** show who gave the last
  Passed or Failed. On a phone the tab shows cards with the same buttons.
  Set **Verification** to Automated test or Demo, and the **Automated test
  reference**, on the form of the case.
* *Technical Solution* tab (AI-driven projects): record the Questions, the
  Answers and the Solution.
* *Pull Requests* tab (AI-driven projects): add the pull requests of the
  task, each with its link, state, merge date and deploy date. A pull
  request already recorded for another task is picked from the list instead
  of being created again. **PR (old value)** shows above the list on a task
  that has one.
* **Resolution** (AI-driven projects), next to the tags: choose it before
  moving the task to a stage that requires one. Code needs a pull request on
  the task; a task that already has one gets Code by itself.
* **Task Type** and **Estimate** (AI-driven projects): set both before
  moving the task to a stage that requires them. The refusal names what is
  missing.

**Project > Checklist** lists the checklist items of all tasks, opened on
your own TODO items.

**Project > Test Cases** lists the test cases of all tasks, opened on the
ones Not run: a list, cards on a phone. Filter on Not run or Failed, group
by project, task, status or verification. The header of a case's form
gives it its result with the same buttons, next to its status bar.

**Project > Pull Requests** lists the pull requests of all tasks, the newest
first: a list, cards on a phone. Search by URL, repository or task, filter
on Open, Merged, Closed or No State, group by repository or state. On the
form of a pull request, set **State**, **Merge Date** and **Deploy Date**
when it is merged and deployed.

A tool that writes over RPC sets ``pr_uri`` on the task to the link of the
pull request, one link per write, or ``resolution`` to ``data``,
``configuration`` or ``no_change`` when there is none. A write of another
link adds a second pull request to the task. The estimate is written to
``planned_hours`` and the type to ``task_type``; a ``tshirt_estimate`` sent
with them is ignored. The estimate is a finite number: a NaN or an infinity
is refused, in any project.

A tool that gives test case results over RPC writes ``state`` on
``project.task.testcase``: ``not_run``, ``passed``, ``failed``, ``waiting``
or ``not_applicable``. A create or write of ``passed`` or ``failed`` fills
``verified_by_id`` and ``verified_on`` unless it sends them. It sends
``verification_kind`` (``manual``, ``automated`` or ``demo``) and
``test_reference`` with the result: only the buttons set ``manual`` by
themselves. The former values ``todo``, ``done`` and ``cancelled`` are taken
as ``not_run``, ``passed`` and ``not_applicable``. ``passed`` is read, not
written: a value sent for it is dropped, and ``passed`` true without a
``state`` gives ``passed``.

The stage log has no view and no menu. A system administrator reads
``project.task.stage.log`` over RPC: the rows come ordered by date, each
with ``task_id``, ``previous_stage_id``, ``stage_id``, ``stage_name``,
``date``, ``user_id`` and ``source`` (``live``, ``tracking``, ``stage_date``
or ``creation``). The log holds the stages of every task, so code that
shows it to other users reads it with ``sudo()``, for the tasks those users
can read.
