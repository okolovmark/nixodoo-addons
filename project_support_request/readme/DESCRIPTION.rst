A **Support** item in the header of the web client files requests about this
Odoo, as tasks of the project flagged **Develops This Odoo**
(``project_ai_driven``), and lists the open ones of the user.

* The item counts the open requests the user is a UAT user of: tasks of that
  project in an unfolded stage. Its panel splits them into *Bugs* (task type
  Bug) and *Others*, five at a time with *View more* for the rest; a request
  opens on its task form. *View Closed* lists the user's requests in folded
  stages.
* Each request shows a dot whose colour strengthens with its priority, from
  a pale grey at Lowest to red at Highest, its tooltip naming the level, and
  how long ago it was filed.
* A request stays in the company of the project. Its filer reads, lists and
  opens it whichever companies they selected, without access to that
  company too: the company rule of tasks makes that exception for reading
  only. Outside the selected companies the filer sends messages in the
  request's chatter, files attached in *Send message* included, but does not
  change or delete the request, nor attach a file with the chatter's
  paperclip: both need write access to the task. Where the filer has write
  access to their own requests, the form looks editable there, yet saving is
  refused. The project itself stays closed to them. Other users' requests,
  tasks of other projects and portal users gain nothing from it.
* *New Ticket* takes a title and a task type and creates the task in the
  first unfolded stage of the project, with the user as its UAT user, no
  assignee and no estimate. Every internal user files requests, with or
  without Project rights and whether or not they read the project: the task
  is created with superuser rights, in the user's name.
* Without an active project carrying the flag the item is hidden.
* Only internal users file and list requests: for any other user, a portal
  user for one, the item's routes name no project, list nothing and refuse
  to create a request.

The task key (``key``) is shown before the title where a module of the
database declares it, such as OCA ``project_key``.
