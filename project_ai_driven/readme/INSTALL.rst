A database needs nothing beyond installing the module. What a database that
has ``project_task_pull_request`` installed needs on top of that is under
*Coming from project_task_pull_request*. What the install and the update to
16.0.2.2.0 write to the stage log for the tasks that exist is under *Filling
the stage log*.

The update to 16.0.2.3.0 deletes the fields ``difficulty``,
``knowledge_area_level`` and ``tester_id`` of ``project.task`` with their
columns and the values in them. Beforehand the three fields are taken out
of the stored views of this module; a view outside this module that names
one of them is not rewritten: change it by hand.

The update to 16.0.2.4.0 sets the **Size** (``tshirt_estimate``) of every
task, archived tasks included, to the size of its hours
(``planned_hours``). One SQL statement stores the sizes and writes nothing
else on the tasks: the task type, the hours and the last update date stay
as they are. A task without hours ends with no size, and a size that
disagrees with the hours of its task is replaced by theirs; the former value
is not kept. The server log gets one line with the number of tasks changed;
a second run changes none. The install computes the size of the tasks that
exist through the ORM, which writes those tasks.

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
One SQL statement stores the values and writes nothing else on the cases:
the last update date stays, and **Verified by**, **Verified on** and
**Verification** stay empty on a converted case. The server log gets the
number of cases converted per former value; a second run changes none. Odoo
deletes the former values of the selection at the end of the update. A
saved filter, a default or a server action that names a former value is not
rewritten: change it by hand.

Beforehand the buttons of the former states (``change_state_*``) are taken
out of the test case list of the stored task form of this module, in every
translation; the update would otherwise fail validating the task form. A
view outside this module that names one of them is not rewritten: change it
by hand.

The update to 16.0.2.6.0 renames the checklist model ``project.task.subtask``
to ``project.task.checklist.item``, its table to
``project_task_checklist_item``, and the task fields ``subtask_ids`` and
``kanban_subtasks`` to ``checklist_item_ids`` and ``kanban_checklist``. The
task field ``default_user`` is deleted. The former names are not kept: a
client that names the former model gets *Object project.task.subtask doesn't
exist*. Before the data files are loaded, SQL renames, in every module:

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

.. list-table::
   :header-rows: 1
   :widths: 36 43

   * - Former external id
     - External id
   * - ``access_task_subtask``
     - ``access_task_checklist_item``
   * - ``subtasks_subtype``
     - ``checklist_subtype``
   * - ``all_subtask_subtype``
     - ``project_checklist_subtype``
   * - ``view_task_kanban_subtasks``
     - ``view_task_kanban_checklist``
   * - ``project_task_subtask_view_form``
     - ``project_task_checklist_item_view_form``
   * - ``project_task_subtask_view_search``
     - ``project_task_checklist_item_view_search``
   * - ``subtask_list_view``
     - ``project_task_checklist_item_view_tree``
   * - ``action_view_subtask``
     - ``action_view_checklist_item``
   * - ``menu_action_view_subtask``
     - ``menu_action_view_checklist_item``

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
column. The server log gets one line with the count of activities and of
external ids deleted; a second run deletes nothing.

From 16.0.2.6.1 a checklist item follows the access of its task, the
companies selected in the company switcher included: the **Checklist** menu
no longer lists the items of a task the user cannot read, nor of a task in
a company that is not selected. Only the assignee or the reviewer of an item
gives it another assignee; a reassignment by another user is refused. The
deploy is one update of the module, with nothing else to run.

The update to 16.0.2.7.0 turns the questions and answers of every task,
archived tasks included, from the Html fields ``questions`` and ``answers``
of the task into question rows. Html that shows no text, image, video,
embedded frame or icon is blank.
*Lists alone* is Html of top-level numbered or bulleted lists, each after at
most one block of text, its heading, with nothing else: no other text, no
image, video, embedded frame, icon or list in a heading, no list inside
another block. The heading gives
the round of the rows of its list, as plain text. A list nested in a list
belongs to the item before it; a list nested before any item makes the Html
something else.

