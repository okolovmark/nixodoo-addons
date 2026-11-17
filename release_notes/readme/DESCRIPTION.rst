Release notes shown to users inside Odoo: what a deployed change means for
the people using it, in their words, with buttons that open the new screen.

This module is the engine: the models, the access rules, the screens and the
loader. It ships no notes of its own. A project adds a small holder module,
``<prefix>_release_notes``, that keeps the note files and asks the engine to
load them (see *Configuration*).

* A **Release Note** (``release.note``) has a key (usually the ticket, e.g.
  PROJ-123), a title, a one-line summary, an HTML body, tags and the business
  models the change is about.
* Notes are written as files in the holder module's ``notes/`` folder and
  loaded on every install or update of that module. The loader only creates
  notes; it never overwrites one that already exists.
* A loaded note starts unpublished and is visible to Release Notes managers
  only. Its *Deployed by* user publishes it.
* A published note is announced in the main menu of every active internal
  user who can read at least one of the note's models and was created before
  it was published: a counter on the menu icon and a panel at the top of the
  menu with *View* and *Skip*. It stays announced until the user opens or
  skips it. No chat and no email is sent.
* A note's key can open its ticket, for the users who can read tickets
  (see *Configuration*).
* A video attached to a note plays at the top of it: the place for a demo of
  the change.
* Every internal user can open *Release Notes* to read the published notes,
  search them and filter or group them by tag.

Why the notes live in a module of their own:

* The engine depends on ``mail`` only and the holder on the engine only, so
  an update of the holder (``-u <prefix>_release_notes``) touches no other
  module of the project.
* One loader call reads the whole ``notes/`` folder, so a new note adds a file
  and no manifest entry - parallel pull requests do not conflict on the
  manifest.
* Buttons in a note are plain ``/web#action=<module>.<xml_id>`` links, so a
  note can point at any module's screen without a dependency on it.

``readme/release_note_flow.puml`` shows the flow from the pull request to the
users' main menu.
