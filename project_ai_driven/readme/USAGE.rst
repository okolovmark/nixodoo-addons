On the task form:

* Set **Priority**, **Estimate** (in hours) and **Task Type** next to the
  tags. **Size** shows the size of the estimate and cannot be edited.
* *Checklist* tab: add items, each with an **Assigned to** user, yourself
  unless you pick another, and an optional deadline. You are the reviewer of
  the items you add. The assignee or the reviewer moves an item with the
  state icons (TODO, Waiting, Done, Cancelled), changes its description or
  gives it another assignee. The task's kanban card shows
  your own completion percentage, only when you have non-cancelled items
  assigned to you.
* *Test Cases* tab: add ordered cases with steps and expected results, then
  give each case its result with the icons of its row; a row offers the
  states other than its own (Passed, Failed, Not run, Waiting, Not
  applicable). **Verified by** and **Verified on** show who gave the last
  Passed or Failed. On a phone the tab shows cards with the same buttons.
  Set **Verification** to Automated test or Demo, and the **Automated test
  reference**, on the form of the case.
* *Technical Solution* tab (AI-driven projects): the **Questions** list,
  then the **Solution**. Add a row per question, with its round and the user
  it is addressed to, and drag the rows into order. Give a row its answer and
  it becomes Answered, stamped with your name and the time; the status of a
  new row follows its answer. Once the row is saved, set it back to Open to
  ask again, or to Dropped when it no longer applies. The Answer column is
  hidden in the list until you show it. On a phone the list shows cards.
* *Pull Requests* tab (AI-driven projects): add the pull requests of the
  task, each with its link, state, merge date and deploy date. A pull
  request already recorded for another task is picked from the list instead
  of being created again. **PR (old value)** shows above the list on a task
  that has one.
* **Resolution** (AI-driven projects), next to the tags: choose it before
  moving the task to a stage that requires one. Code needs a pull request on
  the task; a task that already has one gets Code by itself.
* **Task Type** and **Estimate** (AI-driven projects): set both before
  moving the task to a stage that requires them. The refusal names what is
  missing.

**Project > Checklist** lists the checklist items of the tasks you can read
in the selected companies, opened on your own TODO items. Group by project,
task, user or status.

**Project > Test Cases** lists the test cases of all tasks, opened on the
ones Not run: a list, cards on a phone. Filter on Not run or Failed, group
by project, task, status or verification. The header of a case's form
gives it its result with the same buttons, next to its status bar: Not run,
Passed and Failed, and the status of the case when it is another.

**Project > Pull Requests** lists the pull requests of all tasks, the newest
first: a list, cards on a phone. Search by URL, repository or task, filter
on Open, Merged, Closed or No State, group by repository or state. On the
form of a pull request, set **State**, **Merge Date** and **Deploy Date**
when it is merged and deployed.

**Project > Task Notes** lists the internal notes of the tasks you can read,
archived tasks included, the newest first: a list, cards on a phone. A note
of files alone is listed; a message to one user and a change of tracked
fields without a note are not. Filter on a category, search by contents,
task or author, and group by category or by task: a task's group bears its
current name, and the categories come in the order of the filters, the notes
without one last. A note opens read-only with its files, and **Open Task**
opens its task. Nothing is created, changed or deleted from this menu.

On the task form, the **Notes** button counts the notes of the task and
opens them in the same list. It shows once the task has a note.

To give a note a category, log it on the task with the category in bold as
its first words, alone in the bold or followed there by a dash, a colon, a
slash, a plus or an opening parenthesis: **Design Decision** - keep the
flow; **Progress: 2/3**.

A tool that writes over RPC sets ``pr_uri`` on the task to the link of the
pull request, one link per write, or ``resolution`` to ``data``,
``configuration`` or ``no_change`` when there is none. A write of another
link adds a second pull request to the task. The estimate is written to
``planned_hours`` and the type to ``task_type``; a ``tshirt_estimate`` sent
with them is ignored. The estimate is a finite number: a NaN or an infinity
is refused, in any project.

A tool that keeps a checklist over RPC creates ``project.task.checklist.item``
records with ``task_id``, ``name`` and ``user_id``, which defaults to the
tool's own user, and reads the items of a task from ``checklist_item_ids``.
It moves an item by writing ``state`` (``todo``, ``waiting``, ``done`` or
``cancelled``) as the item's assignee or reviewer. It reaches only the items
of the tasks its user can read; without ``allowed_company_ids`` in the
context, those of all the user's companies.

A tool that keeps the questions of a task over RPC creates
``project.task.question`` records with ``task_id`` and ``question``, and
``round``, ``addressed_to_id`` and ``answer`` when it has them, and reads the
rows of a task from ``question_ids``. It answers a row by writing
``answer``; ``state`` takes ``open``, ``answered`` or ``dropped``. A create or
write that makes a row Answered fills ``answered_by_id`` and ``answered_on``
unless it sends them. It reaches only the rows of the tasks its user can
read.

A tool that gives test case results over RPC writes ``state`` on
``project.task.testcase``: ``not_run``, ``passed``, ``failed``, ``waiting``
or ``not_applicable``. A create or write of ``passed`` or ``failed`` fills
``verified_by_id`` and ``verified_on`` unless it sends them. It sends
``verification_kind`` (``manual``, ``automated`` or ``demo``) and
``test_reference`` with the result: only the buttons set ``manual`` by
themselves. The former values ``todo``, ``done`` and ``cancelled`` are taken
as ``not_run``, ``passed`` and ``not_applicable``. ``passed`` is read, not
written: a value sent for it is dropped, and ``passed`` true without a
``state`` gives ``passed``.

A tool reads the notes of one category of a task with ``search_read`` on
``mail.message``, the domain ``[("task_id", "=", <task id>),
("note_category", "=", "design_decision")]`` and fields such as ``date``,
``author_id`` and ``body``. ``note_category`` takes ``requirement_change``,
``design_decision``, ``bug_fix``, ``review_fix``, ``user_feedback``,
``progress``, ``revised`` or ``deploy``. It reaches the messages its user
can read. It gives a note a category by posting it on the task with
``message_post``, ``subtype_xmlid`` ``mail.mt_note`` and a body that opens
with the category in bold, in a paragraph:
``<p><b>Progress</b> - in review</p>``. A body of inline content only and
more than one node, such as ``<b>Progress</b> - in review``, is stored
inside a ``<span>`` and gets its category too. A grouped read
(``read_group``) of messages takes a project user, ``("is_task_readable",
"=", True)`` in the domain and group-bys among ``note_category``,
``task_id``, ``author_id``, ``date``, ``model``, ``message_type`` and
``subtype_id``.

The stage log has no view and no menu. A system administrator reads
``project.task.stage.log`` over RPC: the rows come ordered by date, each
with ``task_id``, ``previous_stage_id``, ``stage_id``, ``stage_name``,
``date``, ``user_id`` and ``source`` (``live``, ``tracking``, ``stage_date``
or ``creation``). The log holds the stages of every task, so code that
shows it to other users reads it with ``sudo()``, for the tasks those users
can read.
