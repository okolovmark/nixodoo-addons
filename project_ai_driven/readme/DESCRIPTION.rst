Additions to project tasks for AI-driven development: task attributes,
clarifying questions as rows with their answers, the technical solution, a
checklist and test cases per task, pull requests as records, the agent
sessions that worked on a task, the project that develops this Odoo and the
installed modules its tasks touch, the internal notes of tasks by category,
and two rules on the stages a project marks: a Resolution to enter some, a
task type and an estimate to enter others. The module also logs the stage
changes of every task of the database, keeps the images of the texts of
every task, question row and test case as attachments of the record holding
the text, and gives every task a brief: the whole task in Markdown, in one
read. Projects and their tasks get keys, such as ``PROJ-12``, that name them
and open them by link.

**Task attributes** (``project.task``)

* **Priority** (Highest to Lowest), **Estimate**, **Size** and **Task Type**
  (Feature, Bug, Improvement, Investigation, Milestone, Support, Technical
  Debt), next to the tags on the task form. The type is also a column of the
  task list; its tracking messages name the field **AI-driven Task Type**.
  Neither the type nor the estimate has a default value.
* **Estimate** is the core field ``allocated_hours`` (*Allocated Time*), in
  hours. **Size** (``tshirt_estimate``) is stored, read-only and follows it:
  XS up to 2 hours, S up to 6, M up to 12, L up to 32, XL up to 60, XXL
  above; empty while the estimate is not above zero. A size sent in a write
  is dropped and one sent at creation is recomputed from the hours; neither
  call is refused, but for a portal user, whose create or write sending a
  size core refuses, as for any field outside the portal's writable fields.
  The type and the estimate are among those fields on the tasks of an
  AI-driven project: a collaborator of the shared project with Edit or
  Advanced Edit writes them there, one who may only read does not. On the
  tasks of any other project the portal writes neither, as in core, and
  its views show both read-only. Nor does a portal user's write take a
  task that has either out of an AI-driven project into one that is not,
  as a new parent in such a project can do for a sub-task and the
  sub-tasks under it: the write is refused, naming the tasks the user may
  read and counting the others. With Task Logs (``hr_timesheet``)
  installed, timesheet users also see the same field as *Allocated Time*
  on the tasks of a project that uses timesheets, and an estimate given in
  the same save wins over the hours Task Logs reads from a title such as
  ``Fix 2h``.
* **UAT users** (``uat_user_ids``): the users who run the acceptance tests
  of the task, after the Assignees on the form of every task, picked as the
  Assignees are among the active internal users of the task's company; an
  archived one stays shown. Changes are tracked. No copy takes them: a
  Duplicate, a task created from a template and the tasks of a duplicated
  project start without UAT users.
* The core priority star is hidden on the tasks of a project flagged
  **AI-driven Project**: on the form, its *Sub-tasks* and *Blocked By*
  lists, the kanban card, the task list and the calendar popover. Its stored
  value is not touched: core still orders tasks by it, and the *Priority*
  filters of the search still search on it. The tasks of other projects keep
  the star, also in the same list. The task list of a flagged project opened
  from the project has no *Priority* column at all.
* A **Technical Solution** tab with the Questions list and the Solution,
  shown to project users on the tasks of a project flagged **AI-driven
  Project**.

**Checklist** (``project.task.checklist.item``)

Task-level checklist items, the **Checklist** of the task
(``checklist_item_ids``), each with a description, an **Assigned to** user,
a **Reviewer**, a deadline and a state: TODO, Waiting, Done, Cancelled. A new
item's assignee and reviewer default to the user who creates it, on the form
and over RPC; the reviewer is read-only on the form. Items have no
activities.

An item is found and read by the users who can read its task, and created,
changed and deleted by the users who can change the task, archived or not.
Its two access rights carry the domains ``[('task_id', 'access', 'read')]``
and ``[('task_id', 'access', 'write')]``, so the record rules of the task
apply to its items, the companies selected in the company switcher included.
Moving an item to another task also takes write access to that task. The
superuser and ``sudo()`` reach every item. Reading, changing or deleting an
item deleted meanwhile raises ``MissingError``, as core does.

Only the assignee or the reviewer of an item changes its state, description
or assignee. The check runs before the write, against the assignee and the
reviewer the item has then: the assignee may hand the item over and change
its state in one write, another user cannot take it. The deadline, and the
reviewer over RPC, are changed by anyone who can change the item.

A creation and a change of state, description or assignee make one post per
item on the task, addressed to the item's assignee and reviewer, and to its
former assignee on a change of assignee, other than the user making the
change, under the internal *All checklist updates* subtype. When no one else
is left, the post is a note addressed to no one. A change of description
shows the former description under the former state. A post is written in
the language of the user making the change. The subtype is not subscribed
by default: other followers receive the posts of others only after opting
into *All checklist updates* on the task or on its project. The
``mail_auto_subscribe_no_notify`` context key silences these posts, the way
it silences the assignment notification of a task in Odoo itself: an
integration writes tasks and checklist items with it and nobody is notified.

The items a new task is created with, as ``Command.create`` in its
``checklist_item_ids`` (a sub-task created from its parent included), are
created right after the task, in their order, as items added to a saved
task: their posts come after the creation message of the task, and the
followers of *All checklist updates* on its project receive them. They are
created with the access rights of the caller and without the ``default_*``
keys of the context, which are the task's.

The task's kanban card shows the current user's completion percentage over
their non-cancelled items and the number of their TODO and Waiting items.
Only users with the **User** access of *Project* read these figures, the
task fields ``completion``, ``completion_xml`` and ``kanban_checklist``,
and see them on the card; an internal user without that access reads every
other field of the task. These figures, the buttons of an item and the red
of one's own TODO items are computed for the user who reads them, also
where two users read the same task in one transaction. The **Checklist**
menu opens on your own TODO items (the *My* and *TODO* filters are on by
default); remove those filters to see every item you can read.

An item that turns out to be work of its own is converted into a sub-task of
its task (``action_convert_to_subtask``):

