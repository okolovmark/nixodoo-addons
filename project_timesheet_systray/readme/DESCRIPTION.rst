A **Timelog** item in the header of the web client runs the timesheet timer
of ``project_timesheet_time_control`` on the user's tasks. A timer is a
timesheet line of the user with a start time and no duration yet.

* With a timer running, the item shows its elapsed time, and its panel the
  task and its project; *Stop* ends the timesheet line. The timer is the
  user's own running line, shown and stopped also where the rules on
  timesheet lines hide it from them (they unfollowed its task), where they
  cannot read its project or no longer read its task, and where its project
  had *Timesheets* turned off after it started. A timesheet approver or
  administrator stops only their own timer here. The task's name opens the
  task only where they read it in the companies selected in the web client.
* With none running, the panel lists the user's tasks in unfolded stages of
  projects with *Timesheets* on, the most recently logged first, five at a
  time with *Show more* for the rest. Each shows the name of its project,
  even a project the user has no access to. *Start timer* on one stops
  every timer of the user and starts a timesheet line on the task, for the
  user's employee in the task's company. A task whose project had
  *Timesheets* turned off since the list was loaded is refused.

In a project visible to *Invited internal users*, the rule of *Timesheets /
User: own timesheets only* lets a user log time only as a follower of the
project or of the task. Where a line is refused and the user does not
follow the task, such as an assignee who unfollowed it, *Start timer* makes
them follow the task again and starts the timer: they get the notifications
of the task again, as any follower does, until they unfollow it. A start
that fails changes nothing: the stopped timers run on and the follow is
taken back. *Start work* on a task form and *Resume work* on a timesheet
line of a task (``project_timesheet_time_control``) do the same, and end a
running timer of the user the rules hide from them. A line
added by hand in the task's *Timesheets* tab does not: it stays refused
until the user follows the task from its chatter.

The time controls of ``project_timesheet_time_control`` on task and project
forms find the user's running timer also on a project the user cannot read.
On a task form they find it also where the rules on timesheet lines hide it
from the user (they unfollowed the task while it ran): the form shows
*Stop work*, which ends it.

The task key and the project key (``key``) are shown where a module of the
database declares them, such as OCA ``project_key``: the project then reads
*KEY - name*.
