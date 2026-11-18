The module has no install hook. Installing it is all a database needs, but
for the two cases below. The install adds two columns to the message table
and computes them for every message through the ORM: the table stays locked
until the module is loaded, which takes long on a large message table.

**A database that has an earlier 20.0 version installed**

The update runs one migration, ``20.0.2.8.1/pre-migration.py``. Before the
data files load, it blanks the stored arch of the module's extension views,
which still name fields and buttons of the earlier version, so that the
update passes without dev flags. The data files rewrite each view they name;
a view no file names any more is deleted at the end of the update. The
migration converts no data. The update:

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

* the test cases with the states of the earlier version (``todo``,
  ``done``, ``cancelled``) as they are stored, with their **Passed** flag.
  Such a case matches no filter and no kanban count of this version, and the
  completion of its task counts it as not passed, until it is given a
  result;
* the external ids that ``mail`` (selection values of the activity
  fields) and ``phone_validation`` (the activity mixin inheritance) hold
  for the former checklist model; nothing reads them;
* the sizes the tasks have stored, until their hours change.

The update to 20.0.2.9.0 adds the **Modules** of a task: it creates the
table ``project_task_ir_module_module_rel``, the two views of the module
picker, the **Module** field of the task search and an access right that
lets project users read the module list. It has no migration and writes
nothing on the tasks, which start without modules. Where
``base_install_request`` already lets every internal user read the module
list, the access right changes nothing; without it, the field still works
for project users.

The update to 20.0.2.10.0 adds **Develops This Odoo** to projects, with its
column, and the same field on tasks, related to the project and not stored.
It has no migration and writes nothing on the projects: no project carries
the flag after it, and the form hides the Modules of every task until one
does. Afterwards a system administrator sets the flag on the AI-driven
project that develops the database.

**A database that has the 20.0 project_software installed**

``project_software`` is the former name of this module. Such a database is
not updated in place. With the code of ``project_ai_driven`` on the addons
path:

#. Install ``project_ai_driven``.
#. Uninstall ``project_software``.

What to expect:

* The tasks keep their field values and their test cases, the test case
  states as they are stored.
* The checklist items and the Html ``questions`` and ``answers`` are not
  kept: ``project_ai_driven`` declares neither ``project.task.subtask`` nor
  the two fields. The uninstall drops the two fields with their columns and
  leaves the items in the table ``project_task_subtask``, which no model
  reads. Export them first if they are needed.
* The project flag does not come along: tick **AI-driven Project** again on
  each project.
* The views of ``project_software`` and its two checklist message subtypes
  are replaced by new ones.
* Between the two steps every page the module adds to the task form shows
  twice, and each start of the server logs that ``project_software`` cannot
  be loaded.
