# The checklist is project.task.checklist.item, without an alias

Status: accepted (2026-11-17), amended (2026-11-20: the next occurrence of a recurring task takes the items TODO)

The 16.0 branch decided this first. Its record, `docs/adr/0006-the-checklist-is-project-task-checklist-item-without-an-alias.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The names.** A line of a task's checklist is one item: a description, an assignee, a reviewer, a deadline and a state. A sub-task stays what core calls one, a task. The model is `project.task.checklist.item` (table `project_task_checklist_item`, described as Checklist Item), the task field `checklist_item_ids`, labelled Checklist, and the kanban summary `kanban_checklist`. The views, the window action, the menu and the two All checklist updates subtypes carry the external ids 16.0 gives them, and the search filters are `my` and `todo`. The access rights are two, `access_task_checklist_item_read` and `access_task_checklist_item_write` (ADR 0007), where 16.0 has one, `access_task_checklist_item`. The rename is hard: no alias, no second model, no field under a former name. A client on the former names fails.

**The post.** A creation and a change of state, description or assignee post on the task through `_notify_checklist_item_change` of the item, to its assignee, its reviewer and, on a change of assignee, its former assignee, never to the user making the change; with no one else left, the post is a note to no one. `send_subtask_email` and the computed `default_user` of the task are gone. A new item's assignee and reviewer default to its author. Items have no activities.

**What differs from 16.0.**

- Nothing is renamed in the database (ADR 0001). 16.0 renames by SQL the table, the model, its fields, the external ids and the stored views, rules, filters and actions that name them, keeping every database id. Here an update from an earlier 20.0 version creates `project.task.checklist.item` with an empty table; the items stay in the table `project_task_subtask`, which no model reads.
- The records of the data files are created under the new external ids, and the former ones are deleted at the end of the update: the two All checklist updates subtypes go with the follower subscriptions to them.
- The external ids that mail (selection values of the activity fields) and phone_validation (the activity mixin inheritance) hold for the former model stay; nothing reads them.
- Copying a task copies its items in every copy Odoo 20 makes, sub-tasks included, as lines of `copy_data` (ADR 0005): with their state and deadline in a plain duplicate and a duplicated project, TODO without deadline in a copy from or into a template. The next occurrence of a recurring task, sub-tasks included, gets them TODO without deadline too (ADR 0005), as on 16.0; core creates it by a plain create, so its items are posted to their assignee and reviewer as those of a new task. The items of a copy are created inside core's copy, under `mail_auto_subscribe_no_notify`, so no item is posted: 16.0 posts each copied item to its assignee and reviewer.

**Consequences.**

- Every client moves to the new names in the deploy that brings the update: RPC scripts, and modules that name the model, the fields, the external ids or the search filters.
- Nothing outside the module's own records is rewritten: server actions, mail templates, export lines, saved filters and views of other modules that name the former names fail or match nothing. None ships with the module.
- On a database updated from an earlier 20.0 version, the checklists start empty, and followers who had ticked All checklist updates tick it again. The former items stay in `project_task_subtask` until the table is dropped by hand; this module neither reads nor drops it. Its foreign key on the assignee restricts deletion, so a user assigned to such an item cannot be deleted until then.

Reversal cost: rename the model, the fields and the external ids back in the code and move every client again in one deploy. An update would leave the items of this model behind the same way, unless a migration moves them.