* Only its assignee or its reviewer converts it, as for a change of its
  state, and only where they may change the task: deleting the item takes
  it. Its reviewer also needs to read the task's project, or to be a
  project manager: Odoo 20 lets a project user create a task only in a
  project they may read or as one of its assignees, and the sub-task goes
  to the item's assignee. Following the task does not count: the sub-task
  gets its followers, its creator and those of the task, only once it is
  created. So a reviewer assigned to a task of a project open to its team
  members only, without being one of them, changes the task but does not
  convert its items. The *Convert to Sub-task* buttons show only to who may
  convert under these conditions, those of templates and private tasks
  below included, computed for the user and for the companies selected in
  the company switcher: an item of a task of a company left out of the
  selection shows none.
* The sub-task takes the item's description as its title, its assignee and
  its deadline, and the project of the task, so also the company of that
  project when the user has several selected. It is given no stage: core
  puts it in the first unfolded stage of the project by sequence. No default
  of the calling view reaches it.
* The item's reviewer follows the sub-task, next to the followers core gives
  it. Core notifies the assignee of the sub-task unless the assignee
  converts the item; ``mail_auto_subscribe_no_notify`` silences that, as for
  any task.
* An internal note on the task, by the user converting, links the sub-task.
  It opens with plain text, so it gets no category, and names the item by
  its description as text: markup in it shows as written. The item is
  deleted.
* An item of a task template becomes a template sub-task: in the template's
  project and its first stage, or without project and stage, then only by
  its assignee or by a project manager who is its reviewer. An item of a
  private task is refused, since Odoo 20 gives a private task no sub-task.
* A refusal, of the user, of a private task, of a task the user may not
  change, of a reviewer who may not create the sub-task or of a first stage
  that refuses the new sub-task under the closing rule or the estimate rule,
  leaves the item as it was, without sub-task or note.

**Test cases** (``project.task.testcase``)

Ordered test cases per task with Steps to execute, Expected results and
Comments. The **Status** of a case is its result: Not run (the default),
Passed, Failed, Waiting or Not applicable.

* **Verified by** and **Verified on**: who gave the last Passed or Failed,
  and when. A create or write that sets Passed or Failed fills them with the
  user who makes it, also under ``sudo()``, and the current time; a value
  the same call gives is kept. A case created Passed or Failed by a
  default, of the context or of ``ir.default``, is filled the same way.
  Passed or Failed given again is a new verification. A create or write
  that gives Not run clears them and **Verification**, also a value the
  same call gives; Waiting and Not applicable leave them.
* **Verification**: Manual, Automated test or Demo. A result button other
  than Not run sets it to Manual when it is empty. A case prepared for its
  run keeps the Verification it is given: a case created without a Status,
  or a Not run case given a Verification alone.
* **Automated test reference**: the automated test that verifies the case,
  such as a test selector. Not run leaves it.

A case follows the access of its task as a checklist item does, through two
access rights with the same domains. The task carries general comments and
a completion percentage: its Passed cases over all its cases but the Not
applicable ones. Its kanban card shows it as *Test cases: N% Done*, the
same figure for every reader, as a case has no assignee, and counts the
cases Not run, Failed and Waiting. As for the checklist, only users with
the **User** access of *Project* read the percentage and the counts, the
task fields
``testcase_completion``, ``testcase_completion_xml`` and
``kanban_testcases``, and see them on the card. The **Test Cases** menu
opens filtered on the cases Not run; remove the *Not run* filter to see all
those you can read.

**Questions** (``project.task.question``)

The clarifying questions of a task, its **Questions** (``question_ids``),
are rows in an order of their own:

* **Round**: the round of questions the row was asked in, a name or a date.
* **Question** and **Answer**: rich text.
* **Addressed to**: the internal user the question is for.
* **Status**: Open, Answered or Dropped. A row is Open until it gets an
  answer that shows something: text, an image, a video or an icon. A create
  or a write that gives such an answer to an Open row makes it Answered,
  unless the same call sets the status; a Dropped row stays Dropped. An
  answer of spaces, empty paragraphs or empty list items leaves the row
  Open.
* **Answered by** and **Answered on**: who made the row Answered, and when. A
  create or write that makes a row Answered fills them with the user who
  makes it, also under ``sudo()``, and the current time; a value the same
  call gives is kept. A row already Answered keeps them when it is set
  Answered again or gets another answer. Open clears them and keeps the
  answer; Dropped leaves them.

A row is named by the text of its question, cut at 80 characters, or of its
answer when the question is blank, or else by its task. A row follows the
access of its task as a checklist item does, through two access rights with
the same domains.

**Sessions** (``project.task.session``)

The agent sessions that worked on a task, its **Sessions**
(``session_ids``), are rows listed the newest first, one per task and
session:

* **Session** (``session_id``): the id of the agent session. No link is
  stored; where the Entire CLI records sessions, ``entire session info
  <id>`` opens it.
* **Date**: when the session last wrote its row.
* **Worked by** (``user_id``): an internal user, by default the one who
  creates the row.
* **Outcome**: what the session did, in one line.

A task lists a session once. A create that names a task and a session that
already have a row writes the values it gives onto that row and returns it
in its place: the date becomes now unless the create gives one, and what it
does not give, the user and the outcome included, stays. A batch that names
a pair twice keeps the later values. The task and the session are read from
the values or their defaults, so a session added to the task's
``session_ids``, on the task form or with ``Command.create``, updates its
row too. A write changes only what it names: editing a row leaves its date
alone. A write that moves a row onto a task and session that have a row is
refused.

A row is named by its outcome, cut at 80 characters, or else by its
session. A row follows the access of its task as a checklist item does,
through two access rights with the same domains: it is read with the task,
and created, changed and deleted with write access to it, also when a
create updates a row. A create, and a write of the task, first checks that
the user can read the task it names, so a create on a task the user cannot
read is refused whether or not that task lists the session. The
**Sessions** tab, after **Pull Requests**, shows the rows to project users
on the tasks of an AI-driven project, as cards on a phone. Copying a task
copies no session.

**The lines of a copy**

A copy of a task takes the checklist items, test cases and question rows of
its source, and its sub-tasks take theirs, unless the values of the copy
give its own. What a line keeps depends on the copy:

