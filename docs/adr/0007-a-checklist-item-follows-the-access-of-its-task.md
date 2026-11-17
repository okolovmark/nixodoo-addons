# A checklist item follows the access of its task

Status: accepted (2026-11-17)

The 16.0 branch decided this first. Its record, `docs/adr/0007-a-checklist-item-follows-the-access-of-its-task.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The task decides for its items.** An item is found, read, written, deleted and created exactly by whoever can read its task, archived or not, in the companies selected in the company switcher. The access right of the item, Project / User with all four operations, carries the domain `[('task_id', 'access', 'read')]`: Odoo 20's `access` operator applies the read rules of `project.task` to the task of each item, as core makes journal items follow their journal entry and tasks their project. The superuser and `sudo()` are not filtered. A question row (`project.task.question`, ADR 0008) has the same domain on its access right.

**What differs from 16.0.**

- 16.0 says it in two overrides, `_apply_ir_rules` and `_filter_access_rules_python`, held by `project.task.access.mixin`. This branch has no override and no mixin.
- Reading, writing or deleting an item or a row deleted meanwhile raises `MissingError`, as core does for a record whose access domain it cannot evaluate: of two users deleting the same line from an open task form, the second save fails. On 16.0 a missing item passes the check.

**Consequences.**

- Reading the task opens its items; changing an item's state, description or assignee still takes its assignee or reviewer.
- A user who narrows the company switcher sees fewer items, as with tasks. An RPC call without `allowed_company_ids` reaches the items of the tasks of all the user's companies.
- The items of a task the user cannot read are not found, counted or grouped, in the menu and in name searches. Reading, writing or deleting one, or creating one on such a task, raises an access error.
- A rule any module adds to `project.task` applies to the items without a change here.

Reversal cost: drop the domain of the two access rights; every Project / User reaches every item and row again. No data changes either way.
