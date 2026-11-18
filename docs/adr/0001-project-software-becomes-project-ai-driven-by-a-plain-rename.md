# project_software becomes project_ai_driven, on this branch by a plain rename

Status: accepted (2026-11-13)

The module `project_software` is renamed `project_ai_driven`, and its project flag `is_software_project` becomes `is_ai_driven`, labelled "AI-driven Project". The flag marks the projects whose tasks are shaped for work done by AI agents: a task type, an estimate, questions and answers, a solution, test cases, a checklist. "Software" named the team that used it first, not what the flag switches on. The 16.0 branch made the same rename first; its record of the decision is `docs/adr/0001-project-software-becomes-project-ai-driven-in-a-release-of-its-own.md` there.

**On this branch a database does not have to follow.** On 16.0 the old name stays for one release as an empty module that depends on the new one, and a `pre_init_hook` hands everything the old module owns to the new one under its database ids, because installed databases run it. The 20.0 module was first merged two days before this decision, and no database that has to be kept is known to run it. So the rename here is the directory, the flag, the external ids and the file names, and nothing else: no module is left under the old name, and there is no hook.

Rejected: porting the empty module and the hook. They would be carried, tested and removed again for a state no known database is in.

**Consequences.**

- A project lists `project_ai_driven` alone in its `addons.yaml`.
- A database that has the 20.0 `project_software` installed is not updated in place. With the new code on the addons path, install `project_ai_driven`, then uninstall `project_software`. Both declare the same models and the same task fields, and an uninstall keeps what another installed module also declares: the tasks keep their values, their checklists and their test cases. The project flag does not come along, because its column changed name: tick AI-driven Project again. The records the old module created from its data files, its views and the two checklist message subtypes, are replaced by new ones. Between the two steps every page the module adds to the task form shows twice, and each start logs that `project_software` cannot be loaded.
  - 2026-11-17: from 20.0.2.8.1 `project_ai_driven` declares neither `project.task.subtask` nor the task fields `questions` and `answers` (ADR 0006, ADR 0008): the uninstall of `project_software` drops the two fields with their columns, and leaves the checklist items in the table `project_task_subtask`, which no model reads (checked on a copy of such a database). The test cases stay.
- A database upgraded from 16.0 arrives with `project_ai_driven` installed: the rename happened there.
- 2026-11-17: 20.0.2.8.1 carries this branch's first migration, a pre-migration that blanks the module's stored extension views so that an update from an earlier 20.0 version passes without dev flags; it converts no data. Translations of those views made in the interface go with the blanked arch: the module ships none.
  - 2026-11-18: from 20.0.2.15.0 the pre-migration is `migrations/0.0.0/pre-migration.py`, which Odoo runs on every version change. Under `20.0.2.8.1` it ran only on an update from a version before that one, so an update from 20.0.2.8.1 or later to a version that takes a field out of the views (`passed` in 20.0.2.15.0) failed validating the task form. The translations of those views made in the interface are now lost on every version change.
- 2026-11-18: 20.0.2.15.0 carries this branch's first data conversion, `migrations/20.0.2.15.0/post-migration.py`: one SQL statement converts the test case states 20.0.2.4.1 and earlier stored, `todo`, `done` and `cancelled`, to `not_run`, `passed` and `not_applicable` (ADR 0005).

Reversal cost: a rename back. With databases to keep by then, it takes the shape 16.0 used: an empty module for one release, and a hook.