* A plain duplicate of a task, a project or a template of either keeps every
  line as it is: an item with its state and deadline, a case with its
  result, Verified by, Verified on, Verification and Comments, a row with
  its answer, status, Answered by and Answered on. A case keeps them under
  any default Status the copy is made with, of the context or of
  ``ir.default``, and a Not run case keeps its Verification.
* A task or a project created from a template, and a project template made
  from a project, start the lines from zero: items TODO without deadline,
  cases Not run without Verified by, Verified on and comments, rows Open
  without answer, Answered by and Answered on. An item keeps its
  description, assignee and reviewer, a case its steps, expected results
  and Automated test reference, a row its round, question and addressee. A
  case prepared Not run keeps its Verification too, which says how it is
  to be verified; a case with a result loses the Verification of that
  result.
* The next occurrence of a recurring task, and each of its sub-tasks,
  starts the items and the cases from zero, as a task created from a
  template does, a prepared case keeping its Verification, and takes no
  question row. The task it repeats keeps its lines. The occurrence is
  created as a new task, so its items are posted to their assignee and
  reviewer.

A task converted to a template is no copy: it keeps its lines.

**Pull requests** (``project.pull.request``)

A pull request is a record of its own, shared by every task it belongs to:

* **URL**, required and unique, stored in canonical form: scheme and host in
  lower case, then the path as written, up to and including the number.
  ``/files``, a query, a fragment or a closing slash after the number are
  dropped.
* **Repository** and **Number**, read from the URL. The record is named
  ``<repository>#<number>``, for example ``owner/repo#12``, and is found by
  that name or by a part of its URL. A search by that name takes negative
  operators too, and an ``in`` or ``not in`` list may mix such names with
  URLs.
* **State**: Open, Merged or Closed. A new record is Open.
* **Merge Date**, **Deploy Date** and the **Tasks** it is linked to.

A pull request link is an http(s) URL with a repository path followed by
``/pull/N``, ``/merge_requests/N`` (also ``/-/merge_requests/N``),
``/pull-requests/N`` or ``/pulls/N``, N being a number of at most nine
digits.

Internal users read pull requests; users with the **User** access of
*Project* also create and edit them. No access right allows deleting one,
and portal users have no access.

**Pull requests and Resolution on a task** (``project.task``)

* **Pull Requests** (``pull_request_ids``): the pull requests of the task,
  on a tab of their own on the tasks of an AI-driven project. Every pull
  request added to the task or removed from it is tracked in the chatter.
* **Resolution** (``resolution``): how the task was closed: Code, Data,
  Configuration or No change. It is shown on the tasks of an AI-driven
  project, and its changes are tracked in the chatter.
* **First Pull Request** (``pr_uri``) reads the link of the task's first
  pull request, the one created first, and is empty when the task has none.
  It is on no view of this module. It is stored, so it can be searched, its
  changes are tracked in the chatter, and it is writable for the tools that
  write it over RPC. Writing one pull request link finds the record with
  that link or creates it, and adds it to the task; a link the task already
  has changes nothing, and neither does an empty value. Anything that is not
  exactly one pull request link (other text, a link without ``http(s)://``,
  a commit or branch link, two links) is refused: a task closed without a
  pull request takes the Resolution Data, Configuration or No change
  instead.
* **PR (old value)** (``pr_legacy``), read-only: text that a database
  upgraded from 16.0 holds there. Nothing on this branch writes it.
* A copy that keeps the stage of its source takes the Resolution and the
  pull requests of its source, unless the copy is given its own. That is a
  plain Duplicate, and every task of a duplicated project. A copy sent to
  another stage and the copy of a task without a stage take nothing. The
  next occurrence of a recurring task takes nothing either, in any stage: it
  is new work. The old value is never copied.
* A task created from a template, a task template or the tasks of a project
  created from a project template, takes neither, whatever stage it keeps,
  and meets the closing rule as a new task: in a marked stage it is refused.
  A project template with a task sitting in a marked stage cannot be used
  until that task is moved. A plain Duplicate still takes both.
* A project template made from a project keeps no Resolution and no pull
  request of its tasks; the tasks of the project keep theirs. A task
  converted to a template keeps its own.

**The closing rule**

On an AI-driven project, **Stages Requiring a Resolution**
(``pr_required_states``) marks stages. A task of that project enters a
marked stage only with a Resolution:

==============================  ============  ===============================
Resolution                      Pull request  Result
==============================  ============  ===============================
empty                           none          refused: choose a Resolution
empty                           present       let through, Resolution = Code
Code                            none          refused: add a pull request
Code                            present       let through
Data, Configuration, No change  none          let through
Data, Configuration, No change  present       let through, pull request kept
==============================  ============  ===============================

The rule is checked when the task enters a stage: on creation, and on a
write of its stage or its project (``stage_id`` or ``project_id``, also the
project of a sub-task) that leaves it in another stage or another project.
A move to another project counts also when the task keeps its stage, as
Odoo keeps it when the new project has it too. A sub-task whose project and
stage follow its parent, because the parent moves to another project or the
sub-task gets another parent, is not checked, also when the save sends the
new parent's project, and the stage Odoo gives the sub-task there, with
it. The Resolution and the pull request may come in the same write as the
stage. Any other stage, a project that is not flagged AI-driven and a
write that leaves the stage and the project as they are let everything
through, so a task already sitting in a marked stage stays editable. The
``state`` of a task (Done, Cancelled) is not part of the rule: a task set
Done outside the marked stages needs no Resolution. No context key a caller
sends skips the rule.

Templates are outside the rule: a task template, its sub-tasks and the
tasks of a project template enter a marked stage without a Resolution. A
task of a project template moved to an AI-driven project is no template
any more and meets the rule, also where it keeps its stage.

The next occurrence of a recurring task, created by Odoo when the task it
repeats is set Done or Cancelled, enters the first stage of its project, or
the stage of that task when the project lists none, unchecked by either rule,
its sub-tasks included: a refusal would undo the change of state with it. The
task set Done or Cancelled is checked as any task, and so is the occurrence
when it is moved into a marked stage later.

