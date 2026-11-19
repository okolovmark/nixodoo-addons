Projects
~~~~~~~~

* Tick **Task Dependencies** on the project to link its tasks from the
  Gantt chart.
* On the project's task stages (Project > Configuration > Task Stages,
  developer mode only) tick **In Progress Stage** on the working stages and
  **Backlog Stage** on the backlog stages; the Backlog list and the Team
  Dashboard read these flags. Folding a stage does not close its tasks:
  their state does.
* Tick **AI-driven Project** on a project for the Deadline of its tasks to
  follow their planned end (see Usage). Ticking it changes no Deadline,
  and neither does installing or updating the module: each task follows
  from the next change of its planned end or its project.

Users
~~~~~

* Each user sets their **Default Dashboard Project** in Preferences (an
  administrator on the user form); the Backlog menu and the Team Dashboard
  open on it.
* The estimated completion of a member card plans on the working schedule
  of the user's employee in the current company, or of the company: give
  each member an employee with a schedule for an estimate that skips
  weekends, leaves and public holidays. A member's *Today* and estimated
  completion start from the member's day, in the time zone of their
  preferences, else of their employee.

Access
~~~~~~

Team Dashboard tasks are read with the user's project rights, and so are the
logged hours in Project mode. In the Manager and Department modes the
logged hours of the listed users (across all projects), the employee
hierarchy and the department members are read with elevated rights; the
department picker list and the viewer's own department are loaded with the
user's own rights. In every mode the member's employee and working schedule
behind the estimated completion are read with elevated rights, and so are
the name of the project and the key of the parent of each task on a card.

Who opens which team in those two modes follows the user's employee:

* **Manager**: the user themselves, or a manager one of whose employees
  reports to one of the user's active employees, directly or further down
  the hierarchy (the employee's *Manager*, through archived employees too),
  across companies.
* **Department**: a department the user's employee belongs to, or one it is
  the *Manager* of.
* A user with **Timesheets: User: all timesheets** (Timesheets
  Administrator includes it) or **Employees: Officer: Manage all
  employees** opens every manager and every department. So does every
  **Project / Administrator**: Odoo gives project administrators the first
  of these rights.
* In every case a department opens only without a company or in one the
  user has selected, and a manager other than the user only with an
  active employee in a company the user has selected; the manager's team
  itself may reach into other companies.

The Team Dashboard board (``project.team.board``) follows the same rules:
every internal user reads it, and finds the rows of the teams and
projects they open on the screen.

A manager's team is everyone below their employees, the manager left out,
also where the hierarchy loops back: Odoo lets an employee manage their own
manager, and the *Manager* of a department who belongs to it gets themselves
as *Manager*.

The pickers still list every user and department. Any other choice is
refused: the user gets an error message, and the picker goes back to the
manager or department shown before, whose cards stay on screen. A user who
opens every manager and department reads why in the dashboard instead:
"No employee record found for this user" for a manager with no active
employee; for one whose employees are all in companies they have not
selected, the same "in the selected companies" when one of those
companies is one they may select, else the plain message. A department
of a company they may select but have not selected gets "Department not
in the selected companies"; one of a company they may not select, or one
that no longer exists, "Department not found". Give a team lead an
employee with the right *Manager* for them to see their team.

Gantt chart: dependencies can be linked and lags edited by users with write
access to the dependent task and read access to the other one. Only
**Project / Administrator** can save a baseline, and needs write access to
the project. Project Administrators read and manage the baselines of the
projects of their companies, or without a company, and read the dependency
lags of the tasks they can read; project users reach them only through the
Gantt chart. These rights are ``ir.access`` rows
(``security/ir.access.csv``).
