A team's work in Project at a glance: a **Team Dashboard** of workload
cards per member, a multi-project **Gantt chart** with dependency lags and
baselines, a **Backlog** list, and planned dates of a task that roll up
from its sub-tasks. It builds on ``project_ai_driven`` (task type,
estimate, size, modules of a task, stage log) and on ``hr_timesheet``
(logged hours, employees).

Stages (``project.task.type``) gain two flags: **In Progress Stage**
(tasks there are being worked on; the dashboard picks a member's current
task from them) and **Backlog Stage** (tasks there form the backlog).

**Planned dates and sub-tasks**

A task has a planned start and a planned end (``planned_date_start``,
``planned_date_end``), on the task form and, hidden by default, in the task
list. A task without sub-tasks keeps the planned dates it is given, and
none until it is given some: assigning or closing the task sets none of
them. An end before the start is refused. These are the fields of the OCA
module ``project_timeline``, which this module does not need. With it
installed, its computations are replaced by these, so its rules that take
the assignment date as planned start and the closing date as planned end
do not run (``project_team_dashboard_timeline`` keeps its fields on the
form and list).

A task with active sub-tasks takes its planned start and end from them: the
earliest and the latest planned date of those sub-tasks, or no dates while
none of them is dated. Archived sub-tasks are ignored, the deadline is
never touched, and the roll-up climbs every level (Milestone > Task >
Sub-task). Such a task is flagged ``is_planned_from_subtasks``: its planned
dates are read-only on the task form and list, and a write that changes
them is refused with a message to change the sub-tasks instead.

**Backlog**

**Project > Backlog** lists the tasks in a Backlog stage with Key, Task,
Assignee, Type, Priority, Size and Estimate (h), filters *My Tasks*,
*Unassigned*, *Blocked*, *Overdue* and groupings by Assignee, Start Date,
Project and Type. Project users also search it by **Module**, a module's
technical name or title, as in the task search of ``project_ai_driven``.
It opens filtered on the user's **Default Dashboard Project** and on
unassigned tasks. **Estimate (h)** is the task's **Estimate** in hours,
and **Size** the size ``project_ai_driven`` computes from it.

**Team Dashboard**

**Project > Team Dashboard** is a client action with three modes:

* **View by Project**: one card per user assigned to an open task of the
  project (plus an *Unassigned* card).
* **View by Manager**: cards for the subordinates of a user, taken from the
  employee hierarchy down to a chosen depth (1 to 5 levels or all), across
  every project.
* **View by Department**: cards for the users of a department.

Each card shows the member's current task (the most recently updated task in
an In Progress stage), the queue of other in-progress tasks, the backlog,
other open tasks, open task count, estimated and remaining hours, hours logged
today and in the selected period (This Week, Last Week, This Month, Last
Month or a custom range), and an estimated completion date. The estimate
plans the remaining hours on the employee's working schedule with leaves
taken into account, and falls back to 5.7 hours a day when no employee or
schedule exists. Links open the member's kanban, backlog or closed tasks.

Users carry a **Default Dashboard Project**, editable from their
preferences.

**The Team Dashboard as a record**

A reader that can only search and read records, such as an agent behind
an Odoo connector, gets the numbers of the screen from
``project.team.board`` (*Team Dashboard board*). It stores nothing: its
rows are an SQL view of one row per active project, per manager (an
active user whose employee manages another active employee) and per
active department, each in three periods, *This Week*, *Last Week* and
*This Month*. A row has:

* ``name``: the project, manager or department and the period, e.g.
  "Website · This Week";
* ``mode``: ``project``, ``manager`` or ``department``;
* ``project_id``, ``manager_user_id`` or ``department_id``: the row's
  subject, set for its mode only;
* ``period``: ``this_week``, ``last_week`` or ``this_month``;
* ``data``: exactly what the screen shows for that subject and period,
  computed when it is read and never stored. It is the result of the
  screen's own call (``get_team_dashboard_data``,
  ``get_team_dashboard_by_manager`` with all levels, or
  ``get_team_dashboard_by_department``) for the period's first and last
  day, weeks from Monday to Sunday and the month from its first to its
  last day, on the reader's today in their time zone. The member cards,
  today's and the period's logged hours and the estimated completion
  (planned on the employee's working schedule with leaves and public
  holidays) are in it. Only the period follows the reader's time zone:
  today's logged hours, the overdue tasks and the day the estimated
  completion starts from count on the server's date, as on the screen.

A user finds and reads only the rows they open on the screen; the others
are left out of searches, counts and groups, and reading one by its id
raises an access error. A project row follows the project's own access
(its visibility, or its members where ``project_member_access`` is
installed); a manager row is the user's own or that of a manager below
one of their employees; a department row is that of the user's department
or one they manage. Timesheet approvers, every project manager among them,
and HR officers find every manager and department. Either way a manager or
a department of a company the user may not enter stays hidden, the user's
own team aside, and the screen refuses it too. A user who may not read
timesheets finds no project row, as the screen refuses them a project too.
Reading every field of a project computes no dashboard, as the board is a
model of its own, and a read of the board naming no fields lists its rows
without ``data``. The design record
is ``docs/adr/0016-the-team-dashboard-is-a-record-of-a-sql-view-with-its-data-computed-on-reading.md``
in this repository.

