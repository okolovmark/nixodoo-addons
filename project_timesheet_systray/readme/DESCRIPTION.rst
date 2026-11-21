A **Timelog** item in the header of the web client runs the timesheet timer
of ``project_timesheet_time_control`` on the user's tasks. A timer is a
timesheet line of the user with a start time and no duration yet. In a
project whose *Timesheets* is off, such a line is a timer only if the user
created it and no later line they logged themselves, started by now, follows
it: one they logged time after was left behind, not left running, and stays
as it is.

* With a timer running, the item shows its elapsed time, and its panel the
  task and its project; *Stop* ends the timesheet line. A timer on a project
  with no task, such as *Start work* on a project form leaves, shows the
  project and the description of its line instead, with no task to open; a
  description over 200 characters is cut to 200, the last an ellipsis (…).
  Where more than one runs, the panel shows the one started last, and
  once it is stopped the next. The timer is the user's own running line,
  shown and stopped also where the rules on timesheet lines hide it from
  them (they unfollowed its task or project), where they cannot read its
  project or no longer read its task, and where its project had *Timesheets*
  turned off after it started. In a project with *Timesheets* on, a line is
  the user's by its *User*, whoever logged it: a line of no hours with a
  start time that a timesheet approver logs for them, even in a project they
  cannot read, shows as their timer, with its project and description, and
  they can stop it. With *Timesheets* off, only a line they created
  themselves runs, as above. A timesheet approver or administrator stops
  only their own timer here. The task's name opens the task only where they
  read it in the companies selected in the browser tab.
* With none running, the panel lists the user's tasks in unfolded stages of
  projects with *Timesheets* on, the most recently logged first, five at a
  time with *Show more* for the rest. Each shows the name of its project,
  even a project the user has no access to. *Start timer* on one stops
  every timer of the user and starts a timesheet line on the task, for the
  user's employee in the task's company. A task whose project had
  *Timesheets* turned off since the list was loaded is refused.

The panel opens over chat windows, 340 pixels wide: a long task or project
name is cut short on its line. On a phone it spans the width of the screen,
under the item.

In a project visible to *Invited internal users*, the rule of *Timesheets /
User: own timesheets only* lets a user log time only as a follower of the
project or of the task. Where a line is refused and the user does not
follow the task, such as an assignee who unfollowed it, *Start timer* makes
them follow the task again and starts the timer: they get the notifications
of the task again, as any follower does, until they unfollow it. A start
that fails changes nothing: the stopped timers run on and the follow is
taken back. *Start work* on a task form and *Resume work* on a timesheet
line of a task (``project_timesheet_time_control``) do the same. Their
*Previous timer* is the user's own running timer, also one the rules hide
from them or one on a project with no task, and it ends on the start; where
more than one runs, they ask, as
``project_timesheet_time_control`` does, to stop them first, from the
Timelog or with *Stop* on each running line of the user's timesheets. A line
added by hand in the task's *Timesheets* tab does not follow the task again:
it stays refused until the user follows the task from its chatter.

The time controls of ``project_timesheet_time_control`` on task and project
forms find the user's running timer also on a project the user cannot read
(from a task form), and where the rules on timesheet lines hide it from the
user (they unfollowed its task while it ran): the form shows *Stop work*,
which ends it.

Where a task or a project has a key (``key``, from ``project_ai_driven``),
the task shows it and the project reads *KEY - name*.