.. list-table::
   :header-rows: 1

   * - Questions and answers
     - Rows
   * - both lists alone, as many lists on each side, as many items in each
       pair of lists
     - a row per pair of items, Answered, or Open where the answer item is
       blank; a pair of two blank items gives no row. The task is kept whole
       where a heading would be in no row: when the items of a pair of lists
       with a heading are all blank (every pair blank, too), or the answers
       under a heading other than the round of their questions are all blank
   * - anything else on both sides
     - one row of both as they are, Answered
   * - questions alone, lists alone
     - a row per item that is not blank, Open; one row of the questions as
       they are when the items of a list with a heading are all blank (every
       item blank, too), so that no heading is lost
   * - questions alone, anything else
     - one row of the questions as they are, Open
   * - answers alone
     - one row of the answers as they are, without a question, Answered
   * - both blank
     - none

Where an answer list has a heading other than the round of its questions,
the heading is put in front of each of its answers, so that a name or a date
it gives stays with them. Names and dates in the answers are not read: a
converted row has no Answered by and no Answered on. SQL inserts the rows,
in the order of the Html, as the superuser, and writes nothing on the tasks.
A task that already has rows is skipped, so a second run inserts nothing.
The server log gets one line with the number of tasks of each case and of
rows inserted per status.

Before the data files are loaded, the two fields are taken out of the stored
views of this module, in every translation; the update would otherwise fail
validating the task form. A view outside this module that names one of them
is not rewritten: change it by hand.

This module does not declare the two fields. At its end the update deletes
them with their columns, unless a module updated in the same run declares
them: that module keeps the fields and their columns. Without them, the
content is in the rows only. An update that finds the columns missing logs a
warning and converts nothing.

The update to 16.0.2.8.0 gives every message of a task its **Task**
(``task_id``), archived tasks included, and every internal note of a task
its **Category** (``note_category``); the install does the same. Before Odoo
sets up the fields, SQL creates their two columns in ``mail_message`` where
they are missing, with the partial indexes Odoo looks for, so that Odoo
computes neither field over the messages of the database. SQL then fills
the columns by the rule of the fields, clears any value a message should not
hold, and writes nothing else on the messages. The two columns are analyzed
last, so that the query planner has their statistics from the start. The
server log gets one line with the number of messages given a task and
cleared, and of notes given each category and cleared; a second run changes
nothing.

Odoo then adds the foreign key of ``task_id``, which reads the message table
once. Making the columns and the key locks the message table until the
module is loaded, and a running server waits on it meanwhile: on a large
database, run the update or the install with the server stopped.

The update to 16.0.2.8.1 gives their **Category** to the internal notes of
tasks that have none and whose body opens with a ``<span>``, by SQL over
those notes alone. It adds no column and writes nothing else on the
messages. The server log gets one line with the number of notes given a
category; a second run changes nothing.

The update to 16.0.2.9.0 adds the **Modules** of a task: it creates the
table ``project_task_ir_module_module_rel``, the two views of the module
picker, the **Module** field of the task search and an access right that
lets **Project / User** read the module list. It has no migration and writes
nothing on the tasks, which start without modules; it can run with the
server up. Where ``base_install_request``, installed with ``mail``, already
lets every internal user read the module list, the access right changes
nothing; without it, the field still works for project users.

The update to 16.0.2.10.0 adds **Develops This Odoo** to projects, with its
column, and the same field on tasks, related to the project and not stored.
It has no migration and writes nothing on the projects: no project carries
the flag after it, and the form hides the Modules of every task until one
does. It can run with the server up. Afterwards a Settings administrator
ticks the flag on the AI-driven project that develops the database.

The update to 16.0.2.10.1 has no migration and writes nothing on existing
records; it can run with the server up.

The update to 16.0.2.12.0 adds the sessions of a task: the table
``project_task_session``, its access right for **Project / User** and the
**Sessions** tab of the task form. It has no migration and writes nothing
on the tasks, which start without sessions.

The update to 16.0.2.13.0 makes the images of task texts attachments of the
task when a text is saved. It has no migration and writes nothing on
existing records: a text saved before keeps its inline images until it is
saved again, or until a migration or a script calls
``_move_inline_images_to_attachments`` on its task (see *Usage*).

