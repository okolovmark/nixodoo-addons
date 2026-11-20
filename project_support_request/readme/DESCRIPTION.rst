A **Support** item in the header of the web client files requests about this
Odoo, as tasks of the project flagged **Develops This Odoo**
(``project_ai_driven``), and lists the open ones of the user.

* The item counts the open requests the user is a UAT user of: tasks of that
  project in an unfolded stage. Its panel splits them into *Bugs* (task type
  Bug) and *Others*, five at a time with *View more* for the rest; a request
  opens on its task form. *View Closed* lists the user's requests in folded
  stages in one list, not grouped by stage: Odoo's task list without
  grouping (``project.open_view_all_tasks_list_view``), or the default task
  list, grouped by stage, where that view is missing.
* *New Support Request* takes a title and a task type and creates the task
  in the first unfolded stage of the project, with the user as its UAT
  user, no assignee and no estimate. Every internal user files requests,
  with or without Project rights and whether or not they read the project:
  the task is created with superuser rights, in the user's name. The type
  picker names the types in the user's language once a translation of them
  is loaded: ``project_ai_driven``, which defines them, ships none.
* A request belongs to the company of the project. Its filer reads it and
  comments on it in any company: the item lists it and opens it, and *View
  Closed* lists it, whether or not the user is allowed in that company or has
  it ticked in the company switcher, which reading and opening leave as it
  is. Changing or deleting a task still takes its company: a user allowed in
  it who saves a change to the request has it ticked by the form, which then
  saves again. Only the internal UAT users of a task of that project read it
  this way; the task of another project, or of another user, does not open.
* The filer reads the request as its follower: unfollowing it closes it to
  them. ``project_member_access`` leaves it open while the project is
  Public: that module reads a task by the mode and the members of its
  project, not by the company of the project.
* Reading a task outside this exception follows Odoo's multi-company rule of
  tasks as it stands: an administrator who changes its domain, on the rule
  or on a copy made with *Customize* in *Accesses*, changes it for reading
  too. Only a rule that applies to updates counts, and a copy both renamed
  and given another domain is not recognised. Given a group, Odoo's rule or
  its copy becomes a permission, as in Odoo, and no longer restricts
  reading: keep it without a group to keep reading restricted to the
  selected companies. With no active rule in the place of Odoo's, reading
  keeps the domain the ``project`` module gives that rule, or the one Odoo
  ships it with when that module's file cannot be read: to lift the
  company restriction of reading tasks, archive the module's rule
  *Project/Task: multi-company read, except own support requests* as well.
* Installing and updating the module untick *Read*, and *Read* alone, on
  Odoo's rule and on the active rules of tasks without a group whose name
  starts with its name or that carry its domain, copies made with
  *Customize* among them, if they apply to updates: a copy that applies to
  creating, reading and updating then applies to creating and updating. A
  rule that does not apply to updates keeps *Read* and goes on restricting
  reading, the requests included. Installing it restricts reading tasks to
  the selected companies on a database that had lifted that restriction
  before by unticking *Read* on Odoo's rule: to keep it lifted, archive the
  module's rule after installing.
* Uninstalling the module ticks *Read* on Odoo's multi-company rule of
  tasks, whatever else it applies to, and on every active rule of tasks
  without a group whose name starts with its name or that carries its
  domain, copies made with *Customize* in *Accesses* among them, that lacks
  it, whether or not it applies to updates; the other operations stay as
  they are. It ticks *Read* whatever unticked it, and on such a rule made
  without it too: a database that had unticked it before installing the
  module gets it back, so untick it again afterwards. Rules whose domain
  calls the module's method are left out: they are archived. A rule with a group is a permission and keeps its operations.
  The uninstall also archives the copies made of the module's own rule and
  any other rule using its domain.
* Without an active project carrying the flag the item is hidden.
* Only internal users file and list requests: for any other user, a portal
  user for one, the item's routes name no project, list nothing and refuse
  to create a request.

Each request shows its task key (``project_ai_driven``) before its title, and
a new one is created with its key. The dot before it is the colour of its
**Priority Level** (``project_ai_driven``), stronger the higher the level:
from a faint grey for Lowest and grey for Low, the default, up to red for
Highest; its tooltip names the level, and a level the item does not know is
shown as Low.

Every text of the item is translatable: its labels and messages, the errors
the server answers with, the priority levels of the dots and the ages of the
requests. The module ships no translation: it reads in English until one is
loaded.

The design record of the read exception is
``docs/adr/0017-a-filer-reads-and-comments-on-their-own-support-requests-in-any-company.md``
in this repository.
