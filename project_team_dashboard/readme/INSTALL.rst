A database that has none of this module's records needs nothing beyond
installing it. The install creates the planned date columns of the task
when ``project_timeline`` has not created them, empty, and rolls the
planned dates of every task with sub-tasks up from them; it logs ``Planned
dates rolled up from sub-tasks on <n> tasks``.

**A database that holds these records under another module**

The Backlog, the Team Dashboard and the Gantt chart may be installed
already under another module, which then hands them over: that module drops
them from its code and depends on this one, and its update installs this
module first.

* Update that module with the server stopped. Before any data file is
  loaded, the ``pre_init_hook`` takes the records that module holds under
  the external ids of this module (models, fields, views, actions, menus,
  access and record rules, the constraint of the dependency lag), with the
  constraints and foreign keys registered for them, so every record keeps
  its database id: baselines, dependency lags, the stage flags and the
  default dashboard project of every user stay. The module is the one that
  holds the external id of ``project.baseline``.
* The same hook hands the two views of that module that extend the planned
  dates of ``project_timeline`` to ``project_team_dashboard_timeline``,
  which installs itself in the same update and loads its views over them.
  Put both modules on the addons path: without the glue module, those two
  views stay as they are and the form shows the planned dates twice.
* A database where no other module holds the external id of
  ``project.baseline``, ``project.baseline.line`` or
  ``project.task.dependency.lag`` is left untouched, even where a module
  holds records of the same names, and a second run finds nothing to move.
