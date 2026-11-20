On the task form, which opens on Description:

* Set **Priority**, **Estimate** (in hours) and **Task Type** next to the
  tags. **Size** shows the size of the estimate and cannot be edited.
* Add the **UAT users**, the people who run the acceptance tests of the
  task, after the Assignees.
* *Checklist* tab: add items, each with an **Assigned to** user, yourself
  unless you pick another, and an optional deadline. You are the reviewer of
  the items you add. A new task or sub-task saves with the items added before
  its first save: they are created right after the task, as items added to
  it once saved. The assignee or the reviewer moves an item with the
  state icons (TODO, Waiting, Done, Cancelled), changes its description or
  gives it another assignee. They also convert it into a sub-task of the
  task with the *Convert to Sub-task* icon of its row, after a confirmation:
  the item is deleted and a note on the task links the sub-task. The icon
  shows only to who may convert: on a task you may change, the assignee, or
  the reviewer when they may open the task's project. On a task template
  without a project the reviewer converts only as a project manager; a
  private task shows no icon. The task's kanban card shows your own
  completion percentage, only when you have non-cancelled items assigned to
  you.
* *Technical Solution* tab (AI-driven projects): the **Questions** list,
  then the **Solution**. Add a row per question, with its round and the user
  it is addressed to, and drag the rows into order. Give a row its answer and
  it becomes Answered, stamped with your name and the time; the status of a
  new row follows its answer. Once the row is saved, set it back to Open to
  ask again, or to Dropped when it no longer applies. The Answer column is
  hidden in the list until you show it. On a phone the list shows cards.
* *Test Cases* tab: add ordered cases with steps and expected results, then
  give each case its result with the icons of its row; a row offers the
  states other than its own (Passed, Failed, Not run, Waiting, Not
  applicable). **Verified by** and **Verified on** show who gave the last
  Passed or Failed. On a phone the tab shows cards with the same buttons.
  Set **Verification** to Automated test or Demo, and the **Automated test
  reference**, on the form of the case.
* **Notes** button: counts the notes of the task and opens them in the
  **Task Notes** list. It shows once the task has a note. On a template it
  lists the notes the **Task Notes** menu leaves out.
* *Pull Requests* tab (AI-driven projects): add the pull requests of the
  task. The list shows the link, state, merge date and deploy date of each.
  A pull request already recorded for another task is picked from the list
  instead of being created again. **PR (old value)** shows above the list
  on a task that has one.
* *Sessions* tab (AI-driven projects, project users), after *Pull
  Requests*: the agent sessions that worked on the task, the newest first,
  with their date, who worked them, the session id and the outcome; cards
  on a phone. A row saved with a session the task already lists updates
  that row instead of adding one. Editing a row leaves its date as it is.
* **Resolution** (AI-driven projects), next to the tags: choose it before
  moving the task to a stage that requires one. Code needs a pull request on
  the task; a task that already has one gets Code by itself.
* **Modules** (the project that develops this Odoo, project users), after
  the Resolution: the installed modules the task touches, as tags bearing
  their technical names. Type a technical name or a title to pick one; only
  installed modules are offered, and *Search More...* lists them by
  technical name and title, cards on a phone. A module that is not installed
  is refused, and the refusal names it. A module uninstalled after it was
  added stays on the task, which still saves with it. Search tasks by
  **Module**, with a technical name or a title.
* **Task Type** and **Estimate** (AI-driven projects): set both before
  moving the task to a stage that requires them. The refusal names what is
  missing.

An image pasted or dropped into a text of a task, of a question row or of a
test case, or into an html property of a task, becomes an attachment of
that record when it is saved, and the text shows it from there; the images
of the Description, the Solution and the html properties show among the
attachments of the task's chatter. A duplicated task and its lines get
their own copies of the images.

**Project > Checklist** lists the checklist items of the tasks you can read
in the selected companies, opened on your own TODO items. Group by project,
task, user or status. The assignee or the reviewer converts an item into a
sub-task with **Convert to Sub-task** in the header of its form, where they
may convert it from the task: the new sub-task opens in place of the item,
without breadcrumbs back to it.

**Project > Test Cases** lists the test cases of the tasks you can read,
opened on the ones Not run: a list, cards on a phone. Filter on Not run or
Failed, group by project, task, status or verification. The header of a
case's form gives it its result with the same buttons, next to its status
bar: Not run, Passed and Failed, and the status of the case when it is
another.

**Project > Pull Requests** lists all pull requests, the newest first: a
list, cards on a phone. Search by URL, repository or task, filter on Open,
Merged, Closed or No State, group by repository or state. On the form of a
pull request, set **State**, **Merge Date** and **Deploy Date** when it is
merged and deployed.

