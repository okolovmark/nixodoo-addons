* On a project, switch on **AI-driven Project** (*Settings* tab, *Tasks
  Management*) to show the Technical Solution and Pull Requests tabs and the
  Resolution on its tasks, and to hide the core priority star on them.
* Below it, a system administrator (``base.group_system``) switches on
  **Develops This Odoo** on the one AI-driven project whose tasks change
  this Odoo database: its tasks show their Modules. The setting is shown to
  system administrators only, on a project that is AI-driven or carries the
  flag. One project carries it at most, archived projects included: to move
  it, switch it off on the project that has it, then on on the other one.
  Only a system administrator archives or deletes the project that carries
  it.
* Then, on an AI-driven project, **Stages Requiring a Resolution** lists
  the stages a task enters only with a Resolution, and with a pull request
  when the Resolution is Code. Only stages of the project can be picked.
  Leave it empty to switch the closing rule off.
* Below that, **Stages Requiring a Type and an Estimate** lists the stages a
  task enters only with a task type and an estimate above zero hours. Only
  stages of the project can be picked. Leave it empty to switch the estimate
  rule off.
* What creates a task straight into a stage of that second list without
  both values is refused there: the kanban quick create, which takes a title
  and assignees; an email to the alias of the project, which gives neither
  value; a sales order line that creates a task, which gives no task type.
  A copy that keeps the stage of its source, a plain Duplicate or the
  duplication of a project, takes the type and the estimate of its source,
  so it is refused only where its source sits in such a stage without them.
* Checklist items, question rows and test cases are managed by users with
  the **User** access of *Project* (Settings > Users, the *Access Rights* tab
  of a user). Such a user reads the lines of the tasks they can read and
  creates, changes and deletes those of the tasks they can change: core
  lets a project user read every task template without project, and so its
  lines, but change them only where the template may be changed. The
  Checklist, Test Cases and Technical Solution tabs are hidden from users
  without that access.
* **Project > Task Notes** and the **Notes** button of the task form are
  shown to users with the **User** access of *Project*. They list the notes
  of the tasks the user can read; the menu leaves out those of templates.
* Pull requests are read by every internal user and created and edited by
  users with the **User** access of *Project*. No access right allows
  deleting one.
* The Modules of a task, on the form and in the task search, are shown to
  users with the **User** access of *Project* only.
* The stage log is read by system administrators (``base.group_system``)
  only. No access right allows creating, changing or deleting a row.
* To be notified of checklist items of which you are neither the assignee
  nor the reviewer, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items passes
  ``mail_auto_subscribe_no_notify`` in the context to keep the write silent:
  no assignment notification, no checklist post. Without the key the
  notifications are queued as for any user, also for an account that has no
  email address to send them from.
