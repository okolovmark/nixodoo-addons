A team's work in Project at a glance: a **Team Dashboard** of workload
cards per member, a multi-project **Gantt chart** with dependency lags,
baselines and the stage history of tasks, a **Backlog** list, planned
dates of a task that roll up from its sub-tasks, and, in an AI-driven
project, a Deadline that follows the planned end. It builds on
``project_ai_driven`` (task type, key, size, modules of a task, stage log)
and on ``hr_timesheet`` (logged hours, employees).

Stages (``project.task.type``) gain two flags: **In Progress Stage**
(tasks there are being worked on; the dashboard picks a member's current
task from them) and **Backlog Stage** (tasks there form the backlog).

**What counts**

* A task is closed when its state is Done or Cancelled, as in Odoo; a
  folded stage closes nothing. The cards, the Backlog and the baselines
  take open tasks only, and so does the Gantt chart unless *Show Closed* is
  on.
* Task templates and the sub-tasks under them are left out everywhere.
  Project templates are not offered in the project pickers, and the tasks
  of project templates and tasks without a project are left out of the
  Manager and Department modes and of the Backlog.
* Every day a user sees is a day in the viewer's time zone (UTC when none
  is set): today, what is overdue, the dates on the cards, in the Gantt
  chart and in the Excel export, and the day a deadline-only task is drawn
  on. A member's hours logged *Today* and their estimated completion count
  from the member's own day: their time zone, else their employee's.

**Planned dates and sub-tasks**

A task has a planned start and a planned end (``planned_date_start``,
``planned_date_end``, date and time), shown as **Planned Dates** before the
Deadline on the task form and, hidden by default, in the task list. A task
without sub-tasks keeps the planned dates it is given, and none until it is
given some: assigning or closing the task sets none of them. An end before
the start is refused. A task created from a template gets no planned dates.

A task with active sub-tasks takes its planned start and end from them: the
earliest and the latest planned date of those sub-tasks, or no dates while
none of them is dated. Archived sub-tasks are ignored, and the roll-up
climbs every level (Milestone > Task > Sub-task). Such a task is flagged
``is_planned_from_subtasks``: its planned dates are read-only on the task
form and list, and a write that changes them is refused with a message to
change the sub-tasks instead.

**Deadline**

In a project flagged **AI-driven Project** the Deadline of a task becomes
its planned end, date and time, a rolled-up one included, when the planned
end or the project differs from the plan the Deadline last followed. Each
task records that plan in ``date_deadline_planned_end`` and
``date_deadline_project_id``; a Deadline written by hand records the
current plan, so it stays until the plan changes. Elsewhere, and on a task
without a planned end, the Deadline stays as it is. The task form and its
*Sub-tasks* and *Blocked By* lines carry the plan, so the Deadline follows
the planned end as it is edited there. Installing or updating the module
fills the plan of each task with its current planned end and project, and
changes no Deadline.

**Backlog**

**Project > Backlog** lists the open tasks in a Backlog stage with Key,
Task, Task Type, Priority Level, Size, Assignees, Deadline (the planned
end), Estimate (h) and Tags; Project, Stage, Start (the planned start) and
Modules are optional columns. A row is red once its planned end is past
and grey while the task is Waiting. Filters *My Tasks*, *Unassigned*,
*Blocked* (Waiting) and *Overdue* (planned end before today), groupings by
Assignee, Start Date, Project and Type. Project users also search it by
**Module**, a module's technical name or title, as in the task search of
``project_ai_driven``. It opens filtered on the user's **Default Dashboard
Project** and on unassigned tasks. **Estimate (h)** is the task's
**Estimate** (``allocated_hours``), and **Size** the size
``project_ai_driven`` computes from it.

**Team Dashboard**

**Project > Team Dashboard** is a client action with three modes:

* **Project**: one card per user assigned to an open task of the project,
  plus an *Unassigned* card for its open tasks nobody is assigned to. Only
  this mode offers the Gantt chart.
* **Manager**: cards for the users below a manager (the viewer by default)
  in the employee hierarchy, down to a chosen depth (1 to 5 levels or all),
  across every project.
* **Department**: cards for the users of a department (the viewer's by
  default), across every project.

In the Manager and Department modes only members with an open task get a
card, and each card lists the projects of the member's tasks.

Each card shows the member's current task (the most recently updated task
in an In Progress stage), the queue of other in-progress tasks, the
backlog, other open tasks, the open task count, estimated and remaining
hours, hours logged today and in the selected period (This Week, Last
Week, This Month, Last Month or a custom range), and an estimated
completion date (ETA). The ETA plans the remaining hours on the working
schedule of the member's employee (the company's when the employee has
none) with leaves taken into account, and falls back to 5.7 hours a day,
every day, when there is no employee or no schedule. The date of a task on
a card is its planned end, labelled Deadline, red with *(OVERDUE)* once
that day is past. In Project mode buttons open the project's form, its
unassigned backlog, its tasks and the viewer's tasks in it; a card opens
the member's backlog and kanban (Project mode) and closed tasks.

The dashboard keeps its mode, team, period and view, and the Gantt chart
its settings and the window it shows, across a switch between cards and
chart and a return through the breadcrumb.

Users carry a **Default Dashboard Project**, editable from their
preferences; the dashboard and the Backlog open on it.

**Gantt Chart**

