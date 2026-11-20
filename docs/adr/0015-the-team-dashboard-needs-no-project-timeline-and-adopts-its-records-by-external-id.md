# The Team Dashboard needs no project_timeline and adopts its records by external id

Status: accepted (2026-11-18)

The Team Dashboard, the multi-project Gantt chart, the Backlog list, the stage flags *In Progress Stage* and *Backlog Stage* and the roll-up of planned dates from sub-tasks become `project_team_dashboard`, which depends on `project_ai_driven` and `hr_timesheet` only. They were written inside a project-specific module that depended on the OCA modules `project_timeline` and, through it, `web_timeline`, and used two things from them: the vis-timeline library the Gantt chart draws with and the planned date fields of the task, whose computations the roll-up had already replaced. A shared module cannot ask every database for an OCA timeline view it does not use, so the module carries both itself. A database that has `project_timeline` gets `project_team_dashboard_timeline`, a glue module installed by itself.

**The library.** `project_team_dashboard` ships vis-timeline 7.7.0, the copy `web_timeline` ships, unmodified with its MIT license under `static/lib/vis-timeline`, and the Gantt chart loads it from there on demand. It is loaded outside the asset bundles, as before, so no page but the chart pays for its 1.6 MB.

**The planned dates.** `project_team_dashboard` declares `planned_date_start` and `planned_date_end` whole (stored computes, writable, recursive for the roll-up) under the names, compute methods, constraint method and message of `project_timeline`. It does not take over the two rules of `project_timeline` that give a task without dates the assignment date as planned start and the closing date as planned end: those have not run in production since the roll-up replaced their computations, and they stay off (owner's decision, 2026-11-18). A task without sub-tasks keeps the dates it is given; an end before the start is refused. Its `_auto_init` creates the two columns in SQL, empty, when they are missing, so that the ORM does not compute every task one by one to change nothing; the post-init roll-up sets the parents.

Its compute methods call no `super()`, and Odoo calls the method of that name that comes first in the model. A dependency on `hr_timesheet` places the module deeper in the module graph than `project_timeline` (which depends on `project` and `web_timeline` only), so a server that starts with both installed loads it after `project_timeline` and calls its methods. The server process that installs `project_timeline` on a database that has the dashboard loads the installed modules first and `project_timeline` after them, so there `project_timeline`'s methods would come first until the next restart. The glue module, which depends on both and so loads after both in every order, defines the two compute methods again and has them call the dashboard's two compute methods through the dashboard's class, never `super()`, so the roll-up has one body whichever module came last. The field declarations merge whatever the order (`recursive` holds), and Odoo collects the dependencies of every method of the compute's name, those of `project_timeline` (`date_assign`, `date_end`) included: they trigger a computation that leaves a task without sub-tasks as it is. A test of the glue module pins which methods run, in either load order.

Both modules put the fields on the task form and list. `project_team_dashboard` adds them to the core views, read-only on a task planned from its sub-tasks; the glue module keeps the nodes of `project_timeline`, makes them read-only on the same tasks and takes out the copy, so a database with both shows each field once, where it showed it before.

Rejected: depend on `project_timeline`. Every database would install the timeline view and `web_timeline` for two fields and one library.

Rejected: load the library from `web_timeline` without depending on it. Odoo serves the static files of any module on the addons path, installed or not, so it works where the OCA repository is on the path and breaks without a message where it is not.

Rejected: declare the planned dates under new names. The data lives in the columns of `project_timeline`, its timeline view and report read them, and a second pair of fields would have to be kept in step with the first.

**The records keep their ids.** A database may already hold every record of the module under another module, the one it was cut from. The `pre_init_hook` of `project_team_dashboard` hands them over before any data file is loaded: the module that holds the external id of a model of the dashboard (`project.baseline` and its line, `project.task.dependency.lag`) is the holder, and from it the hook takes, by model and name, the external ids the module's data files declare, those of the fields its classes declare (on its own models and on models of other modules; another field the holder keeps on those models stays with it), the SQL and foreign-key constraints registered for its models and fields, and points the adopted views at its own files. Models, fields, views, actions, menus, access and record rules and the unique constraint of the dependency lag keep their database ids, so baselines, lags, the stage flags and every user's default dashboard project stay. The same hook hands the glue module the two views that extend the planned dates of `project_timeline`, from the same holder: the glue module, installed after the dashboard, could no longer tell that holder apart by the models, which the dashboard holds by then. The holder is found by its records, never by name: the shared module cannot know the modules it is cut from, and a holder needs nothing but to depend on it.

Left alone, the data files would create a second copy of every record, and the update of the holder would delete the originals with the rows that point at them.

Rejected: a holder by name, as `take_over_module` of `project_ai_driven` does for its own former module. It names a module of another project in a shared one.

Rejected: find the holder by the record names alone. A name such as `action_team_dashboard` may be another module's own record; only the external id of a model the dashboard defines tells its holder apart.

**Consequences.**

- `project_team_dashboard` reads the task and project `key` that `project_ai_driven` declares (ADR 0014).
- A database that installs `project_timeline` after the dashboard keeps its planned dates as they are: the fill of `project_timeline`'s `_auto_init` runs only on columns it creates. One that installs both at once on existing tasks gets that fill, from `project_timeline`, which loads first.
- A field of `project_timeline` that both modules declare keeps an external id in each; uninstalling one module leaves the column to the other.
- The roll-up runs again at install for every task with sub-tasks, so a database installing the module with tasks already split gets their parents' dates from their sub-tasks at once.
- The glue module goes with either of its two dependencies; the form and list of `project_team_dashboard` then show its own copy of the fields.
- The planned date columns of the task analysis report stay a feature of `project_timeline`.
- The tests `test_01_flow_filling` and `test_invalid_dates` of `project_timeline` expect the assignment and closing dates to fill the planned dates, and that fill is off on purpose: run against the models of a database with the glue module, they fail. They pass where Odoo runs them: they are at-install tests, run while `project_timeline` loads and so before the glue module does.

Reversal cost: depend on `project_timeline` again and drop the declarations and the glue module; the adoption hook does nothing once the records are held.

**2026-11-20:** `project_ai_driven` no longer takes over its own former module (16.0.2.18.0); `take_over_module` stays a helper other modules call.
