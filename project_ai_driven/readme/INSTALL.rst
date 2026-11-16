A database that has installed neither ``project_software`` nor the
project-specific module it was cut from needs nothing beyond installing the
module. What a database that has ``project_task_pull_request`` installed
needs on top of that is under *Coming from project_task_pull_request*. What
the install and the update to 16.0.2.2.0 write to the stage log for the
tasks that exist is under *Filling the stage log*.

The update to 16.0.2.3.0 deletes the fields ``difficulty``,
``knowledge_area_level`` and ``tester_id`` of ``project.task`` with their
columns and the values in them; the install that takes over
``project_software`` or the project-specific module it was cut from deletes
them too. Beforehand the three fields are taken out of the stored views of
this module; a view outside this module that names one of them is not
rewritten: change it by hand.

The update to 16.0.2.4.0 sets the **Size** (``tshirt_estimate``) of every
task, archived tasks included, to the size of its hours
(``planned_hours``); the install that takes over ``project_software`` or the
project-specific module it was cut from does it too. One SQL statement
stores the sizes and writes nothing else on the tasks: the task type, the
hours and the last update date stay as they are. A task without hours ends
with no size, and a size that disagrees with the hours of its task is
replaced by theirs; the former value is not kept. The server log gets one
line with the number of tasks changed; a second run changes none. On a
database that has neither module installed, the install computes the size of
the tasks that exist through the ORM, which writes those tasks.

The update to 16.0.2.4.1 sets to zero the hours (``planned_hours``) of
every task that holds a NaN or an infinity, archived tasks included, and
recomputes what is computed from them: the size, none for zero hours, and
with ``hr_timesheet`` the remaining hours. From this version a write
refuses such hours, so a task holding them could no longer be copied or
recur. No tracking message is posted; the recompute stamps the last update
date of those tasks. The server log gets the number of tasks changed. The
install does the same.

The update to 16.0.2.5.0 converts the **Status** of every test case: TODO
becomes Not run, Done Passed, Cancelled Not applicable, and Waiting stays.
**Passed** (``passed``) is then set from the status wherever it disagrees:
set for Passed, cleared for any other status. The install that takes over
``project_software`` or the project-specific module it was cut from does it
too. SQL statements store the values and write nothing else on the cases:
the last update date stays, and **Verified by**, **Verified on** and
**Verification** stay empty on a converted case. The server log gets the
number of cases converted per former value and of flags corrected; a second
run changes none. Odoo deletes the former values of the selection at the end
of the update. A saved filter, a default or a server action that names a
former value is not rewritten: change it by hand.

Beforehand the buttons of the former states (``change_state_*``) are taken
out of the test case list of the stored task form of this module, in every
translation; the update and the install that takes over would otherwise fail
validating the task form. A view outside this module that names one of them
is not rewritten: change it by hand.

The update to 16.0.2.6.0 renames the checklist model ``project.task.subtask``
to ``project.task.checklist.item``, its table to
``project_task_checklist_item``, and the task fields ``subtask_ids`` and
``kanban_subtasks`` to ``checklist_item_ids`` and ``kanban_checklist``. The
task field ``default_user`` is deleted. The former names are not kept: a
client that names the former model gets *Object project.task.subtask doesn't
exist*. The install that takes over ``project_software`` or the
project-specific module it was cut from renames too. Before the data files
are loaded, SQL renames, in every module:

* the table with its sequence, constraints and indexes, and the
  registrations of the constraints;
* the model and its fields, and the relations, related paths and
  dependencies that name them, the copies of the fields on a model that
  delegates to the task included;
* the model name where a record stores one (messages, followers,
  activities, attachments, views, actions, filters, export and mail
  templates, every stored Many2oneReference) and in the values of stored
  Reference fields;
* the external ids of the model, its fields and their selection values;
* as whole words, the fields in the stored arch of the views of the
  checklist and of the models holding the fields, in every translation, and
  the fields and the model in record rule domains, filters and window
  actions;
* the file recorded on the views loaded from the renamed view file.

The checklist records of this module take these external ids:

