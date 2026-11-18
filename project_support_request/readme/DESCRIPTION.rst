A **Support** item in the header of the web client files requests about this
Odoo, as tasks of the project flagged **Develops This Odoo**
(``project_ai_driven``), and lists the open ones of the user.

* The item counts the open requests the user is a UAT user of: tasks of that
  project in an unfolded stage. Its panel splits them into *Bugs* (task type
  Bug) and *Others*, five at a time with *View more* for the rest; a request
  opens on its task form. *View Closed* lists the user's requests in folded
  stages.
* *New Ticket* takes a title and a task type and creates the task in the
  first unfolded stage of the project, with the user as its UAT user, no
  assignee and no estimate. Every internal user files requests, with or
  without Project rights and whether or not they read the project: the task
  is created with superuser rights, in the user's name.
* Without an active project carrying the flag the item is hidden.
* Only internal users file and list requests: for any other user, a portal
  user for one, the item's routes name no project, list nothing and refuse
  to create a request.

Each request shows its task key (``project_ai_driven``) before its title, and
a new one is created with its key.
