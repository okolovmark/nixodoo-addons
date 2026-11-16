# The checklist is project.task.checklist.item, without an alias

Status: accepted (2026-11-16)

The checklist of a task was the model `project.task.subtask`, held by the task field `subtask_ids`, with a kanban summary `kanban_subtasks`. In core Odoo a sub-task is a task: `child_ids` (Sub-tasks), `subtask_count`, the Sub-tasks setting of a project. On one task form, and over the API, the same word named a child task and a line of the checklist. A row of the checklist model is one line: a description, an assignee, a reviewer, a deadline and a state.

From 16.0.2.6.0 the model is `project.task.checklist.item` (table `project_task_checklist_item`, described as Checklist Item), the task field is `checklist_item_ids`, labelled Checklist, and the kanban summary is `kanban_checklist`. The post a change makes on the task is a private method of the item, `_notify_checklist_item_change`; `send_subtask_email` of the task is gone. Nine external ids follow the new names, and the search filters of the checklist are `my` and `todo`. The rename is hard: no alias, no second model, no field under a former name. A client on the former names gets "Object project.task.subtask doesn't exist".

Rejected: keeping the former names for one more release, as a second model on the same table with every field declared twice and the former one2many on the task, removed in a later version. That is more code in a public module and a second deploy. A renamed module needs such a release (ADR 0001): a restart without the update leaves the modules that depend on it unloaded. A renamed model has no such trap: the module keeps its name, and its own update runs the rename.

Rejected: `project.task.checklist` and `checklist_ids`. A record is one item of a checklist, not the checklist; the task's items together are its checklist.

**Existing data.** The pre-migration, and the pre-init hook after a take-over from the former module names, call `rename_checklist`. Before the data files load, it renames by SQL the table with its sequence, constraints and indexes, the registrations of the constraints, the model and its fields, the copies of the fields on a model that delegates to the task, related paths and dependencies, the model name where a record stores one and in Reference values, the external ids in every module, the fields in the stored arch of the views of the checklist and of the models holding the fields (as whole words), the fields and the model in rule domains, filters and window actions, and the file recorded on the views. It takes `default_user`, a task field the update deletes, out of this module's stored views. Every record keeps its database id. A second run changes nothing.

**Consequences.**

- Every client moves to the new names in the deploy that brings the update: RPC scripts, and modules that name the model, the fields, the external ids or the search filters. Nothing answers to the former names.
- The update runs with the server stopped: renaming the table takes an exclusive lock on it, and running code reads the former names.
- Not rewritten: the code of server actions and scheduled actions, the bodies of mail templates, the field paths of export lines, the views of other models, and a context that turns on a search filter by its former name. None ships with the module.
- Items, their activities and the subscriptions to All checklist updates keep their ids and stay as they are.

Reversal cost: a rename of the same shape back, `rename_checklist` with the names swapped, and every client moved again in one deploy. `default_user` would have to be written again.
