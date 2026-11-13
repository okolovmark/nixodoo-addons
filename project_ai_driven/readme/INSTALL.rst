The module has no install hook and no migration. Installing it is all a
database needs, with one exception.

**A database that has the 20.0 project_software installed**

``project_software`` is the former name of this module. Such a database is
not updated in place. With the code of ``project_ai_driven`` on the addons
path:

#. Install ``project_ai_driven``.
#. Uninstall ``project_software``.

What to expect:

* The tasks keep their field values, their checklists and their test cases.
* The project flag does not come along: tick **AI-driven Project** again on
  each project.
* The views of ``project_software`` and its two checklist message subtypes
  are replaced by new ones.
* Between the two steps every page the module adds to the task form shows
  twice, and each start of the server logs that ``project_software`` cannot
  be loaded.
