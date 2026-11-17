**Write a note** (in the pull request of the change): add
``notes/<key>.html`` to the project's holder module (see *Configuration*) - a
front-matter header between two ``---`` lines, then the HTML body::

    ---
    key: PROJ-123
    title: Release notes in Odoo
    summary: Updates are now announced at the top of the main menu.
    tags: Projects
    models: purchase.order, sale.order
    ---
    <h2>What changed</h2>
    <p>...</p>
    <a class="btn btn-primary" href="/odoo/action-purchase.purchase_rfq">Open Requests for Quotation</a>

* ``key`` and ``title`` are required; ``summary`` is one line under the
  title. ``tags`` and ``models`` are comma-separated; ``models`` are technical
  model names, and a name not installed is skipped.
* Header names are case-insensitive. The file is read as UTF-8.
* A button is a link with ``class="btn btn-primary"`` and
  ``href="/odoo/action-<module>.<action xml_id>"``.
* A file the loader cannot read as a note (no header, no key or title, not
  UTF-8) is skipped and logged as an error, and so is a file declaring a key
  an earlier file already has. The other notes still load and the module
  update goes on: a note never stops a deploy.
* A key that already exists in Odoo is skipped: a later edit of the file does
  not reach that note. Edit the note in Odoo instead.

**Load the notes** (the deploy): update the holder module,
``-u <prefix>_release_notes``. Each file with a new key becomes an unpublished
note.

**Publish a note** (the deployer, after the deploy):

#. Open *Release Notes*, filter *Unpublished* and open the note.
#. Read it and check each button opens the right screen. A manager has the
   body in the text editor: a click on a button shows its link, and that link
   opens the screen.
#. Check *Models*: they decide who is announced the note, and publishing
   needs at least one.
#. If a video is attached, watch it: every reader of the note can play it.
#. Press *Publish*.

Rules:

* Only the note's *Deployed by* user can publish it - administrators
  included. A note without one cannot be published.
* *Published*, *Published by*, *Published date* and *Announced* are set only
  by the buttons; writing them directly is refused.
* The key cannot be changed after the note is created.
* Any manager can *Unpublish*: the note is hidden from users and leaves their
  main menu. Published again, it is announced only to the users who had not
  opened or skipped it.

**Read notes** (every internal user):

* The main menu icon shows how many notes are new for the user. At the top of
  the menu a panel names the newest one: *View* opens it, and the form's pager
  walks through the other new ones; *Skip* stops announcing all of them. On a
  phone, the counter is on the menu button and the panel at the top of the
  menu it opens.
* A note opened in any way (the panel, the menu, a link) is no longer
  announced to that user. Notes published before the user was created are
  never announced to them.
* The key opens the note's ticket for users who can read tickets; the others
  see it as text.
* The newest video attached to a note plays above its text.
* *Release Notes* lists the published notes, newest first. Search by title,
  key or summary, filter by tag, or group by *Tags*.