The **Gantt Chart** plots the tasks of the selected projects that have
planned dates or a deadline, with their ancestors, on a timeline: Day, Week
or Month scale paged a month at a time (a week on Day, a quarter on
Month), *Fit All* and *Today*, grouped by user (the default), by task or by
stage, *Show Closed* to add the Done and Cancelled tasks, faceted search
(Assignee, Task, Key, Stage, Type) with a jump to any shown task by key or
name, several projects at once. A task with a single planned date gets a
one-day bar. Bars take the colour of their stage (grey for a stage whose
tasks shown are all closed, legend built from the stages shown) and show
the task's progress; an open task whose bar ends before today gets a red
outline. Grouped by user, the bars of one person that overlap are
highlighted, a task and its own sub-tasks excepted. Dependencies are drawn
as orange elbow connectors from the predecessor's end into the successor's
start. **Export Excel** downloads the open tasks of the selected projects
that have planned dates or a deadline as an .xlsx Gantt chart, sorted by
start, with today's column marked; the search facets and grouping are not
applied. The chart is drawn with vis-timeline 7.7.0, which the module
ships (``static/lib/vis-timeline``, MIT License) and loads on the first
chart shown; the library injects its own styles.

* **Attribute columns**: the left pane can show Assignees, Deadline,
  Estimate, Spent hours and Stage next to the task name, as a fixed-width
  grid with a header row; hours are right-aligned, and the Deadline is red
  when past on an open task. The choice is stored per user
  (``res.users.gantt_columns``); the first four are on by default. Columns
  are shown only while their total width plus 140 px for the task name
  fits in 60% of the chart width (all five need a chart about 1010 px
  wide); otherwise they are hidden. The pane re-measures after a window
  resize or a column change. A cell tooltip reads "<Column>: <value>"; a
  header tooltip explains the column. The column that repeats the grouping
  is left out: Assignees in Group by User, Stage in Group by Stage; its
  checkbox is greyed out there but keeps the stored choice.
* **Moving bars**: a bar moves and resizes by whole days and keeps the time
  of day of its dates. A move writes only the planned dates the task has;
  a resize that drags the edge of a missing date sets it. The bars of
  deadline-only tasks, of tasks planned from their sub-tasks and of tasks
  the user cannot write do not move.
* **Dependencies**: dragging the handle of a task bar onto another bar
  makes the second task depend on the first (core ``depend_on_ids``). Both
  tasks need a project with Task Dependencies on. Clicking an arrow opens a
  popover to set its dependency lag or remove the link.
* **Dependency lag** (``project.task.dependency.lag``): calendar days per
  dependency, zero or more; no row means lag 0. The chart only checks it,
  on the viewer's days: a link whose successor starts before the
  predecessor's end plus the lag is drawn red with a warning. No date moves
  automatically. Copying a task keeps the lags of the dependencies that are
  copied with it; removing a dependency removes its lag.
* **Baseline** (``project.baseline`` / ``project.baseline.line``): *Save
  Baseline* (Project Administrators) stores the planned start, planned end
  and deadline of every open task with at least one of these dates, one
  baseline per project shown. The new baseline becomes the project's
  **Active Baseline** (``baseline_id``) and replaces the previous one.
  *Show Baseline* draws it as grey bars under the tasks still in that
  project, and the tooltip shows the shift of the planned end, e.g. "+3 d
  vs baseline". Turning *Show Baseline* on when no task shown has a
  baseline raises a warning pointing to *Save Baseline*.
* **Sub-tasks**: grouped by task, user or stage, a sub-task row is nested
  under its parent's row when both fall in the same group. Otherwise the
  sub-task is a top-level row labelled "sub-task of <parent key>" (no label
  when the user cannot read the parent).
* **Unscheduled (N)**: open tasks with no planned dates and no deadline are
  listed in a collapsed group at the end of the chart, where a click on a
  name opens the task, and counted in a *tasks without dates* button that
  opens them as a list.
* **Milestones**: Milestone-type tasks and tasks with only a deadline are
  drawn as a diamond on their date, and exported as milestones in the
  Excel Gantt chart. A Milestone shown with its sub-task rows and spanning
  more than one date is drawn as a bar.
* **Stage History** (off by default, in every grouping): each task row
  expands into one row per stage visit, in the stage's colour and chained
  by orange connectors. The visits come from the stage log of
  ``project_ai_driven`` (``project.task.stage.log``), which holds every
  change of a task's stage, with or without a line in the chatter, and are
  shown for the tasks the user can see. A row with a stage starts a visit
  at its date and ends the visit before it; a row with no stage only ends
  the visit before it. The history starts at the task's first row. The
  visit a task is still in runs to now; for a Done or Cancelled task it
  ends when its state or stage last changed, never before it starts. A
  deleted stage keeps its name and shares the colour and the legend entry
  of tasks without a stage. A visit shorter than a day is drawn one day
  wide; history rows are read-only. The task row then shows a read-only
  summary bar from the start of the first visit to the end of the last
  one, in the current stage's colour (the planned dates stay in its
  tooltip), and the overlap highlighting of the User grouping is off. A
  task with no row in the log keeps its own bar.

Project users reach lags and baselines only through the Gantt methods,
which check their rights on the tasks and projects first. The server
methods of the dashboard and the chart (``get_team_dashboard_data``,
``get_team_dashboard_by_manager``, ``get_team_dashboard_by_department``,
``get_gantt_chart_data``) answer with the keys of the 16.0 branch:
``planned_hours`` carries the allocated hours, ``kanban_state`` the task's
state, and ``progress`` is a percentage. ``readme/gantt_data_model.puml``
draws the models of the Gantt chart; the design record is
``docs/adr/0015-project-team-dashboard-on-20-0-follows-the-odoo-20-core-where-it-differs-from-16-0.md``
in this repository.
