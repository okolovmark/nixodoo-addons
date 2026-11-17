A project gets an **Access** mode and members with a role: Reader,
Contributor, Editor or Admin. In a Public or Private project the roles, not
the project's Visibility or its followers, decide who reads the project and
its tasks and who changes them.

**Access modes** (``project.project``, ``access_mode``)

* **Standard**, the default: the project's Visibility decides, as without
  this module; its members change nothing.
* **Public**: every internal user reads the project and its tasks, as a
  Reader where they hold no role.
* **Private**: only its members read them.

Project Administrators read every project, Private included, and act as
Readers where they hold no role. Settings administrators are Admin of every
project. Portal users and followers get nothing from a Public or Private
project. A role opens nothing a user's Odoo groups do not: creating or
deleting a task takes Project / User rights, saving the project form
Project / Administrator rights. The rights of each role are in *Usage*.

**What follows the project**

Inside a Public or Private project:

* Checklist items, question rows and test cases are read with their task,
  and written, created and deleted by whoever may edit it.
* Task Analysis, the burndown chart, milestones and project updates are
  read by whoever reads the project; milestones and updates are changed by
  its Admins.
* A pull request is read by whoever reads one of its tasks, and changed,
  linked or deleted only by whoever edits each of its tasks in such
  projects. Linking a known link to one's own task stays open.
* The stages of the project are changed only by its Admins, a stage shared
  with other projects included.

**Checks on writes**

Besides the record rules, a create or a write is refused when:

* anyone but an Admin changes the assignees of a task, or creates a task
  with assignees;
* anyone but an assignee of the task, an Editor or an Admin moves it to
  another stage;
* a task enters the project, by its own move or its parent's, and the user
  is not a Contributor, Editor or Admin of it, or not an Admin when the
  task has assignees;
* in a Private project, a user who is not a member is set as assignee or
  UAT user; tasks that carry one when the project turns Private keep them;
* the last Admin of the project is removed or given a lower role, or the
  project is made Public or Private without an Admin;
* the project that develops this Odoo is made Private.

A member row keeps its project and its user. Members are added by an Admin
of the project, or by a Project Administrator to a Standard project and as
the first Admins of a project made Public or Private. A refusal names no
record the user cannot read.

**Notifications**

Notifications of such a project, of its tasks and of its updates, by email
or in the Discuss inbox, reach only the followers and recipients who can
read them; the followers list itself does not change.

**Portal links**

Portal links do not open such a project or its tasks: their access tokens
are cleared when the project becomes Public or Private, or a task moves into
it; while it stays so, the project and its tasks keep no token and its share
buttons are hidden. Attachments keep theirs: such a token opens that one
file only, as the images pasted into a description or a message need.

**How it works**

For each model and operation the module adds a global record rule
``outside OR allow``: *outside* is a record of a Standard project or of no
project, *allow* the role condition for the current user. A global rule is
AND-ed with every other rule, so it takes access away inside Public and
Private projects only. Where a group rule of another module could still
deny inside (a followers-only Visibility, a missing follower), a rule for
internal users grants the same ``inside AND allow``. The domains come from
``res.users._project_member_access_domain`` and read four stored user sets
of the project, computed from its members and never written directly:
``member_user_ids``, ``contributor_user_ids``, ``editor_user_ids`` and
``admin_user_ids``, each holding a role and those above it. What a rule
cannot say about a write is checked in ``create`` and ``write``. The
superuser and ``sudo()`` pass every check.

``readme/access_model.puml`` draws the model and the rules; the design
record is ``docs/adr/0013-a-project-s-access-is-a-mode-and-member-roles.md``
in this repository.
