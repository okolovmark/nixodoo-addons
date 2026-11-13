On the task form:

* Set **Priority**, **T-shirt Estimate**, **Task Type**, **Difficulty** and
  **Knowledge Area Level** next to the tags.
* *Checklist* tab: add items, each with an **Assigned to** user and an
  optional deadline. The assignee or the reviewer moves an item with the
  state icons (TODO, Waiting, Done, Cancelled). The task's kanban card shows
  your own completion percentage, only when you have non-cancelled items
  assigned to you.
* *Test Cases* tab: add ordered cases with steps and expected results, set
  the **Tester**, then move each case with the state icons. Marking a case
  Done ticks **Passed**; any other state, Cancelled included, clears it.
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

**Project > Checklist** lists the checklist items of all tasks, opened on
your own TODO items. **Project > Test Cases** lists the test cases of all
tasks, opened on the ones still in TODO.

**Project > Pull Requests** lists the pull requests of all tasks, the newest
first: a list, cards on a phone. Search by URL, repository or task, filter
on Open, Merged, Closed or No State, group by repository or state. On the
form of a pull request, set **State**, **Merge Date** and **Deploy Date**
when it is merged and deployed.

A tool that writes over RPC sets ``pr_uri`` on the task to the link of the
pull request, one link per write, or ``resolution`` to ``data``,
``configuration`` or ``no_change`` when there is none. A write of another
link adds a second pull request to the task.
