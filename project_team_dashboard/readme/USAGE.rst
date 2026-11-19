Plan a task
~~~~~~~~~~~

* The **Planned Dates** of a task are above its deadline on the task form;
  show them in the task list from its optional columns.
* A task with active sub-tasks shows its planned dates read-only: plan its
  sub-tasks and the task follows them.
* In an AI-driven project the **Deadline** follows the planned end: when a
  task gets a planned end or its planned end really changes, by hand, in
  the Gantt chart or from its sub-tasks, its Deadline becomes that end,
  date and time. A task moved into an AI-driven project with a planned end
  gets that Deadline too, and so does a copy of a task. Nothing else moves
  the Deadline: not a stage change, not a start moved alone in the Gantt
  chart, not a change to a sub-task that leaves its parent's planned end
  where it was, not the same planned end saved again. A Deadline you set
  by hand, alone or together with the planned end or the project, stays
  until the planned end changes again or the task moves into an AI-driven
  project; so does the Deadline Odoo gives the next occurrence of a
  recurring task. On the task form the Deadline changes as you edit
  the planned end; a Deadline you type after it is the one saved. Changing
  the Deadline alone leaves the plan where it is, and clearing the planned
  end leaves the Deadline. In other projects the planned end does not
  change the Deadline.

Work the backlog
~~~~~~~~~~~~~~~~

**Project > Backlog** opens the open Backlog-stage tasks of your Default
Dashboard Project, filtered on *Unassigned*; remove that filter to see
assigned tasks too. Reorder by drag, filter *My Tasks*, *Blocked* or
*Overdue*, and group by Assignee, Start Date, Project or Type. To assign a
task, open it.

Team Dashboard
~~~~~~~~~~~~~~

**Project > Team Dashboard**:

#. Pick the mode: **Project** (type a project name), **Manager** (pick the
   manager and the **Depth**) or **Department**. Manager and Department
   open only the teams your rights allow (see *Configuration*).
#. Pick the **Period** for the logged-hours figures, or *Custom Range* with
   start and end dates. A week starts on the first day of the week of your
   language (Monday in French, Sunday in English (US)).
#. Read each member card: current task, queue, backlog, estimated and
   remaining hours, hours logged, estimated completion. Click a task to
   open it, or the card's *Backlog*, *Kanban* or *Closed* buttons for the
   member's tasks.
#. In Project mode, switch to the **Gantt Chart** with the chart button
   next to the cards button. Change the **Scale** (Day, Week, Month; the
   Week scale starts each week on your language's first day), page
   earlier or later with the arrows around **Today**, press **Fit All** to
   see every task, **Group** by user (the default), task or stage, press
   **Show Closed** to include Done and Cancelled tasks, add more projects
   with *Add project…*, and press **Export Excel** for an .xlsx (the
   export always leaves out closed tasks).

Opening a task and coming back through the breadcrumb, or switching
between the cards and the chart, keeps the mode, team, period and the
chart as you left them.

Plan in the Gantt chart
~~~~~~~~~~~~~~~~~~~~~~~

* Columns: open the columns menu and tick the attribute columns to show;
  hover a column name there or in the header for what it holds. On a
  narrow screen the columns are hidden until the window is wide enough for
  them, or until fewer are ticked. Grouped by User or Stage, the column
  with the same information (Assignees or Stage) is not shown.
* Find a task: type its key or part of its name in the search box and pick
  it under *Go to task* (or press Enter for the first one); the chart
  scrolls to its bar, selects it and briefly highlights its row. Pick a
  field there (Assignee, Task, Key, Stage, Type) to narrow the chart to
  one of its values.
* Link tasks: hover the predecessor's bar, or tap it on a touch screen,
  drag the handle at its end (the right end, the left one in a
  right-to-left language) and drop it on the dependent task. Click the arrow to enter **Lag, days**
  and press *Save*, or press *Remove* to delete the dependency. A red arrow
  means the dependent task starts too early; move its dates yourself.
* Project Administrators press **Save Baseline** to store the current plan;
  it replaces the project's previous baseline. Press **Show Baseline** to
  compare the plan against it: the baseline is drawn as a thin bar under
  each task bar. When none of the tasks shown has a baseline yet, a warning
  says so and points to Save Baseline.
* Move or resize a bar to replan a task by whole days. A move changes only
  the planned dates the task has; drag the padded edge of a one-day bar to
  give the task its missing date. The bar of a task planned from its
  sub-tasks does not move: drag its sub-tasks.
* Press **Stage History** to see under each task the stages it went
  through and how long it stayed in each. Every stage change the stage log
  holds counts, whoever made it: a move by a script or an assistant that
  left no line in the chatter shows too. The log starts when
  ``project_ai_driven`` is installed: a task older than that whose stage
  has not changed since keeps its own bar.
* Expand **Unscheduled (N)**, or press the *tasks without dates* button, to
  find tasks without any date and open them to plan them.
