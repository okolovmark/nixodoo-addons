# project_software becomes project_ai_driven, on this branch by a plain rename

Status: accepted (2026-11-13)

The module `project_software` is renamed `project_ai_driven`, and its project flag `is_software_project` becomes `is_ai_driven`, labelled "AI-driven Project". The flag marks the projects whose tasks are shaped for work done by AI agents: a task type, an estimate, questions and answers, a solution, test cases, a checklist. "Software" named the team that used it first, not what the flag switches on. The 16.0 branch made the same rename first; its record of the decision is `docs/adr/0001-project-software-becomes-project-ai-driven-in-a-release-of-its-own.md` there.

**On this branch a database does not have to follow.** On 16.0 the old name stays for one release as an empty module that depends on the new one, and a `pre_init_hook` hands everything the old module owns to the new one under its database ids, because installed databases run it. The 20.0 module was first merged two days before this decision, and no database that has to be kept is known to run it. So the rename here is the directory, the flag, the external ids and the file names, and nothing else: no module is left under the old name, and there is no hook.

Rejected: porting the empty module and the hook. They would be carried, tested and removed again for a state no known database is in.

**Consequences.**

- A project lists `project_ai_driven` alone in its `addons.yaml`.
- A database that has the 20.0 `project_software` installed is not updated in place. With the new code on the addons path, install `project_ai_driven`, then uninstall `project_software`. Both declare the same models and the same task fields, and an uninstall keeps what another installed module also declares: the tasks keep their values, their checklists and their test cases. The project flag does not come along, because its column changed name: tick AI-driven Project again. The records the old module created from its data files, its views and the two checklist message subtypes, are replaced by new ones. Between the two steps every page the module adds to the task form shows twice, and each start logs that `project_software` cannot be loaded.
- A database upgraded from 16.0 arrives with `project_ai_driven` installed: the rename happened there.

Reversal cost: a rename back. With databases to keep by then, it takes the shape 16.0 used: an empty module for one release, and a hook.
