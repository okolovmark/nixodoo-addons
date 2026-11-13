* The path of a pull request link is stored as written. Two spellings of one
  pull request (``Owner/Repo`` and ``owner/repo``, ``http`` and ``https``,
  with and without ``/-/``) are two records.
* Nothing reads a pull request from its host: **State**, **Merge Date** and
  **Deploy Date** are set by hand or over RPC.
* The pull requests of a task are tracked only through ``pr_uri``: one that
  is added without becoming the task's first leaves no trace in the chatter.
  Odoo 16 tracks no many2many field.
* The closing rule and the estimate rule are checked on the tasks of a write
  that names their stage or their project. A stage recomputed for another
  reason is not checked, as for a subtask whose parent changes project, and
  neither are the other tasks of a recurrence that the write is extended to.
* A module that writes a task's new project on its own, before the other
  values of the same write, runs both rules before the Resolution, the task
  type and the estimate of that write arrive. Setting them in an earlier
  write avoids it.
* Neither rule is run when a stage gets marked or a project gets flagged, so
  a task can sit in a marked stage without what the stage requires: a
  Resolution, a pull request, a task type or an estimate. Such a task stops
  the duplication of its project until it gets it.
* The priority star is hidden on the views the description lists. The
  project sharing views of the portal keep it.
* A row of the stage log is written when the change of stage is flushed to
  the database. Several changes of a task's stage within one flush are one
  row, from the stored stage to the last one, and a change undone within one
  flush leaves none.
* A stage written by raw SQL is not logged.
* The user of a recorded row is the one the update leaves on the task as its
  last writer. When the last writer does not change, that is the user of the
  environment that flushes the change: the acting user in an ordinary
  request, also under ``sudo()``.
* The stage name of a row is the source-language name (``en_US``) of the
  stage when the row was written. A row *From the chatter* has the name the
  chatter tracked.
* During an update with the server running, a worker that still runs the
  previous code changes stages without writing a row, and the fill does not
  come back to a task that has rows.
* The fill tells a changed stage from the stage of creation by the 2 seconds
  between the creation of the task and its last stage update. The creation
  date is the start of the transaction, so a task created 2 seconds or more
  into a long transaction gets its row *From the last stage update*, without
  a user, though its stage never changed.
* Of the stage changes the chatter did not track before the log existed, the
  filled past holds at most the stage the task is in, at its last stage
  update.
