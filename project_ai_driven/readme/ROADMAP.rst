* This module does not keep the former Html ``questions`` and ``answers`` of
  the task: without another module that declares them, the update to
  16.0.2.7.0 deletes them with their columns, and their content is in the
  question rows only.
* The conversion pairs list items by position: two lists of as many items
  pair the first question with the first answer, also where an answer was
  written for another question.
* A row kept whole holds every question and answer of its task: split it by
  hand.
* The round of a converted row is the text of a heading, without its
  formatting.
* Question rows have no chatter: a change of status or answer leaves no
  trace but **Answered by** and **Answered on**.
* The test case flag ``passed`` and the translation of the former state
  values (``todo``, ``done``, ``cancelled``) at create and write serve
  clients written for the former selection, until a later version removes
  them.
* The path of a pull request link is stored as written. Two spellings of one
  pull request (``Owner/Repo`` and ``owner/repo``, ``http`` and ``https``,
  with and without ``/-/``) are two records.
* Nothing reads a pull request from its host: **State**, **Merge Date** and
  **Deploy Date** are set by hand or over RPC.
* The pull requests of a task are tracked only through ``pr_uri``: one that
  is added without becoming the task's first leaves no trace in the chatter.
  Odoo 16 tracks no many2many field.
* A change of the Modules of a task leaves no trace in the chatter, for the
  same reason.
* A module that a pull request of the task brings into the code base is
  added to the task only once it is installed in the database.
* Deleting a module from the module list, which Odoo refuses for a module
  installed or about to be, takes it off every task that held it.
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
  portal keeps it: the project sharing views, the task list and the task
  page.
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
* The breadcrumb of a note opened from **Task Notes** shows the name its
  task had when the note was posted; the title of the note, the list and the
  groups show the current name of the task.
* A note whose category is in bold inside a heading or a list, or coloured,
  gets no category: only one opening ``<p>`` or ``<span>`` may come before
  the bold, and nothing between the bold and the category.
* The eight categories and their spelling are fixed in the module:
  ``Bugfix`` or ``Deployment`` in bold is no category.
