* On a project, tick **AI-driven Project** (*Settings* tab, above
  *Visibility*) to show the Technical Solution and Pull Requests tabs and
  the Resolution on its tasks, and to hide the core priority star on them.
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
  The duplicate of a project copies each task with its type and its
  estimate, so it is refused only where a task sits in such a stage without
  them.
* Checklist items and test cases are managed by **Project / User**. The
  Checklist, Test Cases and Technical Solution tabs are hidden from users
  without that group.
* Pull requests are read by every internal user and created and edited by
  **Project / User**. No access right allows deleting one.
* The stage log is read by **Administration / Settings** only. No access
  right allows creating, changing or deleting a row.
* To be notified of checklist items that are neither assigned to you nor
  created by you, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items with an account that
  has no email address passes ``mail_auto_subscribe_no_notify`` in the
  context; without it Odoo refuses to send the notification and the write
  fails.
