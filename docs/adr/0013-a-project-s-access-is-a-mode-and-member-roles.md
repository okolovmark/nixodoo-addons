# A project's access is a mode and member roles, decided by global rules inside Public and Private projects only

Status: accepted (2026-11-17), amended (2026-11-20: a change applied to several tasks of a recurrence is checked on each; the next task of a recurrence in a Private project takes its member assignees only)

Odoo decides who sees a project and its tasks from the project's visibility (followers, internal users, portal) and the follower lists, and who may change a task from the Project groups. Group record rules are OR-ed, so a module can add access with a rule of its own but never take any away: a field such as "visible to all internal users" left unticked hides nothing, because the core employee rule still shows every project whose visibility is not followers-only. Following a project or a task opens it, whatever the follower's part in the work. Who may assign a task or move its stage is not expressed at all.

From `project_member_access` 16.0.1.0.0 a project has an access mode, Standard, Public or Private, and members with a role: Reader, Contributor, Editor, Admin.

- **Standard** is Odoo as it was: the module's rules let every record of a Standard project through, and a member row acts on nothing. A new project starts in Standard; a database that installs the module changes no access until a project is switched.
- **Public**: every internal user reads the project and its tasks; without a role they are a Reader.
- **Private**: only members read it. Project / Administrator reads every project, private included, and without a role acts as a Reader. A Settings administrator is Admin everywhere, so a project left without a reachable Admin can be given one.
- Portal users and followers get nothing from a Public or Private project.

| Action in a Public or Private project | Reader | Contributor | Editor | Admin |
|---|---|---|---|---|
| Read tasks, post in the chatter | yes | yes | yes | yes |
| Create a task (also: move one in) | no | yes | yes | yes |
| Edit an own task (creator, assignee, UAT user) | yes | yes | yes | yes |
| Edit somebody else's task | no | no | yes | yes |
| Move the stage of a task one is assigned to | yes | yes | yes | yes |
| Move the stage of another task | no | no | yes | yes |
| Change assignees, on create too | no | no | no | yes |
| Change the UAT users of a task one is assigned to | yes | yes | yes | yes |
| Change the UAT users of another task, or create a task with them | no | no | yes | yes |
| Delete a task | no | no | no | yes |
| Members, mode, stages, settings of the project | no | no | no | yes |

Checklist items, question rows, test cases and sessions follow their task inside these projects: read with the task, written, created and deleted by whoever may write the task. Task Analysis, the burndown report, milestones and project updates are read by whoever reads the project; milestones and updates are changed by an Admin, as settings of the project. A pull request is read through one of its tasks, and one linked to a task of a Public or Private project is changed, linked or deleted only by whoever may write that task; linking a known link to one's own task stays open. Notifications of a task or of the project reach only partners who can read it, so following gives no access by mail either.

A member row keeps its project and its user: a change is a removal and an addition. Adding a row is checked against the Admins the project had before the row, so nobody makes themselves Admin by creating one; a Project Administrator may add rows to a Standard project and name the first Admins of a project being made Public or Private.

**Mechanism.** For each model and operation the module adds one global rule: `outside OR allow`, where `outside` is a record of a Standard project (or of no project) and `allow` is the role condition for the current user. A global rule is AND-ed with everything else, so it can take access away inside Public and Private projects and leaves every other rule untouched outside them. Where a core or another module's group rule could still deny inside (a followers-only visibility, a missing follower), a group rule for internal users grants the same `inside AND allow`. All domains come from one method on `res.users` called from `domain_force`, built from field paths only: the evaluated domain is cached per user, so a list of project ids computed at evaluation would go stale when a member is added. A role condition needs one field per threshold (`member_user_ids`, `contributor_user_ids`, `editor_user_ids`, `admin_user_ids`, stored, computed from the members): two leaves on `member_ids.user_id` and `member_ids.role` would match different member rows. What a rule cannot say about a write (assignees, stage, a task moved in, stages of the project, a line moved to another task or given a project other than its task's) is checked in `write` and `create`. The superuser and `sudo()` pass every check.
  - 2026-11-18: from 16.0.1.0.1 a session `create` that finds the row of its task and session (ADR 0010) drops a project given with the values: the row keeps its task's project instead of being refused.
  - 2026-11-18: from 16.0.1.0.2 `write` and `create` also check the UAT users of a task (set by an assignee, an Editor or an Admin; a clearing and a move in count, a move in by an Editor or an Admin), a milestone or an update moved into a Public or Private project (checked again there, as the rules read it in the project it leaves), and the sub-tasks Odoo moves in sudo with a task leaving such a project (refused unless the user could write them there).

**Portal tokens.** A portal access token opens a record to whoever holds the link, whatever the rules say. Entering Public or Private clears the tokens of the project and of its tasks, and none is kept there afterwards, whatever wrote it. Attachment tokens stay: the editor writes them into the links of images pasted into descriptions and messages, and a token opens that one file only. Images pasted into task texts become attachments of the task with an access token on save (ADR 0011), so their URL keeps opening that one image to whoever holds it, a removed member included. They are left so: revoking the token would break the link in the text.

**Constraints.** A Public or Private project keeps at least one Admin: the last one is neither removed nor lowered. In a Private project only a member is set as assignee or UAT user; tasks already carrying a non-member when a project turns Private, or when the user stops being a member, are left as they are. The next task of a recurrence there, created by Odoo's recurrence job, takes only the assignees of the task it repeats who are still members: a refusal would undo the job's run for every due recurrence. A Private project cannot be the project that develops this Odoo, where every employee files support requests.

Rejected: rewriting the core rules (`project_public_members_rule`, `task_visibility_rule` and the others) to add the mode. They are noupdate records of `project`; an uninstall would leave them naming fields that no longer exist, and every other module's rule on the same models would need the same treatment.

Rejected: giving the Project / Administrator group Admin rights everywhere. The group is held by people who administer projects of their own; in a Public project they would assign, move and delete the tasks of every other team.

Rejected: member roles that act in every project. Switching a database to roles in one step changes everybody's access at once; per-project modes let each project move when its people are named.

**Consequences.**

- `privacy_visibility` shows on the project form in Standard only; inside Public and Private projects the mode decides. The share buttons are hidden there.
- An Odoo group still gates what a role can use: creating or deleting a task needs the Project / User access rights, changing the project's settings the Project / Administrator rights, as before. A role opens nothing a user's groups do not.
- Moving a task out of a Public or Private project is an edit; moving one in is a create there. A task that leaves for a Standard project, and so its assignees or UAT users changed in the same write, is judged by the rules of the project it lands in: one moved out with them cleared and moved back in has lost them without the role that changes them inside.
- The role checks judge the tasks written, and a change applied to all or to the following tasks of a recurrence writes each of those tasks: they are found before the write, from the recurrence as it stands, and one refusal refuses the change, naming the task it is about.
- A stage shared between a Standard and a Public or Private project is changed only by an Admin of the latter.
- A refusal names no record the user cannot read; reading is checked before the role checks.
- A search on tasks carries the rule's subqueries on the project's user sets for every internal user who is not an administrator.

Reversal cost: uninstall `project_member_access`; its rules, members and fields go and every project is Standard again. A project's mode and members are lost.
