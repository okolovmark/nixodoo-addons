* The path of a pull request link is stored as written. Two spellings of one
  pull request (``Owner/Repo`` and ``owner/repo``, ``http`` and ``https``,
  with and without ``/-/``) are two records.
* Nothing reads a pull request from its host: **State**, **Merge Date** and
  **Deploy Date** are set by hand or over RPC.
* The closing rule and the estimate rule are checked on the tasks of a write
  that names their stage or their project. A stage recomputed for another
  reason is not checked, as for a subtask whose parent changes project.
* A module that writes a task's new project on its own, before the other
  values of the same write, runs both rules before the Resolution, the task
  type and the estimate of that write arrive. Setting them in an earlier
  write avoids it.
* Neither rule is run when a stage gets marked or a project gets flagged, so
  a task can sit in a marked stage without what the stage requires: a
  Resolution, a pull request, a task type or an estimate. Such a task cannot
  be duplicated, and it stops the duplication of its project, until it gets
  it.
* A recurring task cannot be set Done or Cancelled while the first stage of
  its AI-driven project is marked for the closing rule, unless the task sits
  in that stage: its next occurrence enters that stage without a
  Resolution, and the refusal takes the change of state with it. The same
  holds where that stage is marked for the estimate rule and the task has no
  task type or no estimate.
* The priority star is hidden on the views the description lists. The
  portal keeps it: the project sharing views, the task list and the task
  page.
* The task list of a flagged project drops its *Priority* column only when
  opened from the project, which tells it the project is flagged. Reloaded
  from its address, reached back from an embedded action or opened from a
  milestone, it shows the column with the stars hidden, as core shows its
  *Milestone* column there. A task opened from that list passes the flag
  on, whatever its own project: the *Sub-tasks*, *Dependent Tasks*, *Tasks in
  Recurrence* and parent task it opens have no *Priority* column, tasks of
  other projects included. The same task opened from another list passes
  nothing.
* Leaving template status is not a change of stage: a task template undone,
  a project template turned back into a project or a template task moved to
  another project keeps its stage unchecked by either rule.
* A sub-task that sits in a marked stage without what the stage requires
  stops a project template being made from its project, as it stops the
  duplication of the project: core creates its copy in that project first.
* The sizes stored by an earlier 20.0 version of the module stay as they
  are until the hours of their task change: this branch has no migration
  that recomputes them.
* The stage log starts when the module is installed: the stages a task went
  through before that are not in it.
* A row of the stage log is written when the change of stage is flushed to
  the database. Several changes of a task's stage within one flush are one
  row, from the stored stage to the last one, and a change undone within one
  flush leaves none.
* A stage written by raw SQL is not logged.
* The user of a row is the one the update leaves on the task as its last
  writer. When the last writer does not change, that is the user of the
  environment that flushes the change: the acting user in an ordinary
  request, also under ``sudo()``.
* The stage name of a row is the source-language name (``en_US``) of the
  stage when the row was written.
