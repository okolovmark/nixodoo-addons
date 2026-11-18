# Images in task texts become attachments of the record on save

Status: accepted (2026-11-18)

The 16.0 branch decided this first. Its record, `docs/adr/0011-images-in-task-texts-become-attachments-of-the-task-on-save.md` there, is carried here with what differs on 20.0.

The Html texts of a task, of its question rows and of its test cases can hold their images inline: a tool writing over RPC sends an image as a base64 `data:` URI in the `src` of an `<img>`. Every read of such a text carries the bytes of every image in it: the form, an export, and an agent that reads a task through a connector, whose read grows by the size of each image.

**A save gives the inline images of its texts to the record holding the text.** From 20.0.2.13.0 `project.task.image.mixin`, on the task, the question row and the test case, follows each create and write: an `<img>` whose `src` is a base64 inline image becomes an `ir.attachment` of the record whose text holds it (`res_model` and `res_id` of the task, the row or the test case) with an access token, and the `src` becomes `/web/image/<id>?access_token=<token>`. That is what the html editor does with an image added in the form, and what `mail.message` does with the inline images of a message body. The texts are the stored Html fields that are neither computed nor translated (the column of a translated field holds every language), and from 20.0.2.15.1 the html properties of a task's `task_properties`, the defaults Odoo fills in from the project included: on create and on every change of the task's project, its own or one that follows a parent, its other properties stored as they are. The mover changes only the `src` and leaves the rest of the text as stored, without a parser serialising it again; the field then sanitizes the new text as on every write (`Field.write`). It applies to every task of the database, in an AI-driven project or not, and to every saver.

Rejected:

- Leave the images inline. Every read stays as heavy as its images, and an agent's read of a task with screenshots is mostly base64.
- The editor's upload alone. It covers an image added in the form, not a tool writing over RPC, a text created by other code or the copy of a text.
- A scheduled job that moves the images later. The texts stay heavy until it runs, and it rewrites records after the fact, as its own user, changing their `write_date` and `write_uid` unless it goes around the ORM.

**The new text is stored without a second write.** The attachments are created after `super()`, under `sudo()` and with the saving user as creator, once the save has passed its own access checks. The new texts are stored through their fields (`Field.write`, then `modified`) and flushed with the save, so tracking sees one save, and no write override of another module runs a second time. On the task the mixin comes below core's classes, `html.field.history.mixin` included, so the description history builds its revision from the moved text.

**Core decides what an image is.** `ir.attachment._check_contents` runs first, on a copy of the values and before the create, not in a savepoint around it: a savepoint flushes, which would run the save's precommit hooks (tracking) and catch their errors. The attachment is then created with `image_no_postprocess`, so core processes each image once. An image core fails to read stays inline and the save passes. `MemoryError`, the CPU time limit of a worker and database errors are raised. Core resizes an image larger than `base.image_autoresize_max_px` (1920x1920 by default), as for every attachment upload, and applies its other attachment rules: an SVG moved by a user who may not edit views is stored as text, and no longer displays.

**A new record copies the images of another record.** A task, a question row or a test case created with a text that links `/web/image/<id>` of an attachment of another task, row or test case gets its own copy of that attachment, with a new token, and its text links the copy: a duplicated task and its copied lines, a task created from a template, the next task of a recurrence, a create from another record's text. A row or a test case moved to another task does the same for the images of other records; its own images stay its own. The copy outlives its source. A write that pastes such a link leaves it, and so does a copy of an image core fails to read or whose file is missing from the filestore.

**A copy needs the right to see the image.** A copy hands the image to whoever reads the new record, so it is made only of an image the saving user may see: one they can read, or one whose link carries the attachment's own access token. Under `sudo()`, the superuser included, a read check proves nothing about where the text came from, so only the token counts.

**Old texts move through one method.** 20.0.2.13.0 has no migration. `project.task._move_inline_images_to_attachments(dry_run=False, store=True)` moves the inline images of the texts of tasks and of their rows with the mover a save uses, for a migration or a one-time script, run over the tasks in batches: it holds every new text until it returns. It returns the counts `records`, `images` and `bytes`; a dry run counts as a run would. `store=False` returns the new texts without storing them, so that a caller stores them by SQL and keeps `write_date` and `write_uid`.

**What differs on 20.0.**

- The Odoo 20 html field uploads an image added in the editor when the record is saved, through `/html_editor/attachment/add_data`, as an attachment of the record being edited, and links it with an access token. The mixin covers what the editor does not: a text written over RPC or by other code, the copies of a text, a line moved to another task, and texts saved before.
- The record holding the text owns the image, as the editor attaches it: an image of a question row or a test case is an attachment of the row. On 16.0 every image is an attachment of the task. A link to an image of any other record of the three models is copied; on 16.0, a link to another task's image.
- A row's images are deleted with the row. A task deleted takes its own images, and deletes its rows through the ORM, under `sudo()` once its own access check passed, so their images go with them: the database cascade would leave them.
- The read check of a copy is `has_access("read")`, on 16.0 `check("read")`. The attachment is created with `image_no_postprocess` after `_check_contents`; on 16.0 its create processes the image a second time.
- Core stores markup given as an image and a truncated TIFF as they come, without resizing them, so they become attachments. On 16.0 core fails to read them and they stay inline. A decompression bomb stays inline on both.

**Consequences.**

- The images of a task's Description and Solution show among the attachments of the task's chatter and are deleted with the task. The images of a row are attachments of the row. An image taken out of a text stays an attachment of its record.
- The link carries an access token: whoever holds it sees the image, as with the images of a message.
- One image is one attachment per save: pasted again in a later save, it becomes another.
- Only the `src` of an `<img>` is read. An image in a `srcset`, a `<picture>` source or a CSS background, and an absolute `https://<host>/web/image/<id>` link, stay as they are; a file link `/web/content/<id>` in a copy keeps pointing at the source record.
- Texts saved before 20.0.2.13.0 keep their inline images until they are saved again or the method runs on their task.

Reversal cost: drop the mixin from the three models. Texts already moved keep their links, which work as long as their attachments exist; putting the images back inline takes a script.