The update to 16.0.2.14.0 adds the **Brief** of a task, computed on each
read that names it. It has no migration, adds no column, stores nothing and
writes nothing on existing records.

The update to 16.0.2.15.0 deletes the test case field **Passed**
(``passed``) with its column, at its end. A create or write that sends
``passed``, or a former state value
(``todo``, ``done``, ``cancelled``), fails with Odoo's ``ValueError``. Before
the data files are loaded, the pre-migration takes ``passed`` out of the
stored views of this module, in every translation, the test case sub-views
of the task form included; the update would otherwise fail validating the
task form. A view outside this module that names it is not rewritten:
change it by hand.

Run the update with the server stopped: dropping the column takes an
exclusive lock on the test case table, and workers running the previous code
read the column.

The update to 16.0.2.16.0 adds the **UAT users** of a task
(``uat_user_ids``, table ``project_task__uat_user_rel``) and puts them on
the task form after the Assignees. It has no migration and writes nothing
on the tasks. A database where another module already declares the same
field on the same table keeps the table and its rows; when a new version of
that module no longer declares the field, update both in the same run.

The update to 16.0.2.16.1 has no migration and writes nothing on existing
records; it can run with the server up. From it a checklist item or
question row moved to another task is checked against the new task, as a
session already was: a user who cannot read that task gets an access error.

The update to 16.0.2.17.0 gives projects and tasks their keys (*Task keys*
in the description). On a database where OCA ``project_key`` ran, the
columns and the keys are there already and nothing changes; how this module
takes its place is under *Coming from project_key*. Elsewhere the migration
gives each project a key and its tasks theirs, as described there.

The update to 16.0.2.17.1 has no migration and writes nothing on existing
records; it can run with the server up. From it a portal user's write that
sends a Size is refused instead of dropped, and a copy of a task takes no
UAT users.

The update to 16.0.2.17.3 renews the keys kept from before by tasks taken
out of their project, sub-tasks moved with a new parent or a project to
show in, the other tasks of a recurrence moved with one of them, and tasks
created or copied with no project from within a project (migration
script). Of the keys that end in a hyphen and a number, it clears each one
of a task without a project, and gives each task whose key does not start
with the key of its project and a hyphen the next key of its project, or
none when the project gives none, in the order the tasks were created.
Every other key stays, keys typed by hand such as ``SPEC2`` included.
SQL writes the keys, so the last update date of the tasks does not change;
the log names each task with its old and its new key, and counts the keys
cleared and renewed. A run with ``test_enable`` changes nothing, since
keys are off in a test run: run the update without it, or call
``_renew_stale_keys`` on ``project.task`` from a shell afterwards.

The update to 16.0.2.18.0 has no migration and writes nothing on existing
records; it can run with the server up. From it the install no longer takes
over ``project_software``, the former name of this module, nor the module it
was cut from.

The update to 16.0.2.19.0 has no migration and writes nothing on existing
records; it can run with the server up. From it
``odoo.addons.project_ai_driven.hooks`` no longer has ``is_installed`` and
``take_over_module``, which a module that replaced another under a new name
called from its own ``pre_init_hook``. Odoo cannot load a module whose code
still imports them, so the addons path gets a version of that module that
no longer does before, or together with, this one.

The update to 16.0.2.20.0 has no migration and writes nothing on existing
records; it can run with the server up. From it the next task of a
recurrence takes the checklist items and test cases of its task from zero
and enters its stage unchecked by the closing and the estimate rules, and a
change saved for the following or for all tasks of a recurrence reaches the
tasks of that recurrence only, each one checked and its Assigning Date set
or cleared as Odoo's write of several tasks sets or clears it.

The update to 16.0.2.21.0 has no migration and writes nothing on existing
records. From it a test case follows the access of its task, the companies
selected in the company switcher included: the **Test Cases** menu no
longer lists the cases of a task the user cannot read, nor of a task in a
company that is not selected. The completion and the card summaries of the
checklist and the test cases are read by **Project / User** only: a user
without that group reads the task without them. A create or a write that
gives a case Not run drops the Verified by, Verified on and Verification it
sends, and a create that takes Passed or Failed from a default is stamped.
The deploy is one update of the module, with nothing else to run.

