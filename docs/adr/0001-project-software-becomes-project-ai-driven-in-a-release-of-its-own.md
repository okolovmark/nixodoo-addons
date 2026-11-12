# project_software becomes project_ai_driven in a release of its own

Status: accepted (2026-11-12)

The module `project_software` is renamed `project_ai_driven`, and its project flag `is_software_project` becomes `is_ai_driven`, labelled "AI-driven Project". The flag marks the projects whose tasks are shaped for work done by AI agents: a task type, an estimate, questions and answers, a solution, test cases, a checklist. "Software" named the team that used it first, not what the flag switches on. The rename ships alone, as a release that changes no behaviour, before the task model is reworked on top of it: a regression after that release can only come from the rename.

**How a database follows.** The old module name stays for one release as an empty module that depends on `project_ai_driven`, so updating it installs the new module. The new module's `pre_init_hook` then takes over what the old one owns before any data file is loaded: every external id, constraint and many2many table changes module, the flag's column, field rows and external ids are renamed, and the stored text that names the flag (view archs, rule domains, filters, window actions) is rewritten. Models, fields, views, access rights, menus and message subtypes keep their database ids, so follower subscriptions, tracking values and everything else that points at them survive. The empty module ends up owning nothing and is removed in the next release.

Left alone, the data files would create a second copy of every record under the new module, and the update of the emptied module would delete the originals, with whatever references them.

Rejected: rename the label only, or the field without the module. One thing would keep two names across code, external ids, the template answer and the docs, and finishing the job later costs the same migration again.

Rejected: a hard cut without the empty module. A database that has `project_software` installed, any database restored from a backup taken before the rename included, has nothing that asks for `project_ai_driven`: the old module would simply be missing from the addons path. The empty module is what lets a plain update bring the new one in.

**Consequences.**

- For that one release a project lists both modules in its `addons.yaml`, also when it pins a commit.
- The deploy that brings the rename runs an update of `project_software`, with the server stopped: the hook renames a column the running code still reads. A restart alone leaves an installed module depending on one that is not installed, and it is not loaded.
- A module of a project that extends this one switches its dependency to `project_ai_driven` in the same deploy. Its stored views are rewritten to the new flag name by the hook, and its old data files would write the old name back.
- Text that names the flag outside the rewritten tables (server action code, automated action domains, export templates) is not rewritten.
- The hook keeps its earlier job as well: on a database from before this module was cut out of the project-specific module it came from, it takes over only what moved here.
- The 20.0 branch is renamed when this work is forward-ported to it.

Reversal cost: another rename release of the same shape, a hook and an empty module for one release.
