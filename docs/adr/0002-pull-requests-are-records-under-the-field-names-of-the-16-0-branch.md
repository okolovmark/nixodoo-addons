# Pull requests are records, under the field names of the 16.0 branch

Status: accepted (2026-11-13)

A task of an AI-driven project carries its pull requests as records of `project.pull.request` (the link, the repository and number read from it, a state, a merge date and a deploy date), linked many-to-many to tasks, and it closes with a Resolution: Code, Data, Configuration or No change. The 16.0 branch decided this first. Its record, `docs/adr/0002-pull-requests-are-records-under-the-field-names-of-project-task-pull-request.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The names are those of 16.0.** `pr_uri` on the task and `pr_required_states` on the project, with its table `project_pr_required`, carry names 16.0 kept from the module it took those fields over from. Nothing on this branch forces them. They stay so that a caller written for one branch works on the other, and so that a database upgraded from 16.0 keeps its values without a migration. `pr_uri` reads the link of the task's first pull request. Writing one pull request link to it finds or creates the record and adds it to the task; any other text is refused. `pr_legacy`, the read-only field where 16.0 keeps old text it could not turn into records, exists here for such a database: nothing on this branch writes it.

Rejected: names of this branch's own. One thing would have two names across the branches, in every caller and in every later forward-port.

**The rule runs when the stage changes, and the state of a task is not part of it.** A task enters a marked stage of a flagged project only with a Resolution. Code needs a pull request on the task; a task that has one and no Resolution gets Code. The rule is checked on create and on a write that moves the stage, not on every write: a task that already sits in a marked stage with neither stays editable. Odoo 20 gives a task a `state` next to its stage, and a task can be set Done without entering a marked stage. The rule does not look at it: the stages a project marks are where that project says what closing means, and one rule keeps one trigger.

**A copy that keeps its source's stage takes what closed it.** It is the same closed task, not a new entry into the stage, so it takes the Resolution and the pull requests of its source, unless the copy is given its own. On 16.0 only the duplication of a project keeps the stage of a task. Odoo 20 keeps it for every copy that is not sent to another stage, a plain Duplicate included, so here the values of each copy are completed (`copy_data`) and both cases are covered. A copy sent to another stage takes nothing and meets the rule like a new task, and so does the copy of a task that has no stage. The next occurrence of a recurring task is such a copy: core sends it to the first stage of its project after its values are copied, so it takes nothing unless that is the stage of the task it repeats.

**No conversion.** 16.0 turns the free text of the field it took over into records. This branch never had that text: there is nothing to convert, and no hook.

**Consequences.**

- A new record starts Open, however it is created; whoever merges and deploys sets Merged and the dates.
- A link needs a repository before `/pull/N` (or `/merge_requests/N`, `/pull-requests/N`, `/pulls/N`) and a number of at most nine digits. It is stored as written, but for the scheme and host in lower case and anything after the number: two spellings of one path are two records.
- A task that sits in a marked stage with neither a Resolution nor a pull request, as it can when the stage was marked or the project flagged after it got there, cannot be duplicated and stops the duplication of its project until it gets one.
- A task set Done by its state, outside the marked stages, needs no Resolution.

Reversal cost: the records and the Resolution would have to be folded back into text.
