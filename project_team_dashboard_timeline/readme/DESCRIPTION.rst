Glue between ``project_team_dashboard`` and the OCA module
``project_timeline``, installed by itself on a database that has both.

Both modules declare the planned start and end of a task and put them on
the task form and list. With this module the form and the list show the
fields of ``project_timeline`` once, as they look without the dashboard,
and read-only on a task planned from its sub-tasks; the copy
``project_team_dashboard`` adds is taken out. The values follow
``project_team_dashboard`` (*Planned dates and sub-tasks* there): a task
without sub-tasks keeps the dates it is given, and the rules of
``project_timeline`` that take the assignment and closing dates do not
run. That holds also in the server process that installs
``project_timeline`` after the dashboard, where its computations would
otherwise come first until the next restart.
