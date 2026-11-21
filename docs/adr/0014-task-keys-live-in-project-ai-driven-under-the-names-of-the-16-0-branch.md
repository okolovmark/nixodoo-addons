# Task keys live in project_ai_driven, under the names of the 16.0 branch

Status: accepted (2026-11-18), amended (2026-11-21: a refused create spends the key it drew and names the task by its name only)

The 16.0 branch decided this first. Its record, `docs/adr/0014-task-keys-live-in-project-ai-driven-under-the-names-of-oca-project-key.md` there, holds the alternatives it weighed and every way its keys differ from OCA `project_key`; this one states the decision as it stands on 20.0 and what differs.

A task is referred to by its key, PROJ-1234: in messages, in commits, in links to `/tasks/PROJ-1234`. From 20.0.2.17.0 `project_ai_driven` gives projects and tasks their keys, as 16.0.2.17.0 does there, and not a module of its own: the key is how a task is named.

**The names are those of the 16.0 branch.** `key` on the project (10 characters, indexed, not copied) and on the task (20 characters, indexed), `task_key_sequence_id` on the project (restrict on delete), the computed `url` on the task, and the unique constraints on both keys, whose database names (`project_project_project_key_unique`, `project_task_task_key_unique`) are those of 16.0. Each project's sequence has the code `project.task.key.<project id>` and the prefix `<KEY>-`. A database upgraded from 16.0 keeps every key, every sequence and its next number, and a caller written for one branch reads and searches `key` on the other.

**What the keys do** is what they do on 16.0: a key from the initials of the project's name, letters and digits only, with the lowest free counter, the next key of its project for each new task, new keys for a moved task and the sub-tasks that move with it or with a new parent, no key for a task taken out of its project or a copy without one, renamed task keys keeping the digits after their last hyphen, a key without such an end left as it is, and a renamed sequence prefix for a new project key, cleared task keys for a cleared one, a sequence of its own for a copied project, the key before the name in the display name, a name search by key, and `/projects/<key>` and `/tasks/<key>` redirecting to the record of that key, whatever its case, among those the user can read, with `%` and `_` as themselves. Keys stay off in a run with `test_enable` unless the context sets `test_project_key`.

**What differs on 20.0.**

- No takeover of `project_key`. On 16.0 the module stands down while `project_key` is loaded and documents the order of its uninstall, because installed databases ran it. This branch gives the keys whatever else is installed, as on a 16.0 database that never had `project_key`: no database that has to be kept is known to run this branch (ADR 0001), and none with `project_key`. A database with both would draw two numbers for every task.
  Rejected: porting the stand-down and its tests for a state no known database is in.
- An install hook. It is this branch's first; ADR 0001 and ADR 0004 do without one because the state they would convert is an earlier version's. Missing keys are the state of every database that installs the module with projects in it, and the project form requires a key, so each such project could not be saved until somebody typed one. The hook and the migration of 20.0.2.17.0 run the same `_set_missing_keys` as 16.0: a key and a sequence for each project without a key, archived ones included, then the next numbers of its sequence for its tasks without a key in the order they were created, by one SQL statement that leaves their write dates. Projects that have a key, and their tasks, are left alone. A run with `test_enable` gives no keys.
- The display name comes from `_compute_display_name`: Odoo 20 has no `name_get`. Odoo 20 checks field access on reads and when it turns a domain into SQL, and a portal user reaches only the task fields `TASK_PORTAL_READABLE_FIELDS` lists, so the module adds `key` to them: a portal user sees the key in a task's name as everyone does, and finds a task by it, in a name search and through `/tasks/<key>`. That redirect still leads to the web client's form, which Odoo does not open for a portal user: it sends them to the portal home, `/my`. Leading them to the task in the portal instead is a separate call. The key is still written with sudo, as core refuses it in a portal user's values, so a portal collaborator's task gets its key right after it is created.
  Rejected: reading the key with sudo wherever a portal user meets it, as the first port did. A name search and the redirect search by the key, and a sudo read does not reach a domain.
- A generated key is cut to the 10 characters of its column, its counter included: Odoo 20 does not cut a Char to its size, so a longer key failed the insert. Odoo 16 cuts the value itself.
- The redirects answer 303, which no browser caches, so a key that later names another record leads there; the 16.0 redirects answer 301.
- URLs are Odoo 20's: the task's `url` is `/odoo/action-project.action_view_task/<id>`, and the redirects lead to `/odoo/action-<action>/<id>`. An unknown key, or one the user cannot read, leads to `/odoo/action-<action>/new`, the form of a new record. The 16.0 redirect gives the id `-1`, a form of no record, which the Odoo 20 router does not accept: it takes a numeric id or `new`.
  Rejected: the action's list, and a 404. Both change what an unknown key shows; the empty form keeps the 16.0 meaning.
- A copied project passes its name with its translations; the key is made from the name in the user's language, else in English.
- A project may have no company on Odoo 20; its sequence then has none either.
- The task form shows the key before the title at all times: Odoo 20 forms have no read-only mode, where 16.0 shows it outside edit mode only.

**Consequences.**

- The project form requires a key, as on 16.0. Keys being off in a test run, a test of another module that saves a project through `Form` without a key fails on the required field.
- A generated key may clash with the key of a project the user cannot read, in another company for one: the counter searches what the user reads, and the unique constraint then refuses the project.
- `project_support_request` shows each request's key and creates new requests with theirs; on this branch it reads `key` directly, as `project_ai_driven` is its dependency.
- Keys may skip numbers. A create that draws its key before the rules (so the assignment notice names it) and is then refused spends that number: the sequences are standard ones, PostgreSQL sequences that a rollback does not undo, like any other failed create. Every refusal of a create by the rules of this module and of project_member_access names the task by its name only, not by the key it never kept (2026-11-21).

Reversal cost: the keys are columns of their own and the sequences records of their own; dropping them takes the fields out of the module and the keys out of every reference made to them since.