**Project > Task Notes** lists the internal notes of the tasks you can read,
archived tasks included, templates and the tasks of project templates left
out, the newest first: a list, cards on a phone. A note
of files alone is listed; a message to one user and a change of tracked
fields are not. Filter on a category, search by contents, task or author,
and group by category or by task: a task's group bears its current name.
A note opens read-only with its files, and **Open Task** opens its task.
Nothing is created, changed or deleted from this menu.

To give a note a category, log it on the task with the category in bold as
its first words, alone in the bold or followed there by a dash, a colon, a
slash, a plus or an opening parenthesis: **Design Decision** - keep the
flow; **Progress: 2/3**.

A tool that writes over RPC sets ``pr_uri`` on the task to the link of the
pull request, one link per write, or ``resolution`` to ``data``,
``configuration`` or ``no_change`` when there is none. A write of another
link adds a second pull request to the task. The estimate is written to
``allocated_hours`` and the type to ``task_type``; a ``tshirt_estimate``
sent with them is ignored. The estimate is a finite number: a NaN or an
infinity is refused, in any project.

A tool that records the modules of a task over RPC, as a project user, first
reads ``develops_this_odoo`` on the task and goes on only when it is
``True``: on a task of any other project the form hides the modules, but
its project does not stop a write of them. It finds their ids with
``search_read`` on ``ir.module.module``, the domain
``[("name", "in", <technical names>), ("state", "in", ["installed",
"to upgrade", "to remove"])]`` and the field ``name``, then writes
``module_ids`` on the task with one ``(4, <module id>)`` command per module:
the modules already on the task stay as they are. A write that adds a module
that is not installed is refused as a whole. It reads the technical names
from ``module_names``: sorted, separated by ``", "``, ``False`` when the task
has no module.

A tool that keeps a checklist over RPC creates ``project.task.checklist.item``
records with ``task_id``, ``name`` and ``user_id``, which defaults to the
tool's own user, and reads the items of a task from ``checklist_item_ids``.
Items sent with ``Command.create`` in the ``checklist_item_ids`` of a task
being created are created right after the task, with the access rights of
the calling user: a portal collaborator gets an access error. It moves an
item by writing ``state`` (``todo``, ``waiting``, ``done`` or
``cancelled``) as the item's assignee or reviewer, and turns items into
sub-tasks with ``action_convert_to_subtask``, one sub-task under the task of
each item, as the item's assignee, or as its reviewer where its user may
read the task's project or, on a task template without a project, is a
project manager (never on a private task); ``can_convert`` tells whether its
user may, in the companies of ``allowed_company_ids`` when the context has
it. It reads only the items of the tasks its user can read, and creates,
changes and deletes only those of the tasks its user can change; without
``allowed_company_ids`` in the context, those of all the user's companies.
It reaches question rows and test cases the same way.

A tool that keeps the questions of a task over RPC creates
``project.task.question`` records with ``task_id`` and ``question``, and
``round``, ``addressed_to_id`` and ``answer`` when it has them, and reads the
rows of a task from ``question_ids``. It answers a row by writing
``answer``; ``state`` takes ``open``, ``answered`` or ``dropped``. A create or
write that makes a row Answered fills ``answered_by_id`` and ``answered_on``
unless it sends them.

A tool that records an agent session on a task over RPC, when the session
closes, creates a ``project.task.session`` record with ``task_id``,
``session_id`` and ``outcome``: one ``create`` per task, without a search
first. The first call for a task and session creates the row, with
``user_id`` the tool's user and ``date`` now unless it sends them. A later
call for the same pair updates that row and returns its id: ``date``
becomes now unless the call sends one, and ``user_id`` and ``outcome`` stay
unless it sends them. A batch ``create`` returns one id per values, in
their order, the same id where a pair repeats. ``Command.create`` in the
``session_ids`` of a task updates the same way. It reads the rows of the
tasks its user can read, and creates and updates only those of the tasks
its user can change: a create on another task raises an access error. It
reads the sessions of a task from ``session_ids``.

A tool that gives test case results over RPC writes ``state`` on
``project.task.testcase``, and reads the result from it: ``not_run``,
``passed``, ``failed``, ``waiting`` or ``not_applicable``. A create or write
of ``passed`` or ``failed`` fills ``verified_by_id`` and ``verified_on``
unless it sends them. It sends ``verification_kind`` (``manual``,
``automated`` or ``demo``) and ``test_reference`` with the result: only the
buttons set ``manual`` by themselves. A create or write that sends
``not_run`` clears ``verified_by_id``, ``verified_on`` and
``verification_kind``, also the values it sends for them, and leaves
``test_reference``. To prepare a case with its kind before its run, create
it without ``state``, or write ``verification_kind`` alone on a case Not
run: the case keeps it.

