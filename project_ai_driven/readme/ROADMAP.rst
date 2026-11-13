* The path of a pull request link is stored as written. Two spellings of one
  pull request (``Owner/Repo`` and ``owner/repo``, ``http`` and ``https``,
  with and without ``/-/``) are two records.
* Nothing reads a pull request from its host: **State**, **Merge Date** and
  **Deploy Date** are set by hand or over RPC.
* The pull requests of a task are tracked only through ``pr_uri``: one that
  is added without becoming the task's first leaves no trace in the chatter.
* The closing rule is checked on the tasks of a write that names their stage
  or their project. A stage recomputed for another reason is not checked, as
  for a subtask whose parent changes project.
* A module that writes a task's new project on its own, before the other
  values of the same write, runs the closing rule before the Resolution of
  that write arrives. Setting the Resolution in an earlier write avoids it.
* The closing rule is not run when a stage gets marked or a project gets
  flagged, so a task can sit in a marked stage with neither a Resolution nor
  a pull request. Such a task cannot be duplicated, and it stops the
  duplication of its project, until it gets one.
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
