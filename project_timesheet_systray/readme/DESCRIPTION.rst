A **Timelog** item in the header of the web client runs the timesheet timer
of ``project_timesheet_time_control`` on the user's tasks. A timer is a
timesheet line of the user with a start time and no duration yet.

* With a timer running, the item shows its elapsed time, and its panel the
  task and its project; *Stop* ends the timesheet line. The timer shows and
  stops wherever the user reads its timesheet line, also when they cannot
  read its project or no longer read its task.
* With none running, the panel lists the user's tasks in unfolded stages,
  the most recently logged first, five at a time with *Show more* for the
  rest. Each shows the name of its project, even a project the user has
  no access to. *Start timer* on one stops every timer of the user and
  starts a timesheet line on the task, for the user's employee in the
  task's company.

In a project visible to *Invited internal users*, the rule of *Timesheets /
User: own timesheets only* lets a user log time only as a follower of the
project or of the task. Where a line is refused and the user does not
follow the task, such as an assignee who unfollowed it, *Start timer* makes
them follow the task again and starts the timer: they get the notifications
of the task again, as any follower does, until they unfollow it. A start
that fails changes nothing: the stopped timers run on and the follow is
taken back.

The task key and the project key (``key``) are shown where a module of the
database declares them, such as OCA ``project_key``: the project then reads
*KEY - name*.
