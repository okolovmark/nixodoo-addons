* On a project, switch on **AI-driven Project** (*Settings* tab, *Tasks
  Management*) to show the Technical Solution and Pull Requests tabs and the
  Resolution on its tasks, and to hide the core priority star on them.
* Below it, on an AI-driven project, **Stages Requiring a Resolution** lists
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
* Checklist items and test cases are managed by users with the **User**
  access of *Project* (Settings > Users, the *Access Rights* tab of a user).
  The Checklist, Test Cases and Technical Solution tabs are hidden from users
  without it.
* Pull requests are read by every internal user and created and edited by
  users with the **User** access of *Project*. No access right allows
  deleting one.
* The stage log is read by system administrators (``base.group_system``)
  only. No access right allows creating, changing or deleting a row.
* To be notified of checklist items that are neither assigned to you nor
  created by you, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items passes
  ``mail_auto_subscribe_no_notify`` in the context to keep the write silent:
  no assignment notification, no checklist post. Without the key the
  notifications are queued as for any user, also for an account that has no
  email address to send them from.
