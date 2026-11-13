* The path of a pull request link is stored as written. Two spellings of one
  pull request (``Owner/Repo`` and ``owner/repo``, ``http`` and ``https``,
  with and without ``/-/``) are two records.
* Nothing reads a pull request from its host: **State**, **Merge Date** and
  **Deploy Date** are set by hand or over RPC.
* The pull requests of a task are tracked only through ``pr_uri``: one that
  is added without becoming the task's first leaves no trace in the chatter.
  Odoo 16 tracks no many2many field.
* The closing rule is checked on the tasks of a write that names their stage
  or their project. A stage recomputed for another reason is not checked, as
  for a subtask whose parent changes project.
* A module that writes a task's new project on its own, before the other
  values of the same write, runs the closing rule before the Resolution of
  that write arrives. Setting the Resolution in an earlier write avoids it.
* The closing rule is not run when a stage gets marked or a project gets
  flagged, so a task can sit in a marked stage with neither a Resolution nor
  a pull request. Such a task stops the duplication of its project until it
  gets one.