**The estimate rule**

On an AI-driven project, **Stages Requiring a Type and an Estimate**
(``estimate_required_states``) marks stages. A task of that project enters a
marked stage only with a **Task Type** and an **Estimate** above zero hours.
The refusal names what is missing, the type, the estimate or both, each with
the tasks that lack it.

The rule is checked where the closing rule is: on creation, and on a write
of the stage or the project that leaves the task in another stage or
another project, also one that keeps its stage. The type and the estimate
may come in the same write as the stage, and a task created straight into a
marked stage needs both in the values of its creation. Any other stage, a
project that is not flagged AI-driven and a write that leaves the stage and
the project as they are let everything through, so a task already sitting
in a marked stage stays editable. No context key a caller sends skips the
rule. A stage marked for both rules refuses for the Resolution first.
Templates are outside this rule too, and so is the creation of the next
occurrence of a recurring task (above).

The kanban quick create of an AI-driven project shows **Task Type** and
**Estimate** after the assignees: required in the column of a marked stage,
optional in the others and on templates. The quick create of *My Tasks*,
which asks for the project, shows them once an AI-driven project is picked,
required where its first stage is marked. It shows neither on another
project or without a project. The web client of Odoo 20 does not hold back
an Estimate left at zero, so the rule refuses it, and the kanban then opens
the form of a new task with the title typed. A column whose stage also
requires a Resolution still refuses the quick create, which has no
Resolution. The quick create of a shared project's kanban, which Odoo
offers to a collaborator with Advanced Edit, asks for them the same way.
A refusal there shows the dialog Odoo shows for its kind of error, such as
a redirect with its button or an expired session, and another server
error shows a portal user its message alone, without technical details;
the quick create stays open with what was typed.

The next occurrence of a recurring task is a copy of the task, so it
carries its task type and its estimate.

**The project that develops this Odoo** (``project.project``)

* **Develops This Odoo** (``develops_this_odoo``) marks the AI-driven
  project whose tasks change the Odoo database the module runs in. One
  project of the database carries it at most, archived projects included: a
  second one is refused, and the refusal names the project that has it, also
  to a user who cannot read that project. Setting it on a project that is
  not AI-driven, or clearing **AI-driven Project** on the project that has
  it, is refused.
* Only a system administrator (``base.group_system``) sets or clears it, at
  creation (a default of the context included) or in a write; a write that
  leaves it as it is passes for any user. Only a system administrator
  archives or deletes the project that carries it.
* The project form shows it to system administrators, below **AI-driven
  Project**, on a project that is AI-driven or carries it. A change is
  tracked in the chatter of the project. A duplicated project starts without
  it.
* The task has the same field, related to its project, read-only and
  readable by every user who reads the task.

**Modules of a task** (``project.task``)

* **Modules** (``module_ids``): the installed modules the task touches,
  records of the module list (``ir.module.module``). On the tasks of the
  project that develops this Odoo the form shows them after the Resolution,
  as tags bearing their technical names, and offers only installed modules.
  On the tasks of other projects the form hides them; they are kept, and the
  project does not stop a create or a write of them. A duplicated task
  starts without modules. A change of the modules is not tracked: Odoo 20
  writes tracked values into the text of the chatter message, which every
  reader of the task reads, whatever the groups of the field.
* A create or a write refuses a module it adds that is not installed;
  *To be upgraded* and *To be removed* count as installed, *To be
  installed* does not. The refusal names the modules, and the whole call is
  refused, also for a module given as a default of the context. A module
  already on a task stays there when it is uninstalled: the task saves with
  it, and a write that keeps it or links it again to that task passes.
* **Module Names** (``module_names``), not stored and on no view: the
  technical names of the modules, sorted, separated by ``", "``; ``False``
  without modules.
* The task search has a **Module** field: it finds tasks by a module's
  technical name or title.
* Both fields, and the **Module** search, are for users with the **User**
  access of *Project* only. They read the module list by an access right of
  this module.
* Under the context key ``module_technical_name`` a module is named by its
  technical name instead of its title. The picker passes it, with a list
  and cards of the technical name and the title, without buttons; the Apps
  menu keeps the views of ``base``.

**The stage log** (``project.task.stage.log``)

One row when a task is created with a stage and one row on every change of
its stored stage, for every task of the database, in an AI-driven project
or not. The rows are written by SQL where the row of the task is written, in
``_create`` and ``_write_multi`` of ``project.task``. No context
(``tracking_disable``, ``mail_notrack``, ``mail_create_nolog``) and no
``sudo()`` goes around the log, and a stage recomputed by a change of
project is logged too, on the task and on the subtasks moved with it.

* **Task**, **Date** and **User**: the task, when its stage changed and who
  changed it. Deleting a task deletes its rows.
* **Stage** and **Stage Name**: the stage the task entered. The stage is
  empty when the task left its stage, and when the stage was deleted since;
  a deleted stage keeps its name.
* **Previous Stage**: the stage the task left. It is empty when there was
  none and when it was deleted since.
* **Source**: *Recorded* on every row this branch writes. *From the
  chatter*, *From the last stage update* and *From the creation date* are
  on the rows of a database upgraded from 16.0.

A task created without a stage gets no row. A task that loses its stage
gets a row with an empty stage. A write that carries the stage the task
already has writes no row. A task that existed before the module was
installed has no row until its stage changes; that first row names the
stage it left as the previous one.

Only system administrators (``base.group_system``) read the log, and no
access right allows creating, changing or deleting a row. The log has no
view and no menu. A search of the log first flushes the stage changes that
are pending.

**Task notes** (``mail.message``)

* **Task** (``task_id``): the task a message is posted on, empty for a
  message of another model and for one whose task no longer exists. It is
  stored, set from the model and the record of the message, and emptied
  when the task is deleted.
* **Category** (``note_category``): Requirement Change, Design Decision,
  Bug Fix, Review Fix, User Feedback, Progress, Revised or Deploy, given to
  an internal note of a task whose body opens with it in bold. It is stored,
  and read again when the body, the model, the subtype or the message type
  of the message changes, as when a note is edited.

