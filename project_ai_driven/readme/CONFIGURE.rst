* On a project, tick **AI-driven Project** (*Settings* tab, above
  *Visibility*) to show the Technical Solution, Pull Requests and Sessions
  tabs, the Resolution and Actual Hours on its tasks, and to hide the core
  priority star on them.
* Below it, a Settings administrator ticks **Develops This Odoo** on the one
  AI-driven project whose tasks change this Odoo database: its tasks show
  their Modules. The field is shown to **Administration / Settings** only,
  on a project that is AI-driven or carries the flag. One project carries
  it at most, archived projects included: to move it, clear it on the
  project that has it, then tick it on the other one. Only a Settings
  administrator archives or deletes the project that carries it.
* Then, on an AI-driven project, **Stages Requiring a Resolution** lists
  the stages a task enters only with a Resolution, and with a pull request
  when the Resolution is Code. Only stages of the project can be picked.
  Leave it empty to switch the closing rule off.
* Below that, **Stages Requiring a Type and an Estimate** lists the stages a
  task enters only with a task type and an estimate above zero hours. Only
  stages of the project can be picked. Leave it empty to switch the estimate
  rule off.
* Below that, **Stages Requiring Actual Hours** lists the stages a task
  enters only with Actual Hours above zero; timesheet hours do not count.
  Only stages of the project can be picked. Leave it empty to switch the
  actual hours rule off.
* What creates a task straight into a stage of that second list without
  both values is refused there: an email to the alias of the project, which
  gives neither value; a sales order line that creates a task, which gives
  no task type. The kanban quick create asks for both in such a column, and
  refuses an Estimate left at zero; a column whose stage also requires a
  Resolution refuses it, as it asks for none. The duplicate of a project
  copies each task with its type and its estimate, so it is refused only
  where a task sits in such a stage without them.
* The same holds for the third list: an email to the alias, a sales order
  line and the kanban quick create give no Actual Hours, so they are refused
  in its stages. The duplicate of a project copies each task with its Actual
  Hours, as it keeps its stage, so it is refused only where a task sits in
  such a stage without them.
* Checklist items, question rows, session rows and test cases are managed
  by **Project / User**, each only by a user who can read its task. The
  Checklist, Test Cases, Technical Solution and Sessions tabs are hidden
  from users without that group.
* The completion and the card summaries a task takes from its checklist
  items and test cases are read by **Project / User** only, as the lines
  are: the task cards of a user without that group leave them out, and so
  does a read of the task.
* Pull requests are read by every internal user and created and edited by
  **Project / User**. No access right allows deleting one.
* The Modules of a task, on the form and in the task search, are shown to
  **Project / User** only.
* The stage log is read by **Administration / Settings** only. No access
  right allows creating, changing or deleting a row.
* **Project > Task Notes** and the **Notes** button of the task form are
  shown to **Project / User**. A project user lists and groups there the
  notes of the tasks they can read, by category, task, author, date, model,
  message type or subtype; Odoo lets only administrators group other
  messages, or group by any other field.
* To be notified of checklist items of which you are neither the assignee
  nor the reviewer, edit your follower subscription on the task or on the
  project and tick *All checklist updates*.
* An integration that writes tasks or checklist items with an account that
  has no email address passes ``mail_auto_subscribe_no_notify`` in the
  context; without it Odoo refuses to send the notification and the write
  fails.
* The images that task texts give to attachments follow core's system
  parameters for every attachment upload: an image larger than
  ``base.image_autoresize_max_px`` (``1920x1920`` by default, ``0`` to keep
  every size), of a type listed in ``base.image_autoresize_extensions``
  (``png,jpeg,bmp,tiff``), is resized at the quality of
  ``base.image_autoresize_quality`` (``80``).
