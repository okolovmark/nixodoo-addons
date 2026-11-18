A project gets an **Access** mode and members with a role: Reader,
Contributor, Editor or Admin. In a Public or Private project the roles, not
the project's Visibility, its Team Members or its followers, decide who reads
the project and its tasks and who changes them.

**Access modes** (``project.project``, ``access_mode``)

* **Standard**, the default: the project's Visibility decides, as without
  this module; its members change nothing.
* **Public**: every internal user reads the project and its tasks, as a
  Reader where they hold no role.
* **Private**: only its members read them.

Project Administrators read every project, Private included, and act as
Readers where they hold no role. Settings administrators are Admin of every
project. Portal users, Team Members and followers get nothing from a Public
or Private project. A role opens nothing a user's Odoo groups do not:
creating or deleting a task takes Project / User rights, saving the project
form and changing stages Project / Administrator rights. The rights of each
role are in *Usage*.

**What follows the project**

Inside a Public or Private project:

* Checklist items, question rows, test cases and agent sessions follow
  their task, as ``project_ai_driven`` makes them do: read with it, changed
  by whoever may edit it.
* The burndown chart, milestones and project updates are read by whoever
  reads the project; milestones and updates are changed, and moved into the
  project, by its Admins. Task Analysis is read by Project Administrators
  only, as in Odoo.
* A pull request is read by whoever reads one of its tasks, and changed,
  linked or deleted only by whoever edits each of its tasks in such
  projects. Linking a known link to one's own task stays open.
* A stage of the project, one shared with other projects included, is
  changed only by a Project Administrator who is an Admin of one of the
  Public or Private projects it belongs to.

**Checks on writes**

Besides the access rows, a create or a write is refused when:

* anyone but an Admin changes the assignees of a task, or creates a task
  with assignees;
* anyone but an assignee of the task, an Editor or an Admin moves it to
  another stage, closes it (Done, Cancelled), on create too, or reopens it;
* a task enters the project, by its own move or its parent's, and the user
  is not a Contributor, Editor or Admin of it, or not an Admin when the
  task has assignees;
* a task leaves the project and carries along sub-tasks the user may not
  edit there;
* in a Private project, a user who is not a member is set as assignee or
  UAT user; tasks that carry one when the project turns Private keep them;
* the last Admin of the project is removed or given a lower role, or the
  project is made Public or Private without an Admin; an archived user is
  no Admin (archiving the user of the last Admin is not refused: a Settings
  administrator then names a new Admin);
* the project that develops this Odoo is made Private.

A member row keeps its project and its user. Members are added by an Admin
of the project, or by a Project Administrator to a Standard project and as
the first Admins, themselves or other users, of a project made Public or
Private. The refusals of the module name no record the user cannot read;
Odoo's own record access error lists the refused records to an internal
user with Technical Features.

**Notifications**

Notifications of such a project, of its tasks, milestones and updates, by
email or in the Discuss inbox, reach only the users who can read them; a
recipient known by email address only gets none. The followers list itself
does not change.

**Portal links**

Portal links do not open such a project or its tasks: their access tokens
are cleared when the project becomes Public or Private, or a task moves into
it; while it stays so, the project and its tasks keep no token, its share
buttons are hidden, and the share actions, the *Share* entries of the gear
menu included, and the share wizards are refused. Attachments keep theirs:
such a token opens that one file only.

**How it works**

For each model and operation the module adds an access restriction, an
``ir.access`` row without a group, ``outside OR allow``: *outside* is a
record of a Standard project or of no project, *allow* the role condition
for the current user. Restrictions are AND-ed with every permission, so it
takes access away inside Public and Private projects only. The rows for
creating, changing and deleting members are *allow* alone; in a Standard
project *allow* takes in Project Administrators. Where a
permission of Odoo could still deny inside (a Team Members only Visibility,
a missing follower), a permission row of the module grants the same
``inside AND allow``. A task is read where its project is read
(``('project_id', 'access', 'read')``). The domains come from
``res.users._project_member_access_domain`` and read four stored user sets
of the project, computed from its members and never written directly:
``member_user_ids``, ``contributor_user_ids``, ``editor_user_ids`` and
``admin_user_ids``, each holding a role and those above it. What a row
cannot say about a write is checked in ``create`` and ``write``. The
superuser, ``sudo()`` and Settings administrators pass the rows and the
role checks; the rules on the members and their last Admin, on the people
of a Private project, on the project that develops this Odoo and on the
share wizards hold for them too.

``readme/access_model.puml`` draws the model and the rows; the design
record is ``docs/adr/0013-a-project-s-access-is-a-mode-and-member-roles.md``
in this repository.