The update to 16.0.2.22.0 has no migration and writes nothing on existing
records; it loads the view that adds Task Type and Estimate to the kanban
quick create. From it the closing and the estimate rules check a task moved
to another project also when it keeps its stage, and a save links an image
the task already has instead of attaching it again.

The update to 16.0.2.23.0 has no migration and writes nothing on existing
records; it can run with the server up. From it the completion and the card
summary of the checklist, the buttons of an item and the red of one's own
TODO items are computed for each user who reads them, also where two users
read the same task in one transaction; and the next task of a
recurrence, and each of its sub-tasks, keeps the Verification of each test
case that was Not run with one; the tasks created before stay as they are.

The update to 16.0.2.24.0 has no migration and writes nothing on existing
records; it can run with the server up. From it the kanban card of a task
names the completion of its test cases *Test cases*, no longer *Your Test
cases*: the figure counts every case of the task, whoever reads it.

The update to 16.0.2.31.0 adds **Actual Hours** (``actual_hours``) and the
**Estimate Coefficient** (``estimate_coefficient``) to tasks, with their
columns, and **Stages Requiring Actual Hours** to projects, with the table
``project_actual_hours_required``. It has no migration. Odoo fills the
coefficient column with 1.0 on every task, archived tasks included, in one
SQL statement that writes nothing else on the tasks, and makes it NOT
NULL. Actual Hours stay empty and no stage is marked. Run the update with
the server stopped: a worker still running the previous code leaves the
column out of the tasks it creates, and NOT NULL refuses them.

The update to 16.0.2.32.0 has no migration and writes no task. It drops
NOT NULL from the column ``estimate_coefficient``, and the field loses its
default: a task created without a coefficient has none. Every task keeps
the coefficient it has, the 1.0 of the update to 16.0.2.31.0 included, and
counts in ``get_estimate_coefficient`` with it until a write changes its
Estimate without one. An update from a version before 16.0.2.31.0 adds the
column empty instead: the tasks that exist have no coefficient and do not
count, also once they get Actual Hours, until a coefficient is written to
them. A tool or a script that changes an Estimate priced from the anchors
sends ``estimate_coefficient`` in the same write; without it the
coefficient is emptied.

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

**Coming from project_key**

This module declares ``key`` on the project and the task,
``task_key_sequence_id`` on the project and ``url`` on the task under the
names, types, columns and SQL constraints of OCA ``project_key``, and takes
its place. Each project's sequence keeps its code
``project.task.key.<project id>``, its prefix and its next number, and
whatever reads or searches ``key`` keeps working.

This module does not depend on ``project_key`` and does not uninstall it.
While ``project_key`` is loaded it gives the keys as before: both would draw
a number for every task. A module that depended on it, or an administrator,
uninstalls it once this version is installed or updated, in the same run at
the earliest, not before: the columns, the fields and the unique
constraints stay only because this module declares them too. Uninstalled
before, ``project_key`` drops the key columns with every project and task
key; the update then gives the projects new keys from their names and
numbers every task from 1, and the keys in messages and commits no longer
lead to their tasks. Take its code off the addons path in the deploy that
uninstalls it: left there, its labels stay in the database until the next
update.

The install (``post_init_hook``) and the update to 16.0.2.17.0 (migration
script) give the keys a database lacks:

* Each project without a key, archived ones included, gets one from its
  name and a sequence, whether ``project_key`` is loaded or not.
* The tasks without a key of those projects, archived and folded ones
  included, get the next numbers of the project's sequence in the order
  they were created, and the sequence moves past them. One SQL statement
  writes their keys, so their last update date does not change.
* A project that has a key, and its tasks, are left alone: on a database
  where ``project_key`` ran, nothing changes.
* A run with ``test_enable`` gives no keys, since keys are off in a test
  run: run the update without it, or call ``_set_missing_keys`` on
  ``project.project`` from a shell afterwards.

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
