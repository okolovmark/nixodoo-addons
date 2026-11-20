Projects
~~~~~~~~

* Tick **Task Dependencies** on the project to link its tasks from the
  Gantt chart.
* On the project's task stages (Project > Configuration > Task Stages,
  developer mode only) tick **In Progress Stage** on the working stages and
  **Backlog Stage** on the backlog stages; the Backlog list and the Team
  Dashboard read these flags.
* Tick **AI-driven Project** on a project for the Deadline of its tasks to
  follow their planned end (see Usage). Ticking it changes no Deadline:
  each task follows from the next change of its planned end. Installing
  the module, or updating it to 16.0.1.1.0, sets the Deadline of the open
  tasks (stage not folded, not archived) of AI-driven projects that have a
  planned end to the day of that end, without changing their last-updated
  date, in the time zone of the first active assignee who has a valid one,
  else of the user who last changed the task if still active, else UTC.
  Closed and archived tasks keep their Deadline until their planned end
  changes.

Users
~~~~~

* Each user sets their **Default Dashboard Project** in Preferences (an
  administrator on the user form); the Backlog menu and the Team Dashboard
  open on it. When it is not among the projects the user reads in the
  companies selected in the switcher, the Backlog opens on every project
  and the Team Dashboard on the first of the projects it lists.
* The estimated completion of a member card plans on the working schedule
  of the user's employee in the current company, or of the company: give
  each member an employee with a schedule for an estimate that skips
  weekends, leaves and public holidays.

Access
~~~~~~

Team Dashboard tasks are read with the user's project rights, and so are the
logged hours in View by Project mode. In the Manager and Department modes the
logged hours of the listed users (their timesheets across all projects of the
selected companies), the employee hierarchy and the department members are
read with elevated rights; the department picker list and the viewer's own
department are loaded with the user's own rights. In every mode the
member's employee and working schedule behind the estimated completion are
read with elevated rights.

Who opens which team in those two modes follows the user's employee:

* **View by Manager**: the user themselves, or a manager one of whose
  employees reports to one of the user's employees, directly or further down
  the hierarchy (the employee's *Manager*), across companies.
* **View by Department**: a department the user's employee belongs to, or
  one it is the *Manager* of.
* A user with **Timesheets: User: all timesheets** (Administrator includes
  it) or **Employees: Officer** opens every manager and every department.
  So does every **Project / Administrator**: Odoo gives project
  administrators the first of these rights.
* In every case, whatever the company rules of employees and departments,
  a department opens only without a company or in one the user has
  selected, and a manager other than the user only with an active employee
  in a company the user has selected; the manager's team itself may reach
  into other companies.
* The user's archived employees open no team, and the cards of every mode
  leave out archived projects, employees and tasks, also for a call that
  asks for archived records.

A manager's team is everyone below their employees, the manager left out,
also where the hierarchy loops back: Odoo lets an employee manage their own
manager, and the *Manager* of a department who belongs to it gets themselves
as *Manager*.

The manager picker lists every internal user. The department picker lists
the departments the user finds: those of the companies they have selected
or of none, where the departments' company rule is active. Any other
choice is refused with an error message, and the picker goes back to the
manager or department shown before, whose cards stay on screen. A team
refused when switching modes leaves the toolbar of that mode, whose picker
opens another team.

A user who opens every manager and department is not refused but reads
why under the toolbar:

* "No employee record found for this user": a manager without an active
  employee, or with active employees only in companies the user may not
  select.
* "No employee record found for this user in the selected companies": a
  manager whose active employees are all outside the selected companies,
  one of them in a company the user may select.
* "Department not in the selected companies": a department of a company
  the user may select but has not selected.
* "Department not found": a department of a company the user may not
  select, or one that no longer exists.

Any user opening their own team without an active employee reads the
first message, one opening a manager who reports to them, with active
employees only outside the selected companies, one of the first two, and
one opening their own department, or one they manage, outside the
selected companies one of the last two; any other manager or department
is refused them. Give a team lead an employee with the right *Manager*
for them to see their team.

Gantt chart: dependencies can be linked and lags edited by users with write
access to the dependent task. Only **Project / Administrator** can save a
baseline, and needs write access to the project. Project managers can read
dependency lags and manage baselines directly; project users reach them only
through the Gantt chart.