Both are computed when a message is created, and for every existing message
when the module adds their columns, on install or on the update that brings
them.

The category of a note:

* An internal note is a message of the *Note* subtype on a task, of any
  message type but *Tracking*. A message to the followers, a message on
  another model and a tracking message get no category, whatever their body.
* The body opens with the bold text (``<b>`` or ``<strong>``): only white
  space, ``&nbsp;`` and at most one opening tag, ``<p>`` or ``<span>``, with
  or without attributes, come before it.
* The bold text opens with the category, in any letter case, and ends there
  or goes on after a dash, a colon, a slash, a plus or an opening
  parenthesis: ``<b>Bug Fix</b>: the total``, ``<b>Progress: 2/2</b>``,
  ``<b>Bug Fix (round 2)</b>``. Bold text naming two categories gets the
  first. ``<b>Progress report</b>`` and ``<b>Deploy notes</b>`` get none.

**Project > Task Notes** lists the internal notes of tasks: a message to one
user, a tracking message and a message with neither a body nor a file are
left out, and so are the notes of tasks the user cannot read, which Odoo
would show to their author or recipients. The notes of task templates, of
their sub-tasks and of the tasks of project templates are left out too, as
core leaves those tasks out of its task lists; the **Notes** button of such
a task counts and lists its own. The domain names the model
``project.task``, so Odoo checks the access of the tasks in SQL. Odoo 20
lets any user group messages under that check: the groups and their counts
hold only notes of tasks the user can read. Groups by category come in the
order of the categories, the notes without one last.

**Images in task texts** (``project.task.image.mixin``)

The images of the texts of a task, a question row or a test case are
attachments of that record, on every task of the database, in an AI-driven
project or not. The texts are the stored Html fields, neither computed nor
translated, of the task (**Description**, **Solution**, the general comments
of its test cases), of its question rows (**Question**, **Answer**) and of
its test cases (**Steps to execute**, **Expected results**, **Comments**),
those other modules add to these models included, and the html properties
of a task's **Properties** (``task_properties``), the defaults its project
fills in included, on create and on every write of the task's project or
parent, which also refills the properties of the tasks below it, its other
properties left as they are. The editor of the form already uploads an
image added in an Html field when the record is saved; the module does the
same for every other save.

* A create or a write that saves a text turns each base64 inline image
  (``data:image/...;base64,...``), in any letter case and with an
  entity-encoded scheme too, into an attachment of the record, and its url
  into ``/web/image/<id>?access_token=<token>``, as the editor and core mail
  do. An image is read in the ``src`` of an ``<img>``, in each url of a
  ``srcset`` on ``<img>`` and ``<source>`` (its descriptors, such as ``1x``
  or ``480w``, stay), and in each CSS ``url(...)``, quoted or not, of the
  ``style`` of any tag. Only the url changes: the rest of the text stays as
  stored, and the field sanitizes the new text as on any save. An image or a
  link inside an HTML comment stays as it is. The image of a question row
  or a test case is an attachment of the row.
* The attachment takes the name in the image's ``data-filename``,
  ``data-file-name`` or ``alt``, the first that is not blank, or else
  ``image-<n>.<type>``, numbered per record within the save.
* An image the record already has is not attached again: pasted in any text
  of the record, in the same save or a later one, it links the attachment
  of the record with the same contents and type, as core stores them (a
  large image compares resized), and gives it an access token when it has
  none. It never links one posted with a message, which deleting the
  message deletes, nor one whose file is missing from the filestore, nor
  one the saving user may not read by their own rights, also in a save
  that runs under ``sudo()``: a new attachment then holds the image.
  Another record never shares an attachment, a row and its task included.
* The attachment is created under ``sudo()``, with the user who saves as its
  creator, once the save has passed its own access checks: a save the user
  may not make creates none. The new text is stored without a second write:
  no other chatter message, and no write logic of another module runs again.
* Core reads the image first (``ir.attachment._check_contents``), as for
  every attachment upload, and resizes it as described under
  *Configuration*, once. An image it fails to read (an image too large to
  open) stays inline and the save passes. A lack of memory, the CPU time
  limit of a worker and a database error fail the save. An SVG moved by a
  user who may not edit views is stored as text, as core stores such an
  upload, and no longer displays.
* The images of a task's Description, Solution and html properties show
  among the attachments of the task's chatter. Deleting a task, a row or a
  test case deletes its images; a task deletes its rows with theirs.

A task, a question row or a test case created with a text that shows an
image of an attachment of another task, row or test case
(``/web/image/<id>``) or links one of its files (an ``<a href>`` to
``/web/content/<id>``, as the editor links an uploaded file), as a
duplicated task and its copied lines, a task created from a template, the
next task of a recurrence or a create from another record's text do, gets
its own copy of that attachment, with its own access token, or the
attachment it has with the same contents, and its text links it. A file
link keeps its path, query and fragment, with the new id and access token;
the file is copied as uploaded, without core's resize. The editor's file
box around such a link (``o_file_box``) names the new attachment in its
``data-attachment-id`` too. A question row or a test case moved to another
task copies the images and files of other records in its texts the same
way; its own stay its own. A copy outlives the record it was made from.
An image the editor has cropped or converted names the attachment its next
edit starts from in ``data-original-id`` and ``data-original-src``: when
that original is an attachment of the same record as the image and may be
copied, the new record copies it too and the two attributes name the copy;
otherwise they are dropped, with ``data-mimetype-before-conversion``.

