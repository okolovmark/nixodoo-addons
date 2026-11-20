* The path of a pull request link is stored as written. Two spellings of one
  pull request (``Owner/Repo`` and ``owner/repo``, ``http`` and ``https``,
  with and without ``/-/``) are two records.
* Nothing reads a pull request from its host: **State**, **Merge Date** and
  **Deploy Date** are set by hand or over RPC.
* A module that a pull request of the task brings into the code base is
  added to the task only once it is installed in the database.
* Deleting a module from the module list, which Odoo refuses for a module
  installed or about to be, takes it off every task that held it.
* The closing rule and the estimate rule are checked on the tasks of a write
  that names their stage or their project, but for a sub-task given a parent
  with the project and the stage that follow from it. A stage recomputed
  for another reason is not checked, as for a sub-task whose parent changes
  project or that gets another parent.
* A module that writes a task's new project on its own, before the other
  values of the same write, runs both rules before the Resolution, the task
  type and the estimate of that write arrive. Setting them in an earlier
  write avoids it.
* Neither rule is run when a stage gets marked or a project gets flagged,
  nor on the next occurrence of a recurring task, so a task can sit in a
  marked stage without what the stage requires: a Resolution, a pull
  request, a task type or an estimate. Such a task cannot be duplicated, and
  it stops the duplication of its project, until it gets it.
* The next occurrence of a recurring task is exempt from both rules by a
  key in the context of its creation: a task that an automation of an
  administrator, run on creation, creates inside that creation is exempt
  too.
* A checklist item is not converted into a sub-task while the first unfolded
  stage of its AI-driven project is marked for either rule: the sub-task
  enters that stage without a Resolution, a task type or an estimate. The
  item stays. An item of a template converts.
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
  and the tasks of a project template turned back into a project, keep
  their stage unchecked by either rule.
* A sub-task that sits in a marked stage without what the stage requires
  stops a project template being made from its project, as it stops the
  duplication of the project: core creates its copy in that project first.
* The sizes stored by an earlier 20.0 version of the module stay as they
  are until the hours of their task change: this branch has no migration
  that recomputes them.
* An update from an earlier 20.0 version converts no data but the test case
  states: the former checklist items stay in a table nothing reads, and the
  Html questions and answers are deleted (see *Installation*).
* Question rows have no chatter: a change of status or answer leaves no
  trace but **Answered by** and **Answered on**.
* Session rows have no chatter either: a session that writes its row again
  replaces its date, and its outcome when it sends one; the former values
  are not kept.
* Two first creates of the same task and session in concurrent transactions
  both find no row: the later one fails on the unique constraint, and
  nothing retries it.
* A note whose category is in bold inside a heading or a list, or coloured,
  gets no category: only one opening ``<p>`` or ``<span>`` may come before
  the bold, and nothing between the bold and the category.
* The eight categories and their spelling are fixed in the module:
  ``Bugfix`` or ``Deployment`` in bold is no category. A change of them
  gives existing notes a category only where a migration recomputes it.
* A note that code logs with a change of tracked fields
  (``_track_set_log_message``) is part of the tracking message of the
  change: it gets no category and is not in **Task Notes**.
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
* A link to another record's image or file on a host or port other than
  that of the task's base url stays as it is: a copy of the text still links
  the source record's attachment, and the link breaks when that record is
  deleted.
* An image in another attribute (a ``poster``, a ``data-*`` other than the
  original of an edited image) or in an HTML comment is not read and stays
  as it is, inline or linked. Neither is a ``<style>`` element,
  which the sanitizer drops.
* A file link is copied only where a new record, or a row moved to another
  task, copies images: a write that pastes one leaves it pointing at the
  other record.
* A link is copied in the forms ``/web/image/<id>`` and ``/web/content/<id>``
  only: the other forms the brief reads, such as
  ``/web/content/ir.attachment/<id>/datas`` or ``/web/content?id=<id>``,
  stay as they are.
* A large image that a copied text both shows and links as a file gives
  the new record two attachments: the image resized, the file as uploaded.
* An image taken out of a text stays an attachment of its record.
* The brief removes tokens from a url that stands as a word of a text or of
  code on a best-effort basis: a url split by markup (bold, a line break),
  or holding a brace, ``<`` or ``>``, or a bracket or a quote in its query,
  may keep its token. A token written loose, outside a url, stays as
  written.
* The brief is computed on each read that names it, for each task of the
  read: a read of many tasks naming ``brief`` costs one brief per task.
* The brief holds the last 10 internal notes of the task; older ones are
  read from ``mail.message``.
