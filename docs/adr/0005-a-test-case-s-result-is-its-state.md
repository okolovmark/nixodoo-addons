# A test case's result is its state

Status: accepted (2026-11-17)

The 16.0 branch decided this first. Its record, `docs/adr/0005-a-test-case-s-result-is-its-state.md` there, holds the alternatives it weighed; this one states the decision as it stands on 20.0 and what differs.

**The state is the only result.** A test case is Not run (the default), Passed, Failed, Waiting or Not applicable, under the keys `not_run`, `passed`, `failed`, `waiting`, `not_applicable`. `passed` stays as a stored compute of the state, read-only, set for Passed only, for the clients that read it.

**A Passed or a Failed is a verification.** A create or a write that sets one records Verified by, the user who makes it (also under `sudo()`), and Verified on, now, unless the call gives them. The same result given again is stamped again. Not run takes the result back: it clears Verified by, Verified on and Verification, and leaves the Automated test reference, which names a test, not a run. Waiting and Not applicable leave the stamp. Verification is Manual, Automated test or Demo. The result buttons of the task's case list, its phone cards and the case form set Manual where the case has no kind yet; the Not run button only clears.

**Former values.** Create and write translate `todo` to `not_run`, `done` to `passed` and `cancelled` to `not_applicable`, and drop a sent `passed`, `passed` true without a state counting as Passed, so a client written for the former selection keeps working. The translation goes with the flag, in a later version of both branches.

**What differs from 16.0.**

- No case is converted (ADR 0001). 16.0 converts every case by SQL and sets the flag from the state. Here an update from an earlier 20.0 version leaves `todo`, `done` and `cancelled` stored where that version wrote them, with the flag it wrote: Odoo leaves the rows of a value taken out of a field's own selection as they are. Verified by, Verified on and Verification start empty on every existing case, as on 16.0.
- 16.0 takes the removed `change_state_*` buttons out of the stored task form with a pre-migration and a pre-init hook. Here the pre-migration of 20.0.2.8.1 blanks every stored extension view of the module before the data files rewrite them (ADR 0001).

**Consequences.**

- A case an earlier 20.0 version left with a former value matches no filter and no kanban count of this version, and the completion of its task counts it as not passed, until it is given a result.
- A saved filter or a domain that names a former value matches only such cases; a default that names one makes a create without a state fail; a server action that writes one goes through the translation. None ships with the module.
- Clients write the state, with `verification_kind` and `test_reference` when they know them, and never `passed`: a value sent for it is dropped.
- Completion is the Passed cases over every case but the Not applicable ones; the task's kanban card counts Not run, Failed and Waiting.
- Copying a task copies each case with its state, Verified by, Verified on, Verification and Automated test reference, in every copy Odoo 20 makes: a duplicate and its sub-tasks, a duplicated project, a task or project created from a template or made into one. Odoo 20 builds the sub-tasks of a copy in `copy_data`, so the cases go there as lines of the copy. The next occurrence of a recurring task gets none, as on 16.0. A case duplicated on its own starts Not run without them.

Reversal cost: bring back the former keys and an editable flag. The states map back one to one except Failed, which has no former value, and the verification stamps have no place in the former model. No flag was overwritten on this branch.
