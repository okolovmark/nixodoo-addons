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
  each task follows from its next planned end. Installing the module, or
  updating it to 16.0.1.1.0, sets the Deadline of the open tasks (stage not
  folded) of AI-driven projects that have a planned end to the day of that
  end, in the time zone of the user who last changed the task, without
  changing their last-updated date; closed tasks keep theirs.

Users
~~~~~

* Each user sets their **Default Dashboard Project** in Preferences (an
  administrator on the user form); the Backlog menu opens on it.
* The estimated completion of a member card plans on the working schedule
  of the user's employee in the current company, or of the company: give
  each member an employee with a schedule for an estimate that skips
  weekends, leaves and public holidays.

Access
~~~~~~

Team Dashboard tasks are read with the user's project rights, and so are the
logged hours in View by Project mode. In the Manager and Department modes the
logged hours of the listed users (across all projects), the employee hierarchy
and the department members are read with elevated rights; the department
picker list and the viewer's own department are loaded with the user's own
rights.

Who opens which team in those two modes follows the user's employee:

* **View by Manager**: the user themselves, or a manager one of whose
  employees reports to one of the user's employees, directly or further down
  the hierarchy (the employee's *Manager*), in any company.
* **View by Department**: a department the user's employee belongs to, or
  one it is the *Manager* of.
* A user with **Timesheets: User: all timesheets** (Administrator includes
  it) or **Employees: Officer** opens every manager and every department.
  So does every **Project / Administrator**: Odoo gives project
  administrators the first of these rights.

A manager's team is everyone below their employees, the manager left out,
also where the hierarchy loops back: Odoo lets an employee manage their own
manager, and the *Manager* of a department who belongs to it gets themselves
as *Manager*.

The pickers still list every user and department. Any other choice is
refused: the user gets an error message, and the picker goes back to the
manager or department shown before, whose cards stay on screen. Give a team
lead an employee with the right *Manager* for them to see their team.

Gantt chart: dependencies can be linked and lags edited by users with write
access to the dependent task. Only **Project / Administrator** can save a
baseline, and needs write access to the project. Project managers can read
dependency lags and manage baselines directly; project users reach them only
through the Gantt chart.