* A link is one of this database when it is relative, or names the host of
  the base url the brief reads the task with (``get_base_url()``, a
  website's domain where it applies), in any letter case, by either scheme
  or none (``//<host>/...``), on the same port, the default port of a
  scheme counting as none. A link to another host or port stays as it is.
* The id is read as the editor writes it, at most ten ASCII digits; a link
  written otherwise stays as it is.
* Only an attachment the user saving may see is copied: one they can read,
  or one whose link carries the attachment's access token, the first
  parameter whose name decodes to ``access_token``, as the server reads it.
  Under ``sudo()``, the superuser included, only a link with the
  attachment's own access token is copied. A token that is not ASCII
  matches nothing.
* Any other link stays as it is, and so does a link to another record's
  image or file that a write pastes, or to an image core fails to read or
  whose file is missing from the filestore.

Texts saved before 20.0.2.13.0 keep their inline images until the text is
saved again or ``_move_inline_images_to_attachments`` runs on its task (see
*Usage*).

**Task brief** (``project.task``)

**Brief** (``brief``) is the whole task as Markdown text, for an agent or a
tool that reads tasks over RPC. It is computed for the user who reads it,
in the language and the companies of their context, and not stored. It is
on no view, cannot be exported, and is left out of a read that names no
fields; a read that names ``brief`` gets it. Its help text is the reference
for the forms of urls it rewrites.

A brief holds, in this order, each part only when it has something to show:

* The header: the task's key (*Task keys* below), or ``#<id>`` for a task
  without one, and its title; then the project, the stage and since when,
  the status (``state``), the task type, the estimate and its size, the
  priority, the planned start, the deadline, the hours spent, the
  assignees, the UAT users, the author and the creation date, the
  Resolution, the modules on a task of the project that develops this Odoo,
  the tags, and the link to the task form (``/odoo/project.task/<id>``).
  The planned start and the hours spent show where a module declares their
  fields (``planned_date_begin``, ``effective_hours``).
* Related tasks: the parent, the sub-tasks, the tasks it is blocked by and
  those it blocks, each with its key, title and stage.
* Pull requests, with their state, merge date and deploy date.
* The Description.
* The question rows, each with its id, status, round, addressee, who
  answered and when, its question and its answer.
* The Solution.
* The checklist, its progress counting the Done items over the items not
  cancelled; each item with its id, state, assignee, reviewer and deadline.
* The test cases: the general comments, then each case in its order with
  its id, status, verification (who, when, how), automated test reference,
  steps, expected results and comments.
* The sessions, the newest first.
* The last 10 internal notes with a body, of those the task's **Notes**
  button lists, oldest first, each with its date, author and category.
  Nothing else is cut.
* The attachments of the task, each with its name, id, type and size. The
  images of a question row or a test case are attachments of the row: they
  show as pointers in its texts, not in this list.

Times are in UTC, the deadline included. The texts become Markdown
(headings, emphasis, links, lists, tables, code) with no markup left; the
headings of a text sit below the section that holds it. Lists:

* An item right inside an editor's bullet checklist, other than an editor's
  nested item (class ``oe-nested``), is a task list item, ``- [x]`` when
  ticked and ``- [ ]`` when not, a nested checklist nested items; the box
  stands on a line of its own when the item opens with a heading, a list,
  a table, a rule, a code block or a quote. A ``[x]`` typed in the text of
  such an item stays as typed: the brief is read as raw text. An item in a
  block inside the checklist has no box, as the editor draws none, and an
  ordered list of the checklist class keeps its numbers, with no box.
  Another list item that starts like one has its bracket escaped.
* A list right after a list of its kind takes the other marker (``+`` after
  ``-``, ``)`` after ``.``), so that Markdown keeps the two apart: the
  marker of the list before is that of its own items, not of a list nested
  in its first one. So does the first item of an ordered list right under
  nested items that end in an ordered list of its marker, so that each list
  keeps the numbers a browser shows.
* A list that opens with nested items, two levels deep or more, stays a
  list, not code, at the start of a text, after a paragraph, under an item,
  as an item's first block or in a block between a list's parts. Right
  after a list, those nested items are written at its level, not under the
  last item of the list before.
* An item that holds nothing but lists keeps its marker, as in a browser
  (``2. - x``). An editor's nested item, or a block other than a quote
  right inside a list that holds nothing but lists, has none: its lists are
  nested items. Such a quote stays a quote between the list's parts; an
  editor's nested item right after it is then a list of its own.
* An editor's nested item that holds text has no marker and no number
  either, as the editor shows it as a block: its text goes on under the
  item before it in its part of the list, on a new line of that item's
  last paragraph where both are paragraphs, else as a block apart
  (``1. alpha``, ``bravo`` under it, ``2. charlie``). With no item before
  it in its part, it stands between the list's parts. An empty one takes
  no number, nor does a block that holds no items but editor's nested ones.
* Text right inside a list, and a block right inside it that is no item (a
  paragraph, a heading, a ``<div>``, an ``<hr>``), stand between its parts,
  unnumbered, as in a browser; an empty block is left out. The list goes on
  after them as at the start of a text, or as after a list where they end
  in one. A block that holds an item of the list, outside the lists nested
  in it, writes that item as one right in the list, and the rest of what it
  holds between the list's parts; a quote or a table that holds one, other
  than an editor's nested item, is an item. A rule that opens a bullet item
  is written ``- * * *`` (``- ---`` is a rule of its own).
* An item outside any list, as the HTML parser leaves the items after a
  code block right inside a bullet list, is a bullet item, as a browser
  shows it, an editor's nested one as in a list; a run of them with no
  text between them is one list.
* In a list, an ordered list not numbered from 1 or opening with an empty
  item stands a blank line under a paragraph above it, one that ends nested
  items included, which would otherwise read it as its text.
* An ordered item has the number a browser shows, across the parts of its
  list too: the ``value`` of an item sets its number and the items after it
  go on from it; a ``reversed`` list counts down from its ``start``, else
  from the number of its items; an editor's nested item is not counted,
  and its ``value`` is not read. CommonMark numbers a list on from its first
  item, so an item whose number does not follow the one above opens a list
  of the other delimiter, ``)`` for ``.`` and back.
* An empty ordered item is written as its number alone, as a browser shows
  it, where an item, nested lines or the text of an editor's nested item
  follow it in its part of the list (``2.`` between ``1. a`` and ``3. b``).
  An empty bullet item is written only right before the text of an
  editor's nested item, which goes under its marker; in a checklist it
  keeps its box, and a paragraph goes on the box's line. Text right under
  the marker of an empty item is escaped as the start of an item's text: a
  typed ``[x]`` stays text there. Any other empty item is left out.
