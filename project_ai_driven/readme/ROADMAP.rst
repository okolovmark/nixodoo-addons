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
* Session rows have no chatter either: a session that writes its row again
  replaces its date, and its outcome when it sends one; the former values
  are not kept.
* Two first creates of the same task and session in concurrent transactions
  both find no row: the later one fails on the unique constraint, and
  nothing retries it.
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
* The three stage rules are checked on the tasks of a write that names
  their stage or their project. A stage recomputed for another reason is not
  checked, as for a subtask whose parent changes project.
* A module that writes a task's new project on its own, before the other
  values of the same write, runs the rules before the Resolution, the task
  type, the estimate and the Actual Hours of that write arrive. Setting them
  in an earlier write avoids it.
* No rule is run when a stage gets marked or a project gets flagged, nor on
  the creation of the next task of a recurrence, in the first stage of its
  project or, when the project lists none, in the stage of the task it
  repeats, so a task can sit in a marked stage without what the stage
  requires: a Resolution, a pull request, a task type, an estimate or Actual
  Hours. Such a task stops the duplication of its project until it gets it.
* A duplicated project copies its closed tasks with their Actual Hours, and
  each copy gets the time of the copy as its last stage update: the copies
  count again in ``get_estimate_coefficient``, as the most recently closed
  tasks of their assignees, while they are among the newest 30.
* The creation of the next task of a recurrence is told by a marker in the
  context of that creation: a task that an automated action creates inside
  it, on the creation of the next task, enters its stage unchecked too, and
  a test case it creates there Not run keeps the Verification given with
  it.
* A stage saved for *All tasks* or *This and following tasks* of a
  recurrence is refused when one of the tasks it reaches has no project:
  Odoo's check of a personal stage runs on every task reached.
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
* A link to another task's image or file on a host or port other than that
  of the task's ``get_base_url()`` stays as it is: a copy of the text still
  links the source task's attachment, and the link breaks when that task is
  deleted.
* An image in another attribute (a ``poster``, a ``data-*``) is not read
  and stays as it is, inline or linked. Neither is a ``<style>`` element,
  which the sanitizer drops.
* A file link is copied only where a new record, or a row moved to another
  task, copies images: a write that pastes one leaves it pointing at the
  other task.
* A link is copied in the forms ``/web/image/<id>`` and ``/web/content/<id>``
  only: the other forms the brief reads, such as
  ``/web/content/ir.attachment/<id>/datas`` or ``/web/content?id=<id>``,
  stay as they are.
* A large image that a copied text both shows and links as a file gives
  the new task two attachments: the image resized, the file as uploaded.
* An image taken out of a text, and the images of a deleted question row or
  test case, stay among the attachments of the task.
* The brief removes tokens from a url that stands as a word of a text or of
  code on a best-effort basis: a url split by markup (bold, a line break),
  or holding a brace, ``<`` or ``>``, or a bracket or a quote in its query,
  may keep its token. A token written loose, outside a url, stays as
  written.
* A few table shapes come out otherwise than a browser shows them: a
  browser ends what holds a part of the table there, the brief does not.

  * A row inside a row: the cells after it stay in the outer row.
  * A cell or a caption inside a cell, or a cell inside a caption: it is
    part of their text.
  * Text after a row inside a cell stays in the cell.
  * A table inside a list item or a quote that stands loose in another
    table ends that table, but the item or the quote, which a browser
    closes there, keeps the rest of what it holds.
  * A rowspan over rows that stand right in the table runs on past an empty
    row group between them.
  * A cell inside a heading, preformatted text or code loose in the table:
    the words on both sides of it stay in one heading, code block or code
    span.
* The brief is computed on each read that names it, for each task of the
  read: a read of many tasks naming ``brief`` costs one brief per task.
* The brief holds the last 10 internal notes of the task; older ones are
  read from ``mail.message``.
