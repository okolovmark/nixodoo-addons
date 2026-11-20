Additions to project tasks for AI-driven development: task attributes,
clarifying questions as rows with their answers, the technical solution, a
checklist and test cases per task, pull requests as records, the agent
sessions that worked on a task, the project that develops this Odoo and the
installed modules its tasks touch, the internal notes of tasks by category,
and two rules on the stages a project marks: a Resolution to enter some, a
task type and an estimate to enter others. The module also logs the stage
changes of every task of the database, keeps the images of every task's
texts as attachments of the task, and gives every task a brief: the whole
task in Markdown, in one read. Projects and their tasks get keys, such as
``PROJ-12``, that name them and open them by link.

**Task attributes** (``project.task``)

* **Priority** (Highest to Lowest), **Estimate**, **Size** and **Task Type**
  (Feature, Bug, Improvement, Investigation, Milestone, Support, Technical
  Debt), next to the tags on the task form. The type is also a column of the
  task list; its tracking messages name the field **AI-driven Task Type**.
  Neither the type nor the estimate has a default value.
* **Estimate** is the core field ``planned_hours`` (*Initially Planned
  Hours*), in hours. **Size** (``tshirt_estimate``) is stored, read-only and
  follows it: XS up to 2 hours, S up to 6, M up to 12, L up to 32, XL up to
  60, XXL above; empty while the estimate is not above zero. A size sent in
  a write is dropped and one sent at creation is recomputed from the hours.
  A portal user is the exception: core refuses their create or write that
  sends a size, as any field outside the portal's writable fields. With
  Timesheets installed, its tab on the task shows the same field as
  *Allocated Hours* (*Allocated Days* where the company encodes timesheets
  in days).
* **UAT users** (``uat_user_ids``): the users who run the acceptance tests
  of the task, after the Assignees on the form of every task, picked among
  active internal users. Changes are tracked. A copy of a task, a sub-task
  copied with its parent too, starts without them.
* The core priority star is hidden on the tasks of a project flagged
  **AI-driven Project**: on the form, the kanban card, the task list, the
  *Sub-tasks* and *Blocked By* lists of the form and the calendar popover.
  Its stored value is not touched: core still orders tasks by it, and the
  filters *High Priority* and *Low Priority* still search on it. The tasks
  of other projects keep the star.
* A **Technical Solution** tab with the Questions list and the Solution,
  shown to project users on the tasks of a project flagged **AI-driven
  Project**.
* Assignment notifications honour the ``mail_auto_subscribe_no_notify``
  context, so an account without an email address (an RPC service account)
  can assign tasks without failing.

**Checklist** (``project.task.checklist.item``)

Task-level checklist items, the **Checklist** of the task
(``checklist_item_ids``), each with a description, an **Assigned to** user,
a **Reviewer**, a deadline and a state: TODO, Waiting, Done, Cancelled. A new
item's assignee and reviewer default to the user who creates it, on the form
and over RPC; the reviewer is read-only on the form. Items have no
activities.

An item is found, read, changed, deleted and created by the users who can
read its task, archived or not: the record rules of the task apply to its
items, the companies selected in the company switcher included. The
superuser and ``sudo()`` reach every item.

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
into *All checklist updates* on the task or on its project. The same context
key silences these posts for an account without an email address.

The assignee or the reviewer of an item converts it into a sub-task of its
task, whatever its state, in a project with **Sub-tasks** turned on, so never
on a task without a project. The conversion needs the right to create a task
in that project under the record rules of tasks, those other modules add
included. In Odoo 16 a project visible to *Invited internal users* refuses it
to whoever neither follows the project nor is a Project / Administrator
(group ``project.group_project_manager``), even the item's assignee, as Odoo
reads the new task before writing its assignees. The *Convert to Sub-task*
icon and button show to a user who may convert the item under these rules;
the closing and estimate rules of the first stage, below, an author
without an email address, and a rule on a field Odoo computes only once the
task is created can still refuse the conversion after the click.

The sub-task takes the item's description as its title, its assignee, and
the date of its deadline in the time zone of the user converting it, or no
deadline. Its parent is the task, its project the task's, its stage the
first unfolded stage of that project; it takes no default of the screen the
conversion starts from. The assignee and the reviewer follow it. When the
reviewer converts an item assigned to another user, that user gets Odoo's
*You have been assigned to* notice. The task gets an internal note, without
a category, linking the sub-task, and the item is deleted. The conversion
also takes the right to delete the item, and when the closing or the
estimate rule marks that first unfolded stage, the stage refuses the
sub-task, which has no Resolution, type or estimate. Any refusal leaves the
item and the task as they were, with no sub-task.