====================================  ===========================================
Former external id                    External id
====================================  ===========================================
``access_task_subtask``               ``access_task_checklist_item``
``subtasks_subtype``                  ``checklist_subtype``
``all_subtask_subtype``               ``project_checklist_subtype``
``view_task_kanban_subtasks``         ``view_task_kanban_checklist``
``project_task_subtask_view_form``    ``project_task_checklist_item_view_form``
``project_task_subtask_view_search``  ``project_task_checklist_item_view_search``
``subtask_list_view``                 ``project_task_checklist_item_view_tree``
``action_view_subtask``               ``action_view_checklist_item``
``menu_action_view_subtask``          ``menu_action_view_checklist_item``
====================================  ===========================================

``default_user`` is taken out of the stored views of this module. Every
record keeps its database id, so the items, their activities and the
follower subscriptions to *All checklist updates* stay as they are; the
update to 16.0.2.6.1 then deletes the activities. The server log gets one
line with the count of each kind of rename; a second run changes nothing.

Not rewritten: the code of server actions and scheduled actions, the bodies
of mail templates, the field paths of export lines, the views of other
models, and a context that turns on the search filters of the checklist by
their former names, ``my_subtask`` and ``done_subtask``, now ``my`` and
``todo``. Change them by hand.

Run the update with the server stopped: renaming the table locks it, and
running code reads the former names. Every client, RPC scripts and modules
that name the checklist, moves to the new names in the same deploy.

The update to 16.0.2.6.1 removes the activities of the checklist: the model
no longer has activity fields. Before the data files are loaded, SQL
deletes the activities of checklist items, the external ids ``mail`` holds
on the selection values of those fields and the external ids other modules
hold on the fields; at its end the update deletes the fields, which have no
column. The install that takes over ``project_software`` or the
project-specific module it was cut from does it too. The server log gets
one line with the count of activities and of external ids deleted; a second
run deletes nothing.

From 16.0.2.6.1 a checklist item follows the access of its task, the
companies selected in the company switcher included: the **Checklist** menu
no longer lists the items of a task the user cannot read, nor of a task in
a company that is not selected. Only the assignee or the reviewer of an item
gives it another assignee; a reassignment by another user is refused. The
deploy is one update of the module, with nothing else to run.

**Upgrading from project_software**

``project_software`` remains for one release as an empty module that depends
on this one, so updating it installs this module. Before any data file is
loaded, the ``pre_init_hook`` takes over everything ``project_software``
owns: models, fields, views, access rights, menus and message subtypes keep
their database ids, so checklist items, test cases, field values and
follower subscriptions stay as they are. It renames the flag where the
database stores it: the column, the field on every model that has it, and
the view archs, record rule domains, filters and window actions that name
it.

What the upgrade needs:

* ``addons.yaml`` lists both modules for that release, also when it pins a
  commit.
* The deploy runs an update of ``project_software`` (``-u
  project_software``) with the server stopped: the hook renames the flag's
  column, which running code still reads. A restart without the update
  leaves ``project_software`` and the modules that depend on it unloaded.
* A module that depends on ``project_software`` switches, in the same
  deploy, its dependency and its external id references to
  ``project_ai_driven`` and the flag to ``is_ai_driven``. The hook rewrites
  its stored views; data files with the former name would write it back.
  The view ``project_project_view_form_inherit_software`` is now
  ``project_project_view_form_inherit_ai_driven``.
* Text naming the flag elsewhere (server action code, automated action
  domains, export templates) is not rewritten: change it by hand.
* ``project_software`` is uninstalled, once no installed module depends on
  it, before the release that removes it, and dropped from ``addons.yaml``.

A dependent module that is renamed as well can call three functions of
``odoo.addons.project_ai_driven.hooks`` from its own ``pre_init_hook``:

* ``is_installed(cr, module_name)``: whether a module is installed or marked
  for upgrade.
* ``take_over_module(cr, former, module)``: moves to ``module`` everything
  ``former`` owns: external ids, constraints, many2many tables and the views
  loaded from its files. The caller clears the registry caches.
* ``rename_external_ids(cr, module, renamed_names)``: renames external ids
  of ``module`` from a ``{former name: new name}`` dict.

**Upgrading from the module it was cut from**

On a database from before the features were cut from the project-specific
module, where ``project_software`` was never installed, the hook takes over
only what moved here and renames the flag the same way.

**Coming from project_task_pull_request**

