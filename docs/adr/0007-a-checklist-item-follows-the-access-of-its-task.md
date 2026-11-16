# A checklist item follows the access of its task

Status: accepted (2026-11-16)

A checklist item had one access right, Project / User with read, write, create and delete, and no record rule. Every project user found, read and changed the items of every task, including the tasks a project's visibility or a company kept from them, and the Checklist menu listed them. An item is a line of its task's work and is as private as the task.

From 16.0.2.6.1 the task decides for its items: an item is found, read, written, deleted and created exactly by whoever can read its task, archived or not. The item applies the read rules of `project.task`, the company selection included, as a filter on the tasks the user can read: in `_apply_ir_rules`, which serves search, read_group and reads, and in `_filter_access_rules_python`, which serves `check_access_rule` for write, unlink and create. The superuser and `sudo()` are not filtered. The access right stays Project / User. The Checklist menu lists the items of the tasks of the selected companies, as My Tasks lists tasks.

Rejected: record rules of the item's own that repeat the task's (project visibility, followers, company). Every change to task access would have two places to change, and a rule another module adds to the task would have no twin on the item.

Rejected: following the user's companies instead of the selected ones. The Checklist menu would list items whose task the user cannot open in that selection.

**Consequences.**

- Reading the task opens its items; changing an item's state, description or assignee still takes its assignee or reviewer.
- A user who narrows the company switcher sees fewer items, as with tasks. An RPC call without `allowed_company_ids` reaches the items of the tasks of all the user's companies.
- The items of a task the user cannot read are not found, counted or grouped, in the menu and in name searches. Reading, writing or deleting one, or creating one on such a task, raises an access error.
- A rule any module adds to `project.task` applies to the items without a change here.
- An item already deleted passes the check, as core lets a missing record through: two users deleting the same line from an open task form do not fail.
- A search of items carries a subquery of the tasks the user can read.

Reversal cost: drop the two overrides; every Project / User reaches every item again. No data changes either way.
