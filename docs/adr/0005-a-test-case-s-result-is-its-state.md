# A test case's result is its state

Status: accepted (2026-11-16)

A test case carried two results: a state (TODO, Done, Waiting, Cancelled) and a Passed flag that the state buttons set and cleared but any write could set apart from the state. On converted data the two disagreed: cases Done without the flag, TODO with it, Cancelled with it. Nothing recorded a failed run, who verified a case or when. An agent reads the keys over the API, not the labels, and `done` and `cancelled` misled it.

From 16.0.2.5.0 the state is the only result: Not run (the default), Passed, Failed, Waiting, Not applicable, under the keys `not_run`, `passed`, `failed`, `waiting`, `not_applicable`. The keys say what the labels say. `passed` stays as a stored compute of the state, read-only, for the clients that read it.

A Passed or a Failed is a verification. A create or a write that sets one records Verified by, the user who makes it (also under `sudo()`), and Verified on, now, unless the call gives them. Setting the same result again is a new verification and is stamped again. Not run takes the result back: it clears Verified by, Verified on and Verification, and leaves the Automated test reference, which names a test, not a run. Waiting and Not applicable leave the stamp as it is. Verification says how the result was reached: Manual, Automated test or Demo. The result buttons on the task's case list, the phone cards and the case form are a person's verdict and set Manual where the case has no kind yet; the Not run button only clears.

For the rollout, create and write translate the former values (`todo` to `not_run`, `done` to `passed`, `cancelled` to `not_applicable`) and drop a sent `passed`, `passed` true without a state counting as Passed, so a client written for the former selection keeps working. The translation goes with the flag, in a later version.
  - 2026-11-18: 16.0.2.15.0 removes the flag and the translation, as planned: a sent `passed` or former value fails with Odoo's `ValueError`, and the update deletes the flag with its column.

Rejected: keeping `todo`, `done` and `cancelled` as keys under new labels. The screen would be right and the API, which agents read, still wrong.

Rejected: refusing the former values. A session running older tooling would fail mid-run.

Rejected: filling Verified by and Verified on of migrated cases from their last writer and write date. The last writer of a row is not necessarily who verified it: an edit of the steps writes the row too.

**Existing cases.** The post-migration and the install hook convert every case by SQL (`todo` to `not_run`, `done` to `passed`, `cancelled` to `not_applicable`) and set the flag from the state, without writing the cases through the ORM: write dates stay, and Verified by, Verified on and Verification stay empty on a converted case. Odoo deletes the former selection values at the end of the update. A second run changes nothing. Before the data files load, the pre-migration and the pre-init hook take the removed `change_state_*` buttons out of the stored test case list of the task form, in every translation; without that, an update without dev flags, and a take-over from the former module names, fail validating the task form.

**Consequences.**

- A saved filter or a domain that names a former value matches nothing after the update; a default that names one makes a create without a state fail; a server action that writes one goes through the translation. None ships with the module.
- Clients write the state, with `verification_kind` and `test_reference` when they know them, and never `passed`: a value sent for it is dropped.
- Completion is the Passed cases over every case but the Not applicable ones; the task's kanban card counts Not run, Failed and Waiting.
- Copying a task copies each case with its state, Verified by, Verified on, Verification and Automated test reference. A case duplicated on its own starts Not run without them.
- The buttons are taken out of the stored arch the way the dropped fields are. A view of another module that names a removed button is not rewritten.
- The 20.0 branch gets the same change when this work is forward-ported to it.

Reversal cost: bring back the former keys and an editable flag. The states map back one to one except Failed, which has no former value. The flag values the conversion overwrote are not recoverable, and the verification stamps have no place in the former model.
