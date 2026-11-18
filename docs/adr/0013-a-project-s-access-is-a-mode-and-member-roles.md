# A project's access is a mode and member roles, decided by access restrictions inside Public and Private projects only

Status: accepted (2026-11-17)

The 16.0 branch decided this first. Its record, `docs/adr/0013-a-project-s-access-is-a-mode-and-member-roles.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

Odoo decides who sees a project and its tasks from the project's visibility (Team Members, invited users, internal users, portal) and the follower lists, and who may change a task from the Project groups. Permissions are OR-ed, so a module can add access with a permission of its own but never take any away by one. Following a project or a task opens it, whatever the follower's part in the work. Who may assign a task or move its stage is not expressed at all.

From `project_member_access` 20.0.1.0.0 a project has an access mode, Standard, Public or Private, and members with a role: Reader, Contributor, Editor, Admin.

- **Standard** is Odoo as it was: the module's restrictions let every record of a Standard project through, and a member row acts on nothing. A new project starts in Standard; a database that installs the module changes no access until a project is switched.
- **Public**: every internal user reads the project and its tasks; without a role they are a Reader.
- **Private**: only members read it. Project / Administrator reads every project, private included, and without a role acts as a Reader. A Settings administrator is Admin everywhere, so a project left without a reachable Admin can be given one.
- Portal users, Team Members and followers get nothing from a Public or Private project.

| Action in a Public or Private project | Reader | Contributor | Editor | Admin |
|---|---|---|---|---|
| Read tasks, post in the chatter | yes | yes | yes | yes |
| Create a task (also: move one in) | no | yes | yes | yes |
| Edit an own task (creator, assignee, UAT user) | yes | yes | yes | yes |
| Edit somebody else's task | no | no | yes | yes |
| Move the stage of a task one is assigned to, close or reopen it | yes | yes | yes | yes |
| Move the stage of another task, close or reopen it | no | no | yes | yes |
| Change assignees, on create too | no | no | no | yes |
| Delete a task | no | no | no | yes |
| Members, mode, stages, settings of the project | no | no | no | yes |

Checklist items, question rows, test cases and sessions follow their task: `project_ai_driven` gives them access rows on `('task_id', 'access', ...)` (its ADR 0007), so this module's task rows reach them with no row of their own. The burndown report, milestones and project updates are read by whoever reads the project; milestones and updates are changed, and moved into the project, by an Admin, as settings of the project. A pull request is read through one of its tasks, and one linked to a task of a Public or Private project is changed, linked or deleted only by whoever may write that task; linking a known link to one's own task stays open. Notifications of a task, a milestone, an update or the project reach only users who can read it, so following gives no access by mail either; a recipient known by email address only gets none.

A member row keeps its project and its user: a change is a removal and an addition. Adding a row is checked against the Admins the project had before the row, so nobody makes themselves Admin by creating one; a Project Administrator may add rows to a Standard project and name the first Admins, themselves or other users, of a project being made Public or Private in the same save. Odoo writes the members after the mode, so the rows are inserted into a project that is Public or Private already: the member create restriction passes every row of a Project Administrator, and that check in `create` decides.

**Mechanism.** For each model and operation the module adds one `ir.access` restriction, a row without a group: `outside OR allow`, where `outside` is a record of a Standard project (or of no project) and `allow` is the role condition for the current user. Restrictions are AND-ed with the permissions, so they take access away inside Public and Private projects and leave every other row untouched outside them. Where a permission of Odoo could still deny inside (a Team Members only visibility, a missing follower), a permission row of the module, for internal users or Project / User, grants the same `inside AND allow`: projects, milestones and updates for read, milestones and updates for change, the burndown report for read. A task is read where its project is: its read restriction allows `('project_id', 'access', 'read')`. All domains come from one method on `res.users`, `_project_member_access_domain`, called from the rows, built from field paths only: Odoo caches an access domain per user and company selection, so a list of project ids computed at evaluation would go stale when a member is added. A role condition needs one field per threshold (`member_user_ids`, `contributor_user_ids`, `editor_user_ids`, `admin_user_ids`, stored, computed from the members): two leaves on `member_ids.user_id` and `member_ids.role` would match different member rows. What a row cannot say about a write (assignees, stage, closing and reopening, a task moved in, sub-tasks carried out with their parent, a milestone or an update moved in, stages of the project) is checked in `write` and `create`. The superuser and `sudo()` pass every check.

**Portal tokens.** A portal access token opens a record to whoever holds the link, whatever the rows say. Entering Public or Private clears the tokens of the project and of its tasks, and none is kept there afterwards, whatever wrote it. The share actions of the project and of its tasks, the *Share* entries of the gear menu included, and their wizards are refused there; the share buttons are hidden. Attachment tokens stay: the editor writes them into the links of images pasted into descriptions and messages, and a token opens that one file only.

**Constraints.** A Public or Private project keeps at least one Admin, an active user: the last one is neither removed nor lowered. Archiving the user of the last Admin is not refused; a Settings administrator then names a new Admin. In a Private project only a member is set as assignee or UAT user; tasks already carrying a non-member when a project turns Private are left as they are. A Private project cannot be the project that develops this Odoo, where every employee files support requests.

**What differs from 16.0.**

- 16.0 says it in record rules: a global rule per model and operation, and group rules for internal users where another rule could deny. Here they are `ir.access` restrictions and permissions under the external ids of the 16.0 rules they replace (`project_task_rule_member_access_read` and the others); the field, model and method names are those of 16.0.
- A read restriction keeps its outside branch for every user, a portal user included: Odoo 20 raises on a search whose read domain is empty, where the user should find nothing.
- Odoo 20 covers part of what 16.0 adds. Its task permissions open a task whose project is read, so the tasks need no permission of the module. The lines and the sessions of a task follow it through `project_ai_driven`'s rows, where 16.0 has a rule per line and session model and a check on a line moved. Task Analysis is read by Project Administrators only, who read every project, so it has no row. A project has no portal token of Odoo's making, and a sub-task follows its parent's project without a `display_project_id`. Odoo reads a new parent or project of a task as the user, and a record added to a many2many as the user, so a refusal for one the user cannot read is Odoo's, naming nothing.
- A task's `state` closes it: moving it into Done or Cancelled, or out of them, is checked as a move of stage, by an assignee, an Editor or an Admin. Its other values (In Progress, Changes Requested, Approved, Waiting) are an edit of the task, open to its creator and UAT users too, so a review is marked by those who run it; Odoo's own return to In Progress after a move is not refused.
- Stages are changed by Project Administrators, as in Odoo, and a stage of a Public or Private project, one shared with other projects included, only by one who is also an Admin of one of the Public or Private projects it belongs to, as on 16.0. An Admin without Project / Administrator rights changes no stage.
- The Team Members of a project (`allowed_internal_user_ids`) are hidden in a Public or Private project and open nothing there.
- Linking a pull request by its link (`pr_uri`) finds it as the superuser, and marks it readable for the transaction: Odoo 20 checks read access on a record added to a many2many, and the link of a pull request whose tasks are all hidden would otherwise be refused, or created a second time against its unique URL.

**Consequences.**

- `privacy_visibility` and the Team Members show on the project form in Standard only; inside Public and Private projects the mode decides. The share buttons are hidden there; the *Share* entries of the gear menu stay and are refused.
- An Odoo group still gates what a role can use: creating or deleting a task needs the Project / User rights, changing the project's settings and its stages the Project / Administrator rights, as before. A role opens nothing a user's groups do not.
- Moving a task out of a Public or Private project is an edit, and so is each sub-task it carries out with it, which Odoo moves as the superuser; moving one in is a create there. A task that leaves for a Standard project, and so its assignees changed in the same write, is judged by the rows of the project it lands in.
- A stage shared between a Standard and a Public or Private project is changed only by an Admin of the latter, and one shared by two Public or Private projects by an Admin of either, each a Project Administrator.
- A refusal of the module names no record the user cannot read; reading is checked before the role checks. Odoo's own record access error lists the refused records to an internal user with Technical Features.
- A search on tasks carries the restriction's subqueries on the project's user sets for every internal user who is neither a Project Administrator nor a Settings administrator.

Reversal cost: uninstall `project_member_access`; its rows, members and fields go and every project is Standard again. A project's mode and members are lost.
