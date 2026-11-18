import base64
import binascii
import hashlib
import html
import logging
import re
from collections import Counter

import psycopg2

from odoo import api, models
from odoo.tools import consteq
from odoo.tools.misc import clean_context

_logger = logging.getLogger(__name__)

INLINE_IMAGE_MARKER = re.compile(r"data:image", re.IGNORECASE)
INLINE_IMAGE_OR_LINK_MARKER = re.compile(r"data:image|/web/image/", re.IGNORECASE)
IMG_TAG = re.compile(r"""<img\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.IGNORECASE)
TAG_ATTRIBUTE = re.compile(r"""\s([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
INLINE_IMAGE = re.compile(r"\s*data:(image/([\w.+-]+))(?:;[^;,]*)*;base64,([A-Za-z0-9+/=\s]*)", re.IGNORECASE)
IMAGE_LINK = re.compile(r"/web/image/(\d+)(?:[-/?#].*)?", re.DOTALL)
ACCESS_TOKEN = re.compile(r"[?&]access_token=([^&#]+)")
# The editor names a pasted file data-file-name, core mail data-filename.
NAME_ATTRIBUTES = ("data-filename", "data-file-name", "alt")
DRY_RUN_SOURCE = "/web/image/0"


def replace_image_sources(html_text, get_new_source):
    """html_text with the src of each <img> replaced where get_new_source(src,
    attributes) gives a new one, the values html-unescaped. Every other
    character stays as it is: a parser would serialise the whole text again.
    """

    def replace_source(tag_match):
        tag = tag_match.group(0)
        attributes = {}
        source_span = None
        for attribute in TAG_ATTRIBUTE.finditer(tag, len("<img")):
            name = attribute.group(1).lower()
            value_group = next((group for group in (2, 3, 4) if attribute.group(group) is not None), None)
            # a browser reads the first of two attributes of a name
            if value_group is None or name in attributes:
                continue
            attributes[name] = html.unescape(attribute.group(value_group))
            if name == "src":
                source_span = attribute.span(value_group)
        new_source = source_span and get_new_source(attributes["src"], attributes)
        if not new_source:
            return tag
        return tag[: source_span[0]] + new_source + tag[source_span[1] :]

    return IMG_TAG.sub(replace_source, html_text)


def get_image_source(attachment):
    return "/web/image/%s?access_token=%s" % (attachment.id, attachment.access_token)


def get_image_marker(copy_links):
    """The pattern a text holds when a mover may change it: a substring test
    first, so that a save without images costs nothing.
    """
    return INLINE_IMAGE_OR_LINK_MARKER if copy_links else INLINE_IMAGE_MARKER


class ImageMover:
    """Gives the images of Html texts to the record holding the text as its
    attachments, as the html editor and mail do: an inline image becomes a
    new attachment and, with copy_links, an image of another record of the
    image mixin a copy of its attachment. The same image twice on one record
    gets one attachment.

    The attachments are created in sudo: callers run it after the save that
    brought the images passed its own access checks.
    """

    def __init__(self, env, dry_run=False, copy_links=False):
        self.env = env
        self.attachments = env["ir.attachment"].sudo().with_context(clean_context(env.context))
        self.dry_run = dry_run
        self.copy_links = copy_links
        self.marker = get_image_marker(copy_links)
        self.image_count = 0
        self.byte_count = 0
        self._sources = {}
        self._fallback_name_counts = Counter()

    def move(self, text, record):
        """text with the src of its images changed to attachments of record, the text as it is without any."""
        if not self.marker.search(text):
            return text
        return replace_image_sources(text, lambda source, attributes: self._get_new_source(source, attributes, record))

    def _get_new_source(self, source, attributes, record):
        inline_image = INLINE_IMAGE.fullmatch(source)
        if inline_image:
            return self._get_inline_image_source(inline_image, attributes, record)
        image_link = self.copy_links and IMAGE_LINK.fullmatch(source)
        if image_link:
            return self._get_linked_image_source(image_link, source, record)
        return None

    def _get_inline_image_source(self, inline_image, attributes, record):
        mimetype, subtype, data = inline_image.groups()
        data = "".join(data.split())
        # a digest, not the data: a run over many records would keep every image in memory
        key = (record._name, record.id, hashlib.sha1(data.encode()).hexdigest())
        if key not in self._sources:
            self._sources[key] = self._attach_inline_image(data, mimetype, subtype, attributes, record)
        return self._sources[key]

    def _get_linked_image_source(self, image_link, source, record):
        attachment = self.attachments.browse(int(image_link.group(1))).exists()
        access_token = ACCESS_TOKEN.search(source)
        if not (
            self._is_attachment_of_other_record(attachment, record)
            and self._is_readable(attachment, access_token and access_token.group(1))
        ):
            return None
        key = (record._name, record.id, attachment.id)
        if key not in self._sources:
            self._sources[key] = self._copy_attachment(attachment, record)
        return self._sources[key]

    def _attach_inline_image(self, data, mimetype, subtype, attributes, record):
        try:
            raw = base64.b64decode(data, validate=True)
        except binascii.Error:
            return None
        if not raw:
            return None
        # core's attachment rules still apply: a user who may not edit views gets svg as text
        values = self._get_checked_contents({"raw": raw, "mimetype": mimetype.lower()}, record)
        if values is None:
            return None
        self.image_count += 1
        self.byte_count += len(raw)
        if self.dry_run:
            return DRY_RUN_SOURCE
        attachment = self._get_unprocessed_attachments().create(
            dict(
                values,
                name=self._get_image_name(attributes, subtype, record),
                res_model=record._name,
                res_id=record.id,
                access_token=self.attachments._generate_access_token(),
            )
        )
        return get_image_source(attachment)

    def _get_checked_contents(self, values, record):
        """A copy of values as the create of an attachment makes them, or None
        where core fails to read the image: it reads any image that decodes,
        and a broken one raises whatever its reader meets. The image then
        stays in the text and the save passes. Checked before the create, not
        in a savepoint around it: a savepoint flushes, which runs the pending
        precommit hooks of the save (tracking) and would catch their errors.
        """
        try:
            return self.attachments._check_contents(dict(values))
        except (MemoryError, psycopg2.Error):
            raise
        except Exception as error:  # noqa: BLE001
            # the CPU time limit of a prefork worker is a plain Exception
            if type(error) is Exception:
                raise
            _logger.info("An image stays inline in a text of %s: %r", record, error)
            return None

    def _get_unprocessed_attachments(self):
        """The values come from _check_contents already: core processes the image once."""
        return self.attachments.with_context(image_no_postprocess=True)

    def _get_image_name(self, attributes, subtype, record):
        name = next((attributes[key].strip() for key in NAME_ATTRIBUTES if attributes.get(key, "").strip()), None)
        if name:
            return name
        self._fallback_name_counts[(record._name, record.id)] += 1
        return "image-%d.%s" % (self._fallback_name_counts[(record._name, record.id)], subtype.split("+")[0].lower())

    def _is_attachment_of_other_record(self, attachment, record):
        """Whether attachment belongs to another record holding texts with images."""
        return (
            attachment.res_model in self.env
            and isinstance(self.env[attachment.res_model], self.env.registry["project.task.image.mixin"])
            and attachment.res_id
            and not attachment.res_field
            and (attachment.res_model, attachment.res_id) != (record._name, record.id)
        )

    def _copy_attachment(self, attachment, record):
        raw = attachment.raw
        # a file missing from the filestore reads empty: the link stays, as an empty inline image does
        if not raw.size:
            return None
        values = self._get_checked_contents({"raw": raw, "mimetype": attachment.mimetype}, record)
        if values is None:
            return None
        attachment_copy = attachment.with_context(image_no_postprocess=True).copy(
            dict(
                values,
                res_model=record._name,
                res_id=record.id,
                access_token=self.attachments._generate_access_token(),
            )
        )
        return get_image_source(attachment_copy)

    def _is_readable(self, attachment, access_token):
        """Whether the user saving may see the attachment anyway: a copy must not
        hand anyone an image of a record they cannot read. In sudo only the
        attachment's own token counts: the text may come from anyone.
        """
        if access_token and consteq(attachment.access_token or "", access_token):
            return True
        if self.env.su:
            return False
        return attachment.with_env(self.env).has_access("read")


class ProjectTaskImageMixin(models.AbstractModel):
    """The images in the Html fields of a task, a question row or a test case
    are attachments of that record, served with an access token, as the html
    editor attaches the images it uploads: an image written inline becomes one
    on create and write, and a new record, or a line moved to another task,
    that shows an image of another such record gets a copy of it, which
    outlives that record. A write that links an image of another record
    leaves it linked: see ADR 0011.
    """

    _name = "project.task.image.mixin"
    _description = "Html Fields Keeping their Images as Attachments"

    @api.model_create_multi
    def create(self, vals_list):
        """The inline images of the texts given become attachments of the new record, and the images of other
        records it shows copies of theirs.
        """
        records = super().create(vals_list)
        records._move_created_images(vals_list)
        return records

    def write(self, vals):
        """The inline images of the texts written become attachments of the record; a record moved to another
        task also copies the images of other records its texts show.
        """
        former_tasks = self._get_former_tasks(vals)
        result = super().write(vals)
        self._move_written_images(vals, self._filter_task_changed(former_tasks))
        return result

    @api.model
    def _get_task_fname(self):
        """The field whose write gives this record another task, if any."""
        return "task_id"

    @api.model
    def _get_image_fields(self):
        """The Html fields whose images are moved: stored, not computed, and
        in one language (the column of a translated one holds every language).
        """
        return [
            fname
            for fname, field in self._fields.items()
            if field.type == "html" and field.store and not field.compute and not field.translate
        ]

    def _get_marked_image_fnames(self, fnames, copy_links):
        """The fields of fnames whose text a mover may change on one of these
        records, read as stored (sanitized): a field the save named comes from
        the cache it just filled, without a query.
        """
        marker = get_image_marker(copy_links)
        return [fname for fname in fnames if any(marker.search(record[fname] or "") for record in self)]

    def _move_created_images(self, vals_list):
        image_fnames = self._get_image_fields()
        fnames_list = [
            record._get_marked_image_fnames([fname for fname in image_fnames if fname in vals], copy_links=True)
            for record, vals in zip(self, vals_list)
        ]
        if not any(fnames_list):
            return
        mover = ImageMover(self.env, copy_links=True)
        new_texts = {}
        for record, fnames in zip(self, fnames_list):
            new_texts.update(record._get_moved_image_texts(mover, fnames))
        self._store_texts(new_texts)

    def _get_former_tasks(self, vals):
        """{record id: task} when vals may give the records another task."""
        task_fname = self._get_task_fname()
        if not task_fname or task_fname not in vals:
            return {}
        return {record.id: record[task_fname] for record in self}

    def _filter_task_changed(self, former_tasks):
        task_fname = self._get_task_fname()
        return self.filtered(lambda record: record.id in former_tasks and record[task_fname] != former_tasks[record.id])

    def _move_written_images(self, vals, moved_records):
        # core write accepts any key on an empty recordset
        written_html_fnames = [fname for fname in vals if getattr(self._fields.get(fname), "type", None) == "html"]
        if written_html_fnames:
            image_fnames = [fname for fname in self._get_image_fields() if fname in written_html_fnames]
            (self - moved_records)._move_images(image_fnames, copy_links=False)
        if moved_records:
            # a line moved to another task copies the images of other records, as a new line would
            moved_records._move_images(self._get_image_fields(), copy_links=True)

    def _move_images(self, image_fnames, copy_links):
        fnames = self._get_marked_image_fnames(image_fnames, copy_links)
        if fnames:
            self._store_texts(self._get_moved_image_texts(ImageMover(self.env, copy_links=copy_links), fnames))

    def _get_moved_image_texts(self, mover, fnames):
        """{record: {field name: new text}} for the records of self whose texts
        mover changes. The texts are read as stored: sanitized.
        """
        new_texts = {}
        for record in self:
            texts = {}
            for fname in fnames:
                text = record[fname]
                new_text = text and mover.move(text, record)
                if new_text != text:
                    texts[fname] = new_text
            if texts:
                new_texts[record] = texts
        return new_texts

    @api.model
    def _store_texts(self, new_texts):
        """Store the texts as write() stores a field, flushed with the save,
        but without the write overrides, which the save that brought the images
        already ran: as core assigns a field of a new record.
        """
        for record, texts in new_texts.items():
            for fname, text in texts.items():
                record._fields[fname].write(record, text)
            record.modified(texts)
