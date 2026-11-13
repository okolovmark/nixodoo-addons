# Pull requests are records, under the field names of project_task_pull_request

Status: accepted (2026-11-13)

A task of an AI-driven project used to carry its pull request in one free-text field, `pr_uri` of OCA `project_task_pull_request`, which had to be filled before the stages a project marks. One field cannot hold several pull requests, says nothing of their state, and a task closed without one needed filler text to pass. From 16.0.2.1.0 a pull request is a record of `project.pull.request` (its link, the repository and number read from it, a state, a merge date and a deploy date), linked many-to-many to tasks, and a task closes with a Resolution: Code, Data, Configuration or No change.

**The old names stay.** `pr_uri` on the task and `pr_required_states` on the project are declared here under the names, the column and the table of the OCA module. `pr_uri` reads the link of the task's first pull request. Writing one pull request link to it finds or creates the record and adds it to the task; any other text is refused. Every caller that reads or writes `pr_uri` keeps working, the tracking history stays on the same field row, the stages each project marked stay marked, and the OCA module can be uninstalled without losing the column or the table: an uninstall keeps what another installed module also declares.

Rejected: new field names with a copy migration. Every caller would change in the same release, the tracking history would stay behind on a field that is gone, and uninstalling the OCA module would drop a column that still held the only copy of the old text unless the copy had run first.

**The rule runs when the stage changes, not as a constraint.** A task enters a marked stage of a flagged project only with a Resolution. Code needs a pull request on the task; a task that has one and no Resolution gets Code. The rule is checked on create and on a write that moves the stage, not on every write: a task that already sits in a marked stage with neither stays editable. Rejected: a constraint like the OCA one, which would refuse any edit of such a task until someone classifies it. The rule applies to flagged projects only, where the OCA constraint applied to every project with marked stages. A copy that keeps its source's stage, as every task of a duplicated project does, takes the source's Resolution and pull requests along: it is the same closed task, not a new entry into the stage.

**Old values are converted, never guessed.** Every pull request link found in the old text is linked to the task as a record. A record the conversion creates has no state: whether the pull request was merged is not in the text. The task gets Code. A value that is exactly one link leaves nothing else behind. Any other value (several links, text around a link, a link without a scheme, a link that is not a pull request, a note) is kept whole in the read-only `pr_legacy`, and one without a pull request link creates no record and no Resolution. The conversion writes the task rows by SQL: it is not an edit of the tasks, so it leaves no write date and no tracking message and runs no rule of another module. It runs from a migration on update and from the post-init hook on install, and a second run finds nothing to convert.

**Who uninstalls the OCA module.** Not this module: it does not depend on it, and a project decides what else relies on it. The project's own module drops its dependency and marks `project_task_pull_request` for removal, once this module holds the three fields and the relation table. It does so from a data file, on every install and update: a run that fails resets the mark, and a migration script would not run a second time.

**Consequences.**

- A new record starts Open, however it is created; whoever merges and deploys sets Merged and the dates. Converted records stay without a state.
- `pr_uri` no longer takes text. Automation that wrote a note there for a task without a pull request sets the Resolution instead.
- A link needs a repository before `/pull/N` (or `/merge_requests/N`, `/pull-requests/N`, `/pulls/N`) and a number of at most nine digits. It is stored as written, but for the scheme and host in lower case and anything after the number: two spellings of one path are two records.
- A module that writes a task's new project on its own, before the other values of the same write, runs the rule before the Resolution of that write arrives. Setting the Resolution first avoids it.
- A subtask whose stage is recomputed from its parent, because the parent changed project or the subtask got another parent, is not checked.
- A task that sits in a marked stage with neither a Resolution nor a pull request, as tasks from before this version can, stops the duplication of its project until it gets one.
- A second pull request added to a task leaves no trace in its history: `pr_uri` does not change, and this version of Odoo tracks no many2many field.
- While `project_task_pull_request` stays installed next to this version, its constraint still asks for a non-empty `pr_uri` in the marked stages of every project, so a task without a pull request cannot enter them.
- While the code of `project_task_pull_request` is on the addons path, its labels of the shared fields are the ones stored, and they stay until the first update after it is gone. Taking it off the addons path in the deploy that uninstalls it gives the new labels at once: a module is uninstalled from what the database holds of it.
- The 20.0 branch gets the same change when this work is forward-ported to it.

Reversal cost: the records and the Resolution would have to be folded back into text. Nothing was discarded on the way in: a value that was more than one link is still whole in `pr_legacy`.
