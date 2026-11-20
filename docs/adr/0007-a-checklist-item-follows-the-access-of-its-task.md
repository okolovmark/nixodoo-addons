# A checklist item follows the access of its task

Status: accepted (2026-11-17)

The 16.0 branch decided this first. Its record, `docs/adr/0007-a-checklist-item-follows-the-access-of-its-task.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The task decides for its lines.** An item is found and read by whoever can read its task, and created, written and deleted by whoever can write it, archived or not, in the companies selected in the company switcher. The item has two access rights for Project / User: `access_task_checklist_item_read`, read, with the domain `[('task_id', 'access', 'read')]`, and `access_task_checklist_item_write`, create, write and delete, with `[('task_id', 'access', 'write')]`. Odoo 20's `access` operator applies the rules of `project.task` for that operation to the task of each item, as core makes journal items follow their journal entry and tasks their project. A question row (`project.task.question`, ADR 0008) and a test case (`project.task.testcase`, ADR 0005) have the same two rights, `access_task_question_*` and `access_task_testcase_*`. The superuser and `sudo()` are not filtered.

**Change follows write.** Odoo 20 lets a project user read every task template without project, also one they may not write, and the lines of a template reach every task made from it: with read access deciding every operation, any project user could change them.

**Moving is checked at both ends.** Odoo checks a write against the line as it is, so the write right covers the task a line leaves. A write of `task_id` also checks write access on the task it names, so a line is not moved onto a task the user may only read.

**What differs from 16.0.**

- 16.0 says it in two overrides, `_apply_ir_rules` and `_filter_access_rules_python`, held by `project.task.access.mixin`, and only for read: there whoever reads a task creates, changes and deletes its items and rows, and test cases follow no task access, so every project user reaches every case. Odoo 16 has no templates. This branch has no override and no mixin.
  - 2026-11-20: 16.0 gives test cases the mixin too: there a case follows the read access of its task, as an item and a row do.
- Reading, writing or deleting an item or a row deleted meanwhile raises `MissingError`, as core does for a record whose access domain it cannot evaluate: of two users deleting the same line from an open task form, the second save fails. On 16.0 a missing item passes the check.

**Consequences.**

- Reading the task opens its lines; changing one takes write access to the task, and changing an item's state, description or assignee still takes its assignee or reviewer.
- A project user reads the lines of a task template without project and changes them only where they may change the template. Converting an item into a sub-task deletes it, so it takes write access to the task too.
- A user who narrows the company switcher sees fewer lines, as with tasks. An RPC call without `allowed_company_ids` reaches the lines of the tasks of all the user's companies.
- The lines of a task the user cannot read are not found, counted or grouped, in the menus and in name searches. Reading one raises an access error; so do creating, writing or deleting one of a task the user cannot write, and moving one onto such a task.
- A rule any module adds to `project.task` applies to the lines without a change here.
  - 2026-11-18: from 20.0.2.12.0 session rows (`project.task.session`, ADR 0010) follow it too, through `access_task_session_read` and `access_task_session_write`; a create, and a write of `task_id`, check read access on the task they name before anything else.

Reversal cost: merge each pair of rights back into one with the read domain, or drop the domains; a project user then changes the lines of every task they read, or reaches every line. No data changes either way.