This module declares ``pr_uri`` on the task and ``pr_required_states`` on
the project and the task under the names, the column and the relation table
of OCA ``project_task_pull_request``, and takes its place. The stages marked
on each project stay marked, the tracking history of ``pr_uri`` stays on the
field, and whatever reads ``pr_uri`` or writes a pull request link to it
keeps working. ``pr_uri`` takes one pull request link and no other text: a
tool that writes a note there for a task without a pull request sets
``resolution`` instead.

The text ``project_task_pull_request`` stored in ``pr_uri`` is converted
when this module is updated to 16.0.2.1.0 (migration script) or installed
(``post_init_hook``):

.. list-table::
   :header-rows: 1

   * - Old value
     - Becomes
   * - one pull request link, whatever the case of its scheme and host, with
       or without spaces around it and a closing slash
     - a pull request on the task, Resolution Code
   * - several pull request links
     - one pull request per link, Resolution Code, old value kept
   * - a pull request link without ``https://``
     - a pull request with ``https://`` added, Resolution Code, old value
       kept
   * - a pull request link with more after the number (``/files``, a commit,
       words) or an invisible character before it
     - the pull request itself, Resolution Code, old value kept
   * - a link that is not a pull request link (commit, branch, repository,
       another site, no repository before ``/pull/N``, a number of more than
       nine digits)
     - no pull request, old value kept
   * - a ``memory://`` reference or any other text
     - no pull request, old value kept
   * - empty
     - nothing

*Old value kept* means the whole text is in **PR (old value)**
(``pr_legacy``). Nothing is guessed: a pull request created by the
conversion has no state, one that already exists is linked as it is, and the
Resolution is set to Code only where it is empty. The same link on several
tasks gives one pull request. Afterwards ``pr_uri`` reads the task's first
pull request, or is empty. Archived tasks are converted too. The tasks are
written by SQL, so their last update date does not change, no tracking
message is posted and no constraint runs. The log of the update gives the
number of values of each kind; a second run converts nothing.

This module does not depend on ``project_task_pull_request`` and does not
uninstall it. A module that depended on it, or an administrator, uninstalls
it once this version is installed, not before: the fields and the relation
table stay only because this module declares them too. While it is
installed, its constraint requires a non-empty ``pr_uri`` in the marked
stages of every project, AI-driven or not, so a task without a pull request
cannot enter them, whatever its Resolution.

Its code leaves the addons path in the deploy that uninstalls it. While the
code is there, the labels stored for the shared fields are its own, *PR URI*
and *PR Required States*, and they stay until the first update after it is
gone.

**Filling the stage log**

The tasks that exist get the past of their stages in the stage log when
this module is updated to 16.0.2.2.0 (migration script) or installed
(``post_init_hook``). One SQL statement inserts the rows of every task that
has none, archived tasks included, and writes nothing on the tasks:

.. list-table::
   :header-rows: 1

   * - Task
     - Rows
   * - with stage changes of its own in the chatter
     - the stage it was created in, then one row per tracked change, all
       *From the chatter*
   * - the same, when the last tracked change does not end at the stage the
       task is in
     - one more row: that stage, empty for a task that is in none, at the
       last stage update, without a user, *From the last stage update*
   * - in a stage, without such changes, last stage update 2 seconds or more
       after its creation
     - one row at the last stage update, without a user, *From the last
       stage update*
   * - in a stage, without such changes, otherwise
     - one row at its creation, by its creator, *From the creation date*
   * - without a stage and without such changes
     - nothing

The stage a task was created in is the old value of its first tracked
change, when that is a stage. Its row is dated at the creation of the task,
or at that first change when it is dated earlier, and its user is the
creator of the task. A tracked change keeps the date of its message and the
stage names the chatter tracked; its user is the user of the author of the
message, the one with the lowest id when the author has several, none when
the author has no user. The stage changes of a predecessor, which Odoo posts
on the dependent tasks, are not changes of the task and are left out.

Nothing is guessed. A tracked change that does not start where the one
before ended stays as tracked. The row for the current stage is left out
when the last stage update is missing or earlier than the last tracked
change, and so is any row that would have no date. The fill writes one line
to the server log with the number of rows of each case and of the rows left
out; a second run inserts nothing.

Run the update with the server stopped. A worker that still runs the
previous code changes stages without writing a row, and the fill skips a
task that has rows, so such a change stays out of the log.
