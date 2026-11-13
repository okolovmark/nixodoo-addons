On the task form, which opens on Description:

* Set **Priority**, **T-shirt Estimate**, **Task Type**, **Difficulty** and
  **Knowledge Area Level** next to the tags.
* *Checklist* tab: add items, each with an **Assigned to** user and an
  optional deadline. The assignee or the reviewer moves an item with the
  state icons (TODO, Waiting, Done, Cancelled). The task's kanban card shows
  your own completion percentage, only when you have non-cancelled items
  assigned to you.
* *Technical Solution* tab (AI-driven projects): record the Questions, the
  Answers and the Solution.
* *Test Cases* tab: add ordered cases with steps and expected results, set
  the **Tester**, then move each case with the state icons. Marking a case
  Done ticks **Passed**; any other state, Cancelled included, clears it.
* *Pull Requests* tab (AI-driven projects): add the pull requests of the
  task. The list shows the link, state, merge date and deploy date of each.
  A pull request already recorded for another task is picked from the list
  instead of being created again. **PR (old value)** shows above the list
  on a task that has one.
* **Resolution** (AI-driven projects), next to the tags: choose it before
  moving the task to a stage that requires one. Code needs a pull request on
  the task; a task that already has one gets Code by itself.

**Project > Checklist** lists the checklist items of all tasks, opened on
your own TODO items. **Project > Test Cases** lists the test cases of all
tasks, opened on the ones still in TODO.

**Project > Pull Requests** lists all pull requests, the newest first: a
list, cards on a phone. Search by URL, repository or task, filter on Open,
Merged, Closed or No State, group by repository or state. On the form of a
pull request, set **State**, **Merge Date** and **Deploy Date** when it is
merged and deployed.

A tool that writes over RPC sets ``pr_uri`` on the task to the link of the
pull request, one link per write, or ``resolution`` to ``data``,
``configuration`` or ``no_change`` when there is none. A write of another
link adds a second pull request to the task.

The stage log has no view and no menu. A system administrator reads
``project.task.stage.log`` over RPC: the rows come ordered by date, each
with ``task_id``, ``previous_stage_id``, ``stage_id``, ``stage_name``,
``date``, ``user_id`` and ``source`` (``live`` on every row this branch
writes). The log holds the stages of every task, also of tasks a user
cannot read: code that shows it to other users reads it with ``sudo()``,
for the tasks those users can read.
