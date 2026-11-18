# Images in task texts become attachments of the task on save

Status: accepted (2026-11-17)

The Html texts of a task, of its question rows and of its test cases held their images inline: the 16.0 editor puts a pasted or dropped image into the text as a base64 `data:` URI, and a tool writing over RPC sends the same. Every read of such a text carries the bytes of every image in it: the form, an export, and an agent that reads a task through a connector, whose read grows by the size of each image.

**A save gives the inline images of its texts to the task, as core mail does.** From 16.0.2.13.0 `project.task.image.mixin`, on the task, the question row and the test case, follows each create and write: an `<img>` whose `src` is a base64 inline image becomes an `ir.attachment` of the task (`res_model` `project.task`, `res_id` the task, also for an image of a row) with an access token, and the `src` becomes `/web/image/<id>?access_token=<token>`. That is what `mail.message` does with the inline images of a message body. The texts are the stored Html fields that are neither computed nor translated (the column of a translated field holds every language). Only the `src` changes: the rest of the text is kept as stored, not serialised again by a parser. It applies to every task of the database, in an AI-driven project or not, and to every saver.

Rejected:

- Leave the images inline. Every read stays as heavy as its images, and an agent's read of a task with screenshots is mostly base64.
- An upload in the client only. The 16.0 editor uploads an image it modifies, not one pasted, and a change to the editor covers neither a tool writing over RPC nor a text created outside the form.
- A scheduled job that moves the images later. The texts stay heavy until it runs, and it rewrites tasks after the fact, as its own user, changing their `write_date` and `write_uid` unless it goes around the ORM.

**The new text is stored without a second write.** The attachments are created after `super()`, under `sudo()` and with the saving user as creator, once the save has passed its own access checks. The new texts are stored through their fields (`Field.write`, then `modified`) and flushed with the save, so tracking sees one save, and no write override of another module runs a second time.

**Core decides what an image is.** `ir.attachment._check_contents` runs first, on a copy of the values and before the create, not in a savepoint around it: a savepoint flushes, which would run the save's precommit hooks (tracking) and catch their errors. An image core cannot read (markup, a decompression bomb, a truncated file) stays inline and the save passes. `MemoryError`, the CPU time limit of a worker and database errors are raised. Core resizes an image larger than `base.image_autoresize_max_px` (1920x1920 by default), as for every attachment upload, and applies its other attachment rules: an SVG is stored as text for a user who may not edit views.

**A new record copies the images of another task.** A task, a question row or a test case created with a text that links `/web/image/<id>` of another task's attachment gets its own copy of that attachment, with a new token, and its text links the copy: a duplicated task, the next task of a recurrence, a create from another task's text. A row moved to another task does the same. The copy outlives the source task, whose deletion deletes its attachments. A write that pastes such a link leaves it.

**A copy needs the right to see the image.** A copy hands the image to whoever reads the new record, so it is made only of an image the saving user may see: one they can read, or one whose link carries the attachment's own access token. Under `sudo()`, the superuser included, a read check proves nothing about where the text came from, so only the token counts.

**Old texts move through one method.** 16.0.2.13.0 has no migration. `project.task._move_inline_images_to_attachments(dry_run=False, store=True)` moves the inline images of the texts of tasks and of their rows with the mover a save uses, for a migration or a one-time script. It returns the counts `records`, `images` and `bytes`; a dry run counts as a run would. `store=False` returns the new texts without storing them, so that a caller stores them by SQL and keeps `write_date` and `write_uid`.

**Consequences.**

- The images show among the attachments of the task's chatter and are deleted with the task. An image taken out of a text, or of a deleted row, stays on the task.
- The link carries an access token: whoever holds it sees the image, as with the images of a message.
- One image is one attachment per save: pasted again in a later save, it becomes another.
- Only the `src` of an `<img>` is read. An image in a `srcset`, a `<picture>` source or a CSS background, and an absolute `https://<host>/web/image/<id>` link, stay as they are; a file link `/web/content/<id>` in a copy keeps pointing at the source task.
- Texts saved before 16.0.2.13.0 keep their inline images until they are saved again or the method runs on their task.

Reversal cost: drop the mixin from the three models. Texts already moved keep their links, which work as long as their attachments exist; putting the images back inline takes a script.
