Installing the module is all a database needs, but for the two cases below.
Its install hook gives the projects and the tasks that exist their keys, as
the update to 20.0.2.17.0 does (below). The install adds two columns to the
message table
and computes them for every message through the ORM: the table stays locked
until the module is loaded, which takes long on a large message table.

**A database that has an earlier 20.0 version installed**

The module has three migrations. ``0.0.0/pre-migration.py`` runs on every
version change of the module. Before the data files load, it blanks the
stored arch of the module's extension views, which still name fields and
buttons of the earlier version, so that an update from any earlier version
passes without dev flags. The data files rewrite each view they name; a view
no file names any more is deleted at the end of the update. Translations of
those views made in the interface are lost with the blanked arch, on every
version change: the module ships none. ``20.0.2.15.0/post-migration.py``
converts the test case states of 20.0.2.4.1 and earlier and
``20.0.2.17.0/post-migration.py`` gives the projects and tasks their keys
(below); they are the only migrations that write data. The update:

* deletes the fields **Difficulty**, **Knowledge Area Level** and **Tester**
  of the tasks, with their values;
* deletes the Html fields ``questions`` and ``answers`` of the tasks, with
  their columns: their text is not turned into question rows;
* creates the checklist model ``project.task.checklist.item`` with an empty
  table. The items of the former ``project.task.subtask`` stay in the table
  ``project_task_subtask``, which no model reads. Its foreign key on the
  assignee restricts deletion: a user assigned to such an item cannot be
  deleted until the table is dropped by hand;
* creates the two *All checklist updates* subtypes under new external ids
  and deletes the former ones, with the follower subscriptions to them: tick
  *All checklist updates* again where it was ticked;
* gives checklist items, test cases and question rows two access rights
  each, ``access_task_<model>_read`` and ``access_task_<model>_write``
  (``checklist_item``, ``testcase``, ``question``), and deletes the access
  rights no data file names any more, ``access_task_testcase`` among them:
  test cases follow the access of their task from then on;
* computes the task and the note category of every message, as an install
  does.

Export the deleted fields first if they are needed.

The update leaves:

* the external ids that ``mail`` (selection values of the activity
  fields) and ``phone_validation`` (the activity mixin inheritance) hold
  for the former checklist model; nothing reads them;
* the sizes the tasks have stored, until their hours change.

Of the versions below, only 20.0.2.15.0 and 20.0.2.17.0 have a migration of
their own, next to the blanking that runs on every version change.

The update to 20.0.2.9.0 adds the **Modules** of a task: it creates the
table ``project_task_ir_module_module_rel``, the two views of the module
picker, the **Module** field of the task search and an access right that
lets project users read the module list. It writes nothing on the tasks,
which start without modules. Where ``base_install_request`` already lets
every internal user read the module list, the access right changes
nothing; without it, the field still works for project users.

The update to 20.0.2.10.0 adds **Develops This Odoo** to projects, with its
column, and the same field on tasks, related to the project and not stored.
It writes nothing on the projects: no project carries the flag after it,
and the form hides the Modules of every task until one does. Afterwards a
system administrator sets the flag on the AI-driven project that develops
the database.

The updates to 20.0.2.10.1 and 20.0.2.11.1 write nothing on existing
records.

The update to 20.0.2.12.0 adds the sessions of a task: the table
``project_task_session``, its two access rights for project users,
``access_task_session_read`` and ``access_task_session_write``, and the
**Sessions** tab of the task form. It writes nothing on the tasks, which
start without sessions.

The update to 20.0.2.13.0 makes the images of the texts of tasks, question
rows and test cases attachments of the record holding the text when a text
is saved. It converts no data: a text saved before keeps its inline images
until it is saved again, or until a migration or a script calls
``_move_inline_images_to_attachments`` on its task (see *Usage*).

The update to 20.0.2.14.0 adds the **Brief** of a task, computed on each
read that names it. It adds no column, stores nothing and writes nothing on
existing records.

The update to 20.0.2.15.0 deletes the test case field **Passed**
(``passed``) with its column, at its end. A create or write that sends
``passed``, or a former state value (``todo``, ``done``, ``cancelled``),
fails with Odoo's ``ValueError``. After the data files load, the update
converts the test cases stored with a former state, which a database that
ran 20.0.2.4.1 or earlier holds, by one SQL statement: ``todo`` to
``not_run``, ``done`` to ``passed`` and ``cancelled`` to
``not_applicable``. It logs how many it converts. Until then such a case
matched no filter and no kanban count, the completion of its task counted
it as not passed, and a copy of its task failed on its state.

The blanking of the extension views takes ``passed`` out of the stored test
case views of the task form before the data files load. A view of another
module that names ``passed`` is not blanked: a module that depends on this
one rewrites its views from its own files when the update reaches it, and
that update fails while a file still names ``passed``; a view without an
external id, such as one made in the interface, fails when it is rendered.
Change them by hand.

Run the update with the server stopped: dropping the column takes an
exclusive lock on the test case table, and workers running the previous code
read the column.

The update to 20.0.2.15.1 adds no column and writes nothing on existing
records. The html properties of a task saved before it keep their inline
images until they are saved again, or until
``_move_inline_images_to_attachments`` runs on the task, which covers them
from this version on.

The update to 20.0.2.16.0 adds the **UAT users** of a task
(``uat_user_ids``, table ``project_task__uat_user_rel``) and puts them on
the task form after the Assignees; a copy of a task starts without them.
It has no migration and writes nothing on the tasks. A database where
another module already declares the same field on the same table keeps the
table and its rows; when a new version of that module no longer declares
the field, update both in the same run.

The update to 20.0.2.17.0 adds the task keys (*Task keys* in the
description): the columns ``key`` of projects and tasks with their unique
constraints, and ``task_key_sequence_id`` of projects. Its migration, as the
install hook does, gives the keys the database lacks:

* Each project without a key, archived ones included, gets one from its
  name and a sequence. A name without letters or digits gives no key: that
  project stays without one until somebody types one, which its form
  requires on the next save. Its tasks created from then on get keys; those
  it had stay without one, since ``_set_missing_keys`` leaves a project that
  has a key alone, until one is written with its project. Call
  ``_number_keyless_tasks`` on that project from a shell to number them.
* The tasks without a key of those projects, archived and folded ones
  included, get the next numbers of the project's sequence in the order
  they were created, and the sequence moves past them. One SQL statement
  writes their keys, so their last update date does not change.
* A project that has a key, and its tasks, are left alone.
* A run with ``test_enable`` gives no keys, since keys are off in a test
  run: run the update without it, or call ``_set_missing_keys`` on
  ``project.project`` from a shell afterwards.

The update to 20.0.2.18.0 has no migration and writes nothing on existing
records. A next occurrence of a recurring task created from then on takes
the checklist items and test cases of its task from zero, takes no
Resolution and no pull request of its task in any stage, and enters the
first stage of its project, or the stage of its task when the project lists
none, unchecked by the closing and the estimate rules.