A tool reads the notes of one category of a task with ``search_read`` on
``mail.message``, the domain ``[("task_id", "=", <task id>),
("note_category", "=", "design_decision")]`` and fields such as ``date``,
``author_id`` and ``body``. ``note_category`` takes ``requirement_change``,
``design_decision``, ``bug_fix``, ``review_fix``, ``user_feedback``,
``progress``, ``revised`` or ``deploy``. It reaches the messages its user
can read, and groups them under the same check. It gives a note a category
by posting it on the task with ``message_post``, ``subtype_xmlid``
``mail.mt_note`` and a body that opens with the category in bold, in a
paragraph: ``<p><b>Progress</b> - in review</p>``. A body of more than one
node that opens with an inline tag and holds no block tag, such as
``<b>Progress</b> - in review``, is stored inside a ``<span>`` and gets its
category too.

The stage log has no view and no menu. A system administrator reads
``project.task.stage.log`` over RPC: the rows come ordered by date, each
with ``task_id``, ``previous_stage_id``, ``stage_id``, ``stage_name``,
``date``, ``user_id`` and ``source`` (``live`` on every row this branch
writes). The log holds the stages of every task, also of tasks a user
cannot read: code that shows it to other users reads it with ``sudo()``,
for the tasks those users can read.

A tool that writes a text over RPC may send its images inline, as
``<img src="data:image/png;base64,..." data-filename="...">``: the create or
write makes each an attachment of the record holding the text, and a read
of the text gives the ``/web/image/<id>?access_token=<token>`` link in its
place. To show an image of another task, question row or test case in a new
record, it sends the link as it read it, access token included: the new
record gets its own copy.

A migration or an ``odoo shell`` script moves the inline images of texts
saved before 20.0.2.13.0 with
``_move_inline_images_to_attachments(dry_run=False, store=True)`` on the
tasks, a private method that RPC cannot call. It covers the texts of the
tasks and of their question rows and test cases, and the html properties of
the tasks, and returns ``records`` (how many tasks and rows have texts that
change), ``images`` (how many attachments it creates), ``bytes`` (their
size before core resizes them) and ``texts`` (``{record: {field name: new
text}}``, the new ``task_properties`` a ``{property name: value}`` dict of
all its values, to store as JSON). An image core fails to read stays inline
and counts nowhere. It holds every new text until it returns: run it over
the tasks in batches.

* ``dry_run=True`` creates and changes nothing, counts as a run would, and
  returns empty ``texts``.
* ``store=False`` creates the attachments and leaves the texts to the
  caller, which can store them by SQL to keep ``write_date`` and
  ``write_uid``; the default stores them as a save does, which updates both.
  A returned text the caller does not store keeps its inline images, and
  the next run attaches them again.

An agent or a tool reads a whole task with one read that names the brief:
``read([<task id>], ["brief"])`` on ``project.task``, or ``search_read`` with
``["brief"]`` as its fields. A read that names no fields, ``read()`` or a
``search_read`` without fields, leaves the brief out, so that it does not
compute one brief per task it returns; a read of several tasks that names
``brief`` computes one brief each. The brief holds what the tool's user may
read, and a task they cannot read raises an access error. Code on the
server reads ``task.brief`` at the cost of that task's brief alone, from a
recordset of any size.

The rows of the brief carry the ids a tool acts on:
``### Question <id> (<status>)`` for a question row,
``- <id> <state>: <description>`` for a checklist item,
``### Test case <id>: <name> (<status>)`` for a test case, and
``(attachment <id>, ...)`` in the attachments list. A pointer in a text names an ``ir.attachment``, of
the task or of the row whose text shows it:

* ``![image: <name>](attachment <id>)``, or ``![image](attachment <id>)``,
  an image;
* ``[file: <name>](attachment <id>)``, or ``[file](attachment <id>)``, a
  bare link to a file;
* ``[<link text>](attachment <id>)``, a link with a text of its own.

The tool reads the file it needs from ``ir.attachment`` by that id, under
its own rights. ``[image not yet moved to an attachment]`` stands for an
inline image of a text saved before 20.0.2.13.0. Notes older than the last
10 are read with ``search_read`` on ``mail.message``, as above. The help
text of the field is the reference for the tokens and credentials removed
from urls, and for the forms that may keep them.
