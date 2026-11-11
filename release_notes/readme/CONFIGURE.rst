**Holder module**

The engine loads nothing by itself. Add one module to the project, named
``<prefix>_release_notes`` (``acme_release_notes`` below), made of:

* ``__manifest__.py``, depending on this module and loading one data file::

      {
          "name": "Acme Release Notes",
          "version": "16.0.1.0.0",
          "license": "LGPL-3",
          "depends": ["release_notes"],
          "data": ["data/release_note_load.xml"],
      }

* an empty ``__init__.py``;
* ``data/release_note_load.xml``, calling the loader with the holder's own
  name::

      <?xml version="1.0" encoding="utf-8"?>
      <odoo>
          <function model="release.note" name="_load_release_notes">
              <value>acme_release_notes</value>
          </function>
      </odoo>

* a ``notes/`` folder with one ``<key>.html`` file per note (see *Usage*);
* a test that parses every file of ``notes/`` with
  ``release.note._parse_release_note_file`` and checks that no two share a
  key and that each key is a release note: the loader only skips a file it
  cannot read, the test fails on it.

Install the holder; it brings the engine with it. Every later update of the
holder (``-u acme_release_notes``) loads the note files whose key is new.

Keep the ``<function>`` out of a ``noupdate`` block: there it would run on
install only, and notes added later would never load.

**Access groups** (Settings > Users > *Release Notes*)

* **Internal users** (no group to set) read published notes and their tags.
  Unpublished notes are hidden from them.
* **Manager** sees every note, creates and edits notes, manages tags, sees
  the *Models* and *Notified* fields, can be a note's *Deployed by* user and
  can publish and unpublish. Administrators (*Settings* access) are managers
  through an implied group and are the only users who can delete a note.

**Deployed by**

* Only a Release Notes manager can be the *Deployed by* user.
* It is meant to be whoever ran the deploy. A deploy script can set it; in
  the form, a manager can set it only to themselves, and only while it is
  empty.
* Once it is set, only an administrator can change it.

**Tags** (Release Notes > Configuration > Tags, managers only)

An editable list of name and colour. Sales, Purchase Orders, Inventory,
Manufacturing, Projects, Accounting, HR, Maintenance, Quality, Shipping and
CRM ship with the module. A tag named in a note file that does not exist yet
is created by the loader; a tag is matched by name regardless of case.