* A ``start`` or a ``value`` is read as a browser reads an integer (``3px``
  gives 3); a number below 0 is written 0, as CommonMark writes no lower
  one. A list number has at most nine digits, however long the number the
  text gives, as in Markdown, where a line numbered with ten or more is no
  list item.

A table is read as a browser builds it. A row, a cell or a caption inside
another block of the table, a list too, is written in the table only, and
the rest of that block before it; a row inside a cell or a caption is a
row of the table, not part of their text. A cell outside a row stands in a
row the browser implies, up to the next row, row group or caption. The
captions, wherever they stand outside the cells, are written before the
table; a caption ends the row and the row group it stands in. A table
inside a table, outside its cells and captions, ends that table: it and
what follows it are no part of it. A cell spans at most 100 columns, and
rows at most to the end of its row group, to that end with
``rowspan="0"``: the ``thead``, ``tbody`` or ``tfoot`` nearest around its
row, else the run of rows outside them. A span is read as a browser reads
it (``2px`` spans 2).

* An image or a link that names an attachment, of the task, of a row or of
  any other record, by a relative url or a url on the host of this database
  (``get_base_url()``, a website's domain where it applies), in any url
  form, its id written as the editor writes it (at most ten ASCII digits),
  becomes a pointer: ``![image: <name>](attachment <id>)`` for an
  image, ``[file: <name>](attachment <id>)`` for a bare link and
  ``[<link text>](attachment <id>)`` for a link with a text of its own;
  ``![image](attachment <id>)`` and ``[file](attachment <id>)`` without a
  name.
* An inline image the save has not moved to an attachment shows as
  ``[image not yet moved to an attachment]``: its data never enters the
  brief.
* Tokens and credentials are removed from urls: a url loses its user and
  password, its query and fragment parameters named like a token, and the
  ``hash`` and ``pid`` that sign a portal link. That holds for the url of a
  link or an image, and on a best-effort basis for a url that stands as a
  word in a text or in code. A token written loose, outside a url, stays.
* A link of a scheme other than http(s) or mailto keeps only its text. A
  link with no text to show (none, or only a ``script``, ``style``,
  ``template`` or ``noscript`` element) is written bare, as its url or its
  attachment's pointer, a word apart from the words around it.
* In the url of a link or an image, a space, a parenthesis, ``<``, ``>`` and
  a backslash are percent-encoded, a backslash as ``%5C``.
* Inline code is written on one line: a line break in it is a space, and so
  is the break on each side of a block in it. Code, inline or preformatted,
  leaves out what a ``script``, ``style``, ``template`` or ``noscript``
  element holds, as the rest of a text does.
* User text that would open a Markdown heading, list, quote, code fence (of
  three backticks or more), table row or HTML block at the start of a line is
  escaped, a table delimiter row too, and names are put on one line, so a text
  cannot open a section of the brief. So is a ``[label]:`` at the start of a
  paragraph, its label over more lines too, which Markdown would read as a
  link reference definition and show nothing of; and a label that opens a
  paragraph and is still open at its end, where the text of an editor's
  nested item going on after it could close it. A table's caption is written
  on one line, and a ``[label]:`` at its start is escaped too, after the
  quote and list markers and the box the caption opens with as well.
* A ``!`` right before a link is escaped (``\![text](url)``), so that the
  link does not read as an image, unless an odd run of backslashes right
  before it escapes it already; the backslashes typed before it count across
  inline elements. A backslash typed in a text stays as written, the brief
  being read raw, but for one that ends a line of a paragraph with another
  line of it after: it gets a space after it, as CommonMark would read it as
  a line break and show no backslash. In the text of a link or an image a
  backslash is escaped, as a bracket is.

A reader who cannot read the task is refused, as a read of it is. What the
reader may not read is left out: the lines (question rows, checklist items,
test cases, sessions, pull requests), the related tasks, the parent
included, the notes and the attachments under their access rights, the
companies not selected in their context included; and the fields their
groups keep from them, such as the question rows, checklist, test cases,
sessions and modules for a user outside **Project / User**. A many-to-one
value of the header (the project, the stage, the author) shows its name as a
read of the task does. Archived sub-tasks, blocking and blocked tasks and
assignees are left out, also under a context with ``active_test`` false; an
archived parent or user in a many-to-one value (such as Author, Verified by
or Addressed to) still shows.

**Task keys** (``project.project``, ``project.task``)

Every project has a **Key** (``key``, up to 10 characters), and every task
of a project a key of its own (``key``, up to 20 characters) such as
``PROJ-12``: the key of its project and the next number of the project's
**Key Sequence** (``task_key_sequence_id``). Both keys are unique. A task's
display name is its key before the name it has otherwise, so the
breadcrumb, lists, cards and references show it, a portal user's included,
and a name search finds projects and tasks by key, as does the search of
their lists. A portal user reads a task's key and finds tasks by it, as
everyone does. The task's ``url`` is the link to its form in the web
client, ``/odoo/action-project.action_view_task/<id>``.

* A new project gets the initials of its name in upper case (the first
  three letters of a one-word name), followed by the lowest counter that
  makes it free; archived projects count. Only letters and digits make the
  key: a word of none gives no initial, and a name of none gives no key.
  A key made of many words is cut to the 10 characters with its counter.
  The project form proposes the key from the name until one is set.
* A task gets the next key of its project when it is created; a task
  created with no project gets none, whatever project the context proposes
  by default. Moved to another project, it gets a key of the new one, and
  so do the sub-tasks that move with it, archived ones and those the user
  cannot read included; moved to a project that gives no key, or taken out
  of its project, they lose theirs. A sub-task given a parent in another
  project moves into that project and gets a key of it. A sub-task in a
  project of its own stays there with its key. Written with its own
  project, a task keeps its key, and a task without one gets the next key
  of that project; its sub-tasks, which do not move, are left as they are.
* Keys may skip numbers: a task draws its key as it is created, from a
  standard sequence that a refused or failed create does not take back. A
  create refused by the closing rule, the estimate rule or an Estimate that
  is no finite number names its tasks by their names alone, without the key
  they drew.
* A copied task gets the next key of its own project, and none without a
  project: the copy keeps no key of its source. So does the next occurrence
  of a recurring task, and a task created from a template.
* A new project key renames the keys of the project's tasks and the prefix
  of its sequence. Each task keeps its number, the digits after the last
  hyphen of its key, whatever hyphens the former project key held. A task
  key that does not end in a hyphen and digits, one given by hand or by an
  import, has no number to keep and stays as it is. A cleared key clears
  the keys of its tasks, those included; the project gives none until it
  has a key again, and its sequence then goes on from its next number. The
  tasks created in it or moved into it meanwhile stay without a key until
  one is written with its project.
* A copied project gets a sequence of its own; a deleted project takes its
  sequence along.
* ``/projects/<key>`` and ``/tasks/<key>`` open the form of the project or
  task of that key, whatever its case, among those the user can read; any
  other key opens the form of a new task or project. ``%`` and ``_`` in a
  key are no wildcards. The form is the web client's: a portal user's
  ``/tasks/<key>`` finds the task but leads to the portal home (``/my``),
  where Odoo sends a user outside the web client, not to the task.
* In a run with ``test_enable`` keys are off, unless the context sets
  ``test_project_key``: the tests of other modules compare display names
  without keys.

The fields, constraints and sequences carry the names and columns of the
16.0 branch, so a database upgraded from it keeps every key and the next
number of every sequence. What the install and the update to 20.0.2.17.0
give a database that lacks keys is under *Installation*.

**Coming from 16.0**

The module's models, fields and selection values carry the names of the
16.0 branch, so a caller written for one branch works on the other, but for
``is_task_readable`` (below). What differs on 20.0:

* The install hook gives the keys a database lacks and nothing else (see
  *Installation*). The 16.0 branch's also takes over the records of the
  module's former name, turns old ``pr_uri`` text into pull request
  records, fills the stage log with the past of the tasks, sets the size of
  every task from its hours, sets hours that are NaN or infinite to zero,
  converts the test case states, renames the checklist model with its table
  and external ids, turns the Html ``questions`` and ``answers`` into
  question rows and fills the task and the category of existing messages by
  SQL. Of these, this branch converts only the test case states, by SQL in
  the migration of 20.0.2.15.0: a task that got such hours under an earlier
  20.0 version keeps them, and the constraint on the estimate refuses its
  copies and its next occurrence until they are corrected. The ORM computes
  the task and the category of existing messages when the module adds their
  columns.
* Task keys do not take the place of OCA ``project_key``: this branch gives
  the keys whatever else is installed, and a database that ran
  ``project_key`` on Odoo 20 is not provided for. The task's ``url`` and the
  ``/projects/<key>`` and ``/tasks/<key>`` redirects lead to
  ``/odoo/action-<action>/<id>`` URLs; an unknown key opens the form of a
  new record, where 16.0 opens a form of no record. The task form shows the
  key before the title at all times; on 16.0 outside edit mode only.
* Checklist items, question rows, session rows and test cases follow the
  access of their task through the domains of their access rights, with the
  ``access`` operator of Odoo 20: reading follows the read access of the
  task, a change its write access. On 16.0 items, question rows, session
  rows and test cases follow the read access of their task for every
  operation, through two ORM overrides in ``project.task.access.mixin``,
  which this branch does not have. On 16.0 an item deleted meanwhile passes
  the access check.
* This branch has neither ``is_task_readable`` nor the grouped read of
  messages that 16.0 opens to project users on a domain naming it: Odoo 20
  groups messages for any user. A client that names ``is_task_readable``
  fails here.
* A message that tracks changed fields is of type *Tracking* on Odoo 20,
  with the changes in its body after any note logged with them. It has no
  category and is not in **Task Notes**.
* A task opens on Description; Checklist, Technical Solution, Test Cases,
  Pull Requests and Sessions follow it. On 16.0 Checklist is the first tab.
* **AI-driven Project**, **Develops This Odoo**, **Stages Requiring a
  Resolution** and **Stages Requiring a Type and an Estimate** are settings
  under *Tasks Management* on the project form.
* A plain Duplicate keeps the stage of its source on Odoo 20, so it takes
  the Resolution and the pull requests as described above. On 16.0 only the
  tasks of a duplicated project keep their stage.
* A task has a ``state`` next to its stage on Odoo 20. Neither rule reads
  it.
* Odoo 20 tracks many2many fields: every pull request added to a task or
  removed from it is in the chatter. On 16.0 only ``pr_uri`` is tracked. A
  change of the modules leaves no trace on either branch.
* Odoo 20 creates a task from a template, which takes no Resolution and no
  pull request and starts its lines from zero, as described above. Odoo 16
  has no templates.
* The rows of the stage log are written in ``_create`` and
  ``_write_multi``, on 16.0 in ``_create`` and ``_write``.
* The estimate is the task's ``allocated_hours`` (*Allocated Time*), the
  name ``planned_hours`` has in Odoo 20.
* Odoo 20 itself skips the assignment notification under
  ``mail_auto_subscribe_no_notify``; on 16.0 the module adds that. Odoo 20
  does not refuse a message from an account without an email address:
  without the key such an account's writes go through and the notification
  is queued without a sender address.
* The image of a question row or a test case is an attachment of the row,
  as the Odoo 20 editor attaches it, and a link to an image or a file of
  any task, row or test case is copied. An image is used again by the
  record that has it. On 16.0 every image is an attachment of the task,
  used again by the task and its rows together, and only a link to another
  task's image or file is copied.
* The rules run on a write of ``stage_id`` or ``project_id``: in Odoo 20
  ``project_id`` is also the project of a sub-task, and
  ``display_in_project`` only shows the sub-task in it. On 16.0 a write of
  ``display_project_id`` counts too.
* Odoo 20 stores markup given as an image and a truncated TIFF as they come,
  so they become attachments; on 16.0 they stay inline.
* The brief's header shows the task's status (``state``), which Odoo 16 does
  not have, a deadline with its time, the planned start where a module
  declares ``planned_date_begin`` (on 16.0 the planned window of
  ``planned_date_start`` and ``planned_date_end``), and links the task as
  ``/odoo/project.task/<id>``.