The task's kanban card shows the current user's completion percentage over
their non-cancelled items and the number of their TODO and Waiting items. The
**Checklist** menu opens on your own TODO items (the *My* and *TODO* filters
are on by default); remove those filters to see every item you can read.

**Test cases** (``project.task.testcase``)

Ordered test cases per task with Steps to execute, Expected results and
Comments. The **Status** of a case is its result: Not run (the default),
Passed, Failed, Waiting or Not applicable.

* **Verified by** and **Verified on**: who gave the last Passed or Failed,
  and when. A create or write that sets Passed or Failed, or a create that
  takes it from a default (the context's or an ``ir.default``), fills them
  with the user who makes it, also under ``sudo()``, and the current time; a
  value the same call gives is kept. Passed or Failed given again is a new
  verification. A create or write that gives Not run clears them and
  **Verification**, whatever values it sends for them; Waiting and Not
  applicable leave them. A create or write without a state keeps the values
  it gives: a case created Not run by default keeps the Verification it is
  prepared with.
* **Verification**: Manual, Automated test or Demo. A result button other
  than Not run sets it to Manual when it is empty.
* **Automated test reference**: the automated test that verifies the case,
  such as a test selector. Not run leaves it.

The task carries general comments and a completion percentage: its Passed
cases over all its cases but the Not applicable ones. Its kanban card counts
the cases Not run, Failed and Waiting. The **Test Cases** menu opens
filtered on the cases Not run; remove the *Not run* filter to see them all.
Copying a task also copies its checklist items, keeping their states, and
its test cases, keeping their status, Verified by, Verified on,
Verification and Automated test reference.

The next task of a recurrence, and each of its sub-tasks, starts the
checklist items and the test cases of the task it repeats from zero: an item
TODO without deadline, keeping its description, assignee and reviewer; a
case Not run without Verified by, Verified on, Verification and comments,
keeping its steps, expected results and Automated test reference. The task
it repeats keeps its lines. The next task is created as a new task, so its
items are posted to their assignee and reviewer.

A case is found, read, changed, deleted and created by the users who can
read its task, as a checklist item is, and moves only onto a task they can
read, so the images of its texts reach only such a task.

The completion percentages and the card summaries of the checklist and the
test cases (``completion``, ``completion_xml``, ``kanban_checklist``,
``testcase_completion``, ``testcase_completion_xml``, ``kanban_testcases``)
are for **Project / User** only, as the lines they count: the task cards of
another user show neither, and a read of the task leaves them out.

**Questions** (``project.task.question``)

The clarifying questions of a task, its **Questions** (``question_ids``),
are rows in an order of their own:

* **Round**: the round of questions the row was asked in, a name or a date.
* **Question** and **Answer**: rich text.
* **Addressed to**: the internal user the question is for.
* **Status**: Open, Answered or Dropped. A row is Open until it gets an
  answer that shows something: text, an image, a video or an icon. A create
  or a write that gives such an answer to an Open row makes it Answered,
  unless the same call sets the status; a Dropped row stays Dropped. An answer of spaces, empty paragraphs or empty
  list items leaves the row Open.
* **Answered by** and **Answered on**: who made the row Answered, and when. A
  create or write that makes a row Answered fills them with the user who
  makes it, also under ``sudo()``, and the current time; a value the same
  call gives is kept. A row already Answered keeps them when it is set
  Answered again or gets another answer. Open clears them and keeps the
  answer; Dropped leaves them.

A row is named by the text of its question, cut at 80 characters, or of its
answer when the question is blank, or else by its task. A row is found,
read, changed, deleted and created by the users who can read its task, as a
checklist item is. Copying a task copies its rows with all their values,
the status, Answered by and Answered on included. The next task of a
recurrence takes no row.

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
session. A row is found, read, changed, deleted and created by the users
who can read its task, as a checklist item is. A create, and a write of the
task, first checks that the user can read the task it names, so a create on
a task the user cannot read is refused whether or not that task lists the
session. The **Sessions** tab, after **Pull Requests**, shows the rows to
project users on the tasks of an AI-driven project, as cards on a phone.
Copying a task copies no session.

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
* **State**: Open, Merged or Closed. A new record is Open; one created by
  the conversion described under *Installation* has no state.
* **Merge Date**, **Deploy Date** and the **Tasks** it is linked to.

A pull request link is an http(s) URL with a repository path followed by
``/pull/N``, ``/merge_requests/N`` (also ``/-/merge_requests/N``),
``/pull-requests/N`` or ``/pulls/N``, N being a number of at most nine
digits.

Internal users read pull requests; **Project / User** also creates and
edits them. No access right allows deleting one, and portal users have no
access.

**Pull requests and Resolution on a task** (``project.task``)

* **Pull Requests** (``pull_request_ids``): the pull requests of the task,
  on a tab of their own on the tasks of an AI-driven project.
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
* **PR (old value)** (``pr_legacy``), read-only: the whole text ``pr_uri``
  held before the conversion described under *Installation*, where that text
  was anything but one plain pull request link.
* A duplicated task starts without pull requests, Resolution and old value.
  A copy that keeps the stage of its source, as every task and subtask of a
  duplicated project does, takes the Resolution and the pull requests of its
  source.

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
write of its stage or its project (``stage_id``, ``project_id``, or
``display_project_id``, the project of a sub-task) that leaves it in another
stage or another project. A move to another project counts also when the
task keeps its stage, as Odoo keeps it when the new project has it too. The
Resolution and the pull request may come in the same write as the stage. Any
other stage, a project that is not flagged AI-driven and a write that leaves
the stage and the project as they are let everything through, so a task
already sitting in a marked stage stays editable. No context key a caller
sends skips the rule.

A change saved for the following or for all tasks of a recurrence is
checked on each task it moves, and one refusal refuses the whole change.
The tasks it reaches are those of the recurrence as it is before the save,
also when the same save turns Recurrent off: the following ones are those
created at or after the task saved, and a task without a recurrence is
saved alone. Odoo itself looked for them after removing the recurrence,
and saved the change to the later tasks of every project, or to none. New
assignees are notified on the task saved only, as in Odoo; **Assigning Date**
of each task reached is set or cleared as Odoo's write of several tasks
sets or clears it, where Odoo did so on the task saved only.

The next task of a recurrence, created by Odoo's recurrence job, enters the
first stage of its project, or the stage of the task it repeats when the
project lists none, unchecked by either rule in either stage, its sub-tasks
included: one refusal would undo the run of the job for every due
recurrence. It takes no Resolution and no pull request of the task it
repeats, and it is checked as any task when it is moved into a marked stage
later.

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
rule. A stage marked for both rules refuses for the Resolution first. A
change saved for the following or for all tasks of a recurrence, and the
creation of the next task of a recurrence, are handled as by the closing
rule (above).

The kanban quick create of an AI-driven project shows **Task Type** and
**Estimate** after the assignees: required in the column of a marked stage,
optional in the others. It shows neither on another project or without a
project. The web client takes an Estimate left at zero as filled, so the
rule refuses it, and Odoo's kanban then opens the full form of a new task
with the title typed. A column whose stage also requires a Resolution still
refuses the quick create, which has no Resolution.

The next task of a recurrence takes the task type of the last one, as it
takes its estimate.

**The project that develops this Odoo** (``project.project``)

* **Develops This Odoo** (``develops_this_odoo``) marks the AI-driven
  project whose tasks change the Odoo database the module runs in. One
  project of the database carries it at most, archived projects included: a
  second one is refused, and the refusal names the project that has it, also
  to a user who cannot read that project. Ticking it on a project that is
  not AI-driven, or clearing **AI-driven Project** on the project that has
  it, is refused.
* Only a Settings administrator (**Administration / Settings**) sets or
  clears it, at creation (a context default included) or in a write; a
  write that leaves it as it is passes for any user. Only a Settings
  administrator archives or deletes the project that carries it.
* The project form shows it to Settings administrators, below **AI-driven
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
  starts without modules.
* A create or a write refuses a module it adds that is not installed;
  *To be upgraded* and *To be removed* count as installed, *To be
  installed* does not. The refusal names the modules, and the whole call is
  refused. A module already on a task stays there when it is uninstalled:
  the task saves with it, and a write that keeps it or links it again to
  that task passes.
* **Module Names** (``module_names``), not stored and on no view: the
  technical names of the modules, sorted, separated by ``", "``; ``False``
  without modules.
* The task search has a **Module** field: it finds tasks by a module's
  technical name or title.
* Both fields, and the **Module** search, are for **Project / User** only.
  Project users read the module list, by an access right of this module.
* Under the context key ``module_technical_name`` a module is named by its
  technical name instead of its title. The picker passes it, with a list
  and cards of the technical name and the title, without buttons; the Apps
  menu keeps the views of ``base``.

**The stage log** (``project.task.stage.log``)

One row when a task is created with a stage and one row on every change of
its stored stage, for every task of the database, in an AI-driven project
or not. The rows are written by SQL where the row of the task is written,
so no context (``tracking_disable``, ``mail_notrack``,
``mail_create_nolog``) and no ``sudo()`` goes around the log, and a stage
recomputed by a change of project is logged too, on the task and on the
subtasks moved with it.

* **Task**, **Date** and **User**: the task, when its stage changed and who
  changed it. The user is empty when it is not known. Deleting a task
  deletes its rows.
* **Stage** and **Stage Name**: the stage the task entered. The stage is
  empty when the task left its stage, and when the stage was deleted since;
  a deleted stage keeps its name.
* **Previous Stage**: the stage the task left. It is empty when there was
  none, when it is not known and when it was deleted since.
* **Source**: *Recorded* for a row written with the change; *From the
  chatter*, *From the last stage update* or *From the creation date* for a
  row of the past, filled as described under *Installation*.

A task created without a stage gets no row. A task that loses its stage
gets a row with an empty stage. A write that carries the stage the task
already has writes no row.

Only system administrators read the log, and no access right allows
creating, changing or deleting a row. The log has no view and no menu. A
search of the log first flushes the stage changes that are pending.

**Task notes** (``mail.message``)

* **Task** (``task_id``): the task a message is posted on, empty for a
  message of another model and for one whose task no longer exists. It is
  stored, set from the model and the record of the message, and emptied
  when the task is deleted.
* **Category** (``note_category``): Requirement Change, Design Decision,
  Bug Fix, Review Fix, User Feedback, Progress, Revised or Deploy, given to
  an internal note of a task whose body opens with it in bold. It is stored,
  and read again when the body, the record or the subtype of the message
  changes, as when a note is edited.
* ``is_task_readable``, not stored: whether the current user, in the
  companies they selected, can read the task of the message, archived tasks
  included. It is searched as SQL, with ``=`` or ``!=`` and a boolean
  (``True`` / ``False`` or ``1`` / ``0``), so that a page of a list holds only
  messages of readable tasks.

The category of a note:

* An internal note is a message of the *Note* subtype, of any message type.
  A message to the followers, a message on another model and a message that
  tracks changed fields get no category, whatever their body.
* The body opens with the bold text (``<b>`` or ``<strong>``): only white
  space, ``&nbsp;`` and at most one opening tag, ``<p>`` or ``<span>``, with
  or without attributes, come before it.
* The bold text opens with the category, in any letter case, and ends there
  or goes on after a dash, a colon, a slash, a plus or an opening
  parenthesis: ``<b>Bug Fix</b>: the total``, ``<b>Progress: 2/2</b>``,
  ``<b>Bug Fix (round 2)</b>``. Bold text naming two categories gets the
  first. ``<b>Progress report</b>`` and ``<b>Deploy notes</b>`` get none.

Odoo lets only administrators group messages. A project user also groups
them on a domain that filters on ``is_task_readable``, as the **Task Notes**
list does: the groups and their counts hold only messages of tasks the user
can read, and a group opens on the user's own domain with the condition of
the group. The group-by fields are category, task, author, date, model,
message type and subtype; any other group-by, and any other grouped read of
messages, stays refused to users who are not administrators.

**Images in task texts** (``project.task.image.mixin``)

The images of a task's texts are attachments of the task, on every task of
the database, in an AI-driven project or not. The texts are the stored Html
fields, neither computed nor translated, of the task (**Description**,
**Solution**, the general comments of its test cases), of its question rows
(**Question**, **Answer**) and of its test cases (**Steps to execute**,
**Expected results**, **Comments**), those other modules add to these models
included.

* A create or a write that saves a text turns each base64 inline image
  (``data:image/...;base64,...``), in any letter case and with an
  entity-encoded scheme too, into an attachment of the task, and its url
  into ``/web/image/<id>?access_token=<token>``, as core mail does with the
  images of a message. An image is read in the ``src`` of an ``<img>``, in
  each url of a ``srcset`` on ``<img>`` and ``<source>`` (its descriptors,
  such as ``1x`` or ``480w``, stay), and in each CSS ``url(...)``, quoted or
  not, of the ``style`` of any tag. Only the url changes: the rest of the
  text stays as stored. An HTML comment is not read and stays as written,
  an image in it included. The image of a question row or a test case goes
  to the row's task.
* The attachment takes the name in the image's ``data-filename``,
  ``data-file-name`` or ``alt``, the first that is not blank, or else
  ``image-<n>.<type>``, numbered per task within the save.
* An image the task already has is not attached again: pasted in any text of
  the task, of its question rows or of its test cases, in the same save or a
  later one, it links the attachment of the task with the same contents and
  type, as core stores them (a large image compares resized), and gives it
  an access token when it has none. It never links one posted with a
  message, which deleting the message deletes, one whose contents read as
  empty from the storage of attachments (a file gone from the filestore),
  nor one the saving user may not read by their own rights, also in a save
  that runs under ``sudo()``: a new attachment then holds the image.
  Another task never shares an attachment.
* The attachment is created under ``sudo()``, with the user who saves as its
  creator, once the save has passed its own access checks: a save the user
  may not make creates none. The new text is stored without a second write:
  no other chatter message, and no write logic of another module runs again.
* Core reads the image first (``ir.attachment._check_contents``), as for
  every attachment upload, and resizes it as described under
  *Configuration*. An image it cannot read (markup, an image too large to
  open, a truncated file) stays inline and the save passes. A lack of
  memory, the CPU time limit of a worker and a database error fail the save.
* The images show among the attachments of the task's chatter. Deleting the
  task deletes them.

A task, a question row or a test case created with a text that shows an
image of another task's attachment (``/web/image/<id>``) or links one of its
files (an ``<a href>`` to ``/web/content/<id>``, as the editor links an
uploaded file), as a duplicated task, the next task of a recurrence or a
create from another task's text does, gets its own copy of that attachment,
with its own access token, or the attachment its task has with the same
contents, and its text links it. A file link keeps its path, query and fragment, with
the new id and access token; the file is copied as uploaded, without
core's resize. A question row or a test case moved to another task copies
the images and files of its texts the same way. A copy outlives the task it
was made from.

* A link is one of this database when it is relative, or names the host of
  the task as the brief reads it (``get_base_url()``, a website's domain
  where it applies), in any letter case, by either scheme or none
  (``//<host>/...``), on the same port, the default port of a scheme
  counting as none. A link to another host or port stays as it is.
* The id is read as the editor writes it, at most ten ASCII digits; a link
  written otherwise stays as it is.
* Only an attachment the user saving may see is copied: one they can read,
  or one whose link carries the attachment's access token. Under
  ``sudo()``, the superuser included, only a link with the attachment's own
  access token is copied. A token that is not ASCII matches nothing.
* An attachment whose contents read as empty (a file gone from the
  filestore) is not copied: its link stays as it is.
* Any other link stays as it is, and so does a link to another task's
  image or file that a write pastes.

Texts saved before 16.0.2.13.0 keep their inline images until the text is
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
  the task type, the estimate and its size, the priority, the planned
  window, the deadline, the hours spent, the assignees, the UAT users, the
  author and the creation date, the Resolution, the modules on a task of the
  project that develops this Odoo, the tags, and the link to the task form.
  The planned window, the hours spent and the UAT users show where a module
  declares their fields (``planned_date_start`` and ``planned_date_end``,
  ``effective_hours``, ``uat_user_ids``).
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
* The last 10 internal notes with a body, oldest first, each with its date,
  author and category. Nothing else is cut.
* The attachments of the task, each with its name, id, type and size.

Times are in UTC. The texts become Markdown (headings, emphasis, links,
lists, tables, code) with no markup left; the headings of a text sit below
the section that holds it. An item of an editor's checklist is a task list
item, ``- [x]`` when ticked and ``- [ ]`` when not, a nested checklist
nested items; the box stands on a line of its own when the item opens with
a heading, a list, a table, a rule, a code block or a quote. Another list
item that starts like one has its bracket escaped. A list right after a
list of its kind at the same level, nested ones included, takes the other
marker, ``+`` for ``-`` or ``1)`` for ``1.`` and back, so that it stays a
list of its own. A table cell spans at most 100 columns, and its rows at
most to the end of its row group (its ``thead``, ``tbody`` or ``tfoot``),
the end ``rowspan="0"`` spans to; a list number has at most nine digits,
however long the number the text gives.

* An image or a link that names an attachment, by a relative url or a url on
  the host of this database (``get_base_url()``, a website's domain where it
  applies), in any url form, its id written as the editor writes it (at most
  ten ASCII digits), becomes a pointer:
  ``![image: <name>](attachment <id>)`` for an image,
  ``[file: <name>](attachment <id>)`` for a bare link and
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
* A link of a scheme other than http(s) or mailto keeps only its text.
* User text that would open a Markdown heading, list, quote, code fence (of
  three backticks or more), table row or HTML block at the start of a line is
  escaped, a table delimiter row too, and names are put on one line, so a text
  cannot open a section of the brief.

A reader who cannot read the task is refused, as a read of it is. What the
reader may not read is left out: the lines (question rows, checklist items,
test cases, sessions, pull requests), the related tasks, the notes and the
attachments under their access rights and record rules, the companies not
selected in their context included; and the fields their groups keep from
them, such as the question rows, checklist, test cases, sessions and
modules for a user outside **Project / User**. A many-to-one value (the
project, the stage, the author) shows its name as a read of the task does.

**Task keys** (``project.project``, ``project.task``)

Every project has a **Key** (``key``, up to 10 characters), and every task
of a project a key of its own (``key``, up to 20 characters) such as
``PROJ-12``: the key of its project and the next number of the project's
**Key Sequence** (``task_key_sequence_id``). Both keys are unique. A task's
display name is its key before the name it has otherwise, so the
breadcrumb, lists, cards and references show it, and a name search finds
projects and tasks by key. The task's ``url`` is the link to its form.

* A new project gets the initials of its name in upper case (the first
  three letters of a one-word name), followed by the lowest counter that
  makes it free; archived projects count. The words are split at
  whitespace and keep only their letters and digits, so the key holds
  nothing else; a name without any gives no key. The key with its counter
  is cut to 10 characters. The project form proposes the key from the name
  until one is set.
* A task gets the next key of its project when it is created, a copy
  included; one created or copied with no project gets none. Moved to
  another project, by its own project, its parent or the project it shows
  in, it gets a key of the new one, and so do the sub-tasks that move with
  it, archived ones and those the user cannot read included, and the other
  tasks of its recurrence that the same edit moves; taken out of its
  project, or moved to a project that gives no key, they lose theirs.
* A new project key renames the keys of the project's tasks that end in a
  hyphen and a number, each keeping that number, and the prefix of its
  sequence; other task keys stay. A cleared key clears the keys of all its
  tasks; the project gives none until it has a key again, and its sequence
  then goes on from its next number. The tasks created in it or moved into
  it meanwhile stay without a key.
* A copied project gets a sequence of its own; a deleted project takes its
  sequence along.
* ``/projects/<key>`` and ``/tasks/<key>`` open the form of the project or
  task of that key, whatever its case, among those the user can read; any
  other key opens an empty form. ``%`` and ``_`` in a key are no wildcards.
* In a run with ``test_enable`` keys are off, unless the context sets
  ``test_project_key``: the tests of other modules compare display names
  without keys.

The fields, constraints and sequences are those of OCA ``project_key``,
under the same names and columns, and this module takes its place: a
database that ran it keeps every key and the next number of every
sequence. While ``project_key`` is loaded it gives the keys as before; this
module gives them once it is uninstalled. Uninstall ``project_key`` only
once this module is updated to 16.0.2.17.0, in the same run at the
earliest: uninstalled before, it drops the key columns with every key. What
the install and that update do is under *Installation* (*Coming from
project_key*).

**Former names**

``project.task.subtask`` is the former name of the checklist model, and
``subtask_ids`` and ``kanban_subtasks`` those of the task fields
``checklist_item_ids`` and ``kanban_checklist``. What the update to
16.0.2.6.0 renames is under *Installation*.

``questions`` and ``answers``, two Html fields of the task, are the former
form of the question rows; this module does not declare them. What the
update to 16.0.2.7.0 makes of them is under *Installation*.