**Gantt Chart**

The **Gantt Chart** view plots the planned dates of the selected projects'
tasks on a timeline: Day, Week or Month scale paged a month at a time (a
week on Day, a quarter on Month), grouped by user (the default), by task
or by stage, optional completed tasks, a counter of tasks without dates,
faceted search with a jump to any shown task by key or name,
several projects at once. Bars take the colour of their stage (closed
stages grey, legend built from the stages shown); an open task past its
planned end gets a red outline. Dependencies are drawn as orange elbow
connectors from the predecessor's end into the successor's start.
**Export Excel** downloads the open tasks of the selected projects that
have planned dates or a deadline as an .xlsx Gantt chart, sorted by start
date; closed tasks are always left out, and the search facets and grouping
are not applied. The chart is drawn with the vis-timeline library, which
the module ships (``static/lib/vis-timeline``, MIT License).

* **Attribute columns**: the left pane can show Assignees, Deadline, Estimate,
  Spent hours and Stage next to the task name, as a fixed-width grid
  with a header row; hours are right-aligned. The choice is stored per user
  (``res.users.gantt_columns``); the first four are on by default. Columns
  are shown only while their total width plus 140 px for the task name fits
  in 60% of the chart width (all five need a chart about 1010 px wide);
  otherwise they are hidden. The pane re-measures after a window resize or a
  column change. A cell tooltip reads "<Column>: <value>"; a header tooltip
  explains the column. The column that repeats the grouping is left out:
  Assignees in Group by User, Stage in Group by Stage; its checkbox is greyed
  out there but keeps the stored choice.
* **Dependencies**: dragging the handle of a task bar onto another bar makes
  the second task depend on the first (core ``depend_on_ids``). Clicking an
  arrow opens a popover to set its dependency lag or remove the link.
* **Dependency lag** (``project.task.dependency.lag``): calendar days per
  dependency, zero or more; no row means lag 0. The chart only checks it: a
  link whose successor starts before the predecessor's end plus the lag is
  drawn red with a warning. No date moves automatically. Copying a task
  keeps the lags of the dependencies that are copied with it; removing a
  dependency removes its lag.
* **Baseline** (``project.baseline`` / ``project.baseline.line``): *Save
  Baseline* (Project Managers) stores the planned start, planned end and
  deadline of every open task with at least one of these dates, one
  baseline per project shown. The new baseline becomes the project's
  **Active Baseline** (``baseline_id``) and replaces the previous one.
  *Show Baseline* draws it as grey bars under the tasks, and the tooltip
  shows the shift of the planned end, e.g. "+3 d vs baseline". Turning
  *Show Baseline* on when no task shown has a baseline raises a warning
  pointing to *Save Baseline*.
* **Sub-tasks**: grouped by task, user or stage, a sub-task row is nested
  under its parent's row when both fall in the same group. Otherwise the
  sub-task is a top-level row labelled "sub-task of <parent key>" (no label
  when the user cannot read the parent). A parent's bar is read-only when its
  dates come from its sub-tasks; other bars move and resize by whole days. In
  Group by User a task and its own sub-tasks never count as conflicting.
* **Unscheduled (N)**: tasks with no planned dates and no deadline are
  listed in their own group at the end of the chart; a click opens the
  task.
* **Milestones**: Milestone-type tasks and tasks with only a deadline are
  drawn as a diamond on their date, and exported as milestones in the
  Excel Gantt chart. A Milestone shown with its sub-task rows and spanning
  more than one date is drawn as a bar.
* **Stage History** (off by default, in every grouping): each task
  row expands into one row per stage visit, in the stage's colour and
  chained by orange connectors. The visits come from the stage log of
  ``project_ai_driven`` (``project.task.stage.log``, *The stage log* in
  that module), which holds every change of a task's stage, with or
  without a line in the chatter, and are shown for the tasks the user can
  see. A row with a stage starts a visit at its date and ends the visit
  before it; a row with no stage only ends the visit before it. The
  history starts at the task's first row. The visit a task is still in
  runs to now, or ends at its own start when its stage is closed (folded).
  A deleted stage keeps its name and shares the colour and the legend
  entry of tasks without a stage. A visit shorter than a day is drawn one
  day wide; history rows are read-only. The task row then shows a
  read-only summary bar from the start of the first visit to the end of
  the last one, in the current stage's colour (the planned dates stay in
  its tooltip), and the User grouping's conflict highlighting is off. A
  task with no row in the log keeps its own bar.

Lags and baselines are read and written only through the Gantt methods,
which check the user's rights on the tasks and projects first.
``readme/gantt_data_model.puml`` draws the models of the Gantt chart; the
design record is
``docs/adr/0015-the-team-dashboard-needs-no-project-timeline-and-adopts-its-records-by-external-id.md``
in this repository.
