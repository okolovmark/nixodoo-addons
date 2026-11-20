import base64
import binascii
import bisect
import hashlib
import html
import logging
import re
from collections import Counter
from functools import partial
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import psycopg2

from odoo import api, models
from odoo.exceptions import AccessError
from odoo.tools import consteq
from odoo.tools.misc import clean_context

_logger = logging.getLogger(__name__)

INLINE_IMAGE_MARKER = re.compile(r"data:image", re.IGNORECASE)
INLINE_IMAGE_OR_LINK_MARKER = re.compile(r"data:image|/web/image/|/web/content/", re.IGNORECASE)
# A comment is text: matched first and whole, so that no tag or quote in it is read; unclosed, it runs to the end.
COMMENT = r"<!--(?s:.*?)(?:-->|\Z)"
# The name holds no quote and ends where an attribute may start, and no unquoted part runs over a "<"
# (lxml quotes every value): otherwise a text of "<a<a<a..." or of '<a" "<a" "...' backtracks.
START_TAG = r"""<([A-Za-z][^\s/<>"']*)(?=[\s/>])(?:[^<>"']|"[^"]*"|'[^']*')*>"""
COMMENT_OR_START_TAG = re.compile(COMMENT + "|" + START_TAG)
TAG_ATTRIBUTE = re.compile(r"""\s([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
# the pattern of html.unescape: references unescaped one by one give the value it gives
CHARACTER_REFERENCE = re.compile(r"&(?:#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)")
# read without its leading zeros: html.unescape runs an int() that refuses over 4300 digits
DECIMAL_REFERENCE = re.compile(r"&#0*([0-9]+)(;?)")
SRCSET_SEPARATORS = re.compile(r"[\t\n\f\r ,]*")
SRCSET_URL = re.compile(r"[^\t\n\f\r ]+")
SRCSET_DESCRIPTORS = re.compile(r"(?:[^,(]|\([^)]*\)?)*,?")
# each alternative takes its own leading spaces: "url(" and spaces without ")" would be quadratic
CSS_URL = re.compile(r"""\burl\((?:\s*"([^"]*)"|\s*'([^']*)'|\s*([^\s"'()]+))?\s*\)""", re.IGNORECASE)
INLINE_IMAGE = re.compile(r"\s*data:(image/([\w.+-]+))(?:;[^;,]*)*;base64,([A-Za-z0-9+/=\s]*)", re.IGNORECASE)
# the id as the editor writes it, ASCII digits, at most ten: int() refuses over 4300 digits
IMAGE_LINK = re.compile(r"/web/image/([0-9]{1,10})(?:[-/?#].*)?", re.DOTALL)
FILE_LINK = re.compile(r"/web/content/([0-9]{1,10})(?:[/?#].*)?", re.DOTALL)
FILE_LINK_ID = re.compile(r"(?<=^/web/content/)[0-9]+")
# The 16.0 editor names a pasted file data-file-name, core mail data-filename.
NAME_ATTRIBUTES = ("data-filename", "data-file-name", "alt")
DRY_RUN_SOURCE = "/web/image/0"
IMAGE = "image"
FILE = "file"
LINK_PATTERNS = {IMAGE: IMAGE_LINK, FILE: FILE_LINK}


def read_attribute_url(value):
    return [(0, len(value))]


def read_srcset_urls(value):
    """The spans of the urls of a srcset value, split as the HTML standard
    does: a url runs to a whitespace (a data URI holds a comma), less its
    trailing commas, and its descriptors to a comma outside parentheses.
    """
    spans = []
    position = SRCSET_SEPARATORS.match(value).end()
    while position < len(value):
        url = SRCSET_URL.match(value, position).group()
        url_end = position + len(url)
        stripped_end = position + len(url.rstrip(","))
        spans.append((position, stripped_end))
        if stripped_end == url_end:
            url_end = SRCSET_DESCRIPTORS.match(value, url_end).end()
        position = SRCSET_SEPARATORS.match(value, url_end).end()
    return spans


def read_style_urls(value):
    spans = []
    for css_url in CSS_URL.finditer(value):
        url_group = next((group for group in (1, 2, 3) if css_url.group(group) is not None), None)
        if url_group:
            spans.append(css_url.span(url_group))
    return spans


# (tag, attribute): how its urls are read, and what they show; the tag None is any tag
URL_ATTRIBUTES = {
    ("img", "src"): (read_attribute_url, IMAGE),
    ("img", "srcset"): (read_srcset_urls, IMAGE),
    ("source", "srcset"): (read_srcset_urls, IMAGE),
    ("a", "href"): (read_attribute_url, FILE),
    (None, "style"): (read_style_urls, IMAGE),
}


def unescape(text):
    """html.unescape(text), without its int() on a long decimal reference."""
    return CHARACTER_REFERENCE.sub(unescape_reference, text)


def unescape_reference(reference):
    decimal_reference = DECIMAL_REFERENCE.fullmatch(reference.group())
    if not decimal_reference:
        return html.unescape(reference.group())
    digits, semicolon = decimal_reference.groups()
    # past seven digits the number is beyond U+10FFFF, which html.unescape reads as U+FFFD
    return "\ufffd" if len(digits) > 7 else html.unescape("&#%s%s" % (digits, semicolon))


def unescape_with_spans(raw_value):
    """raw_value html-unescaped, and the function giving the span in raw_value
    of a span of the unescaped value: a character reference maps as a whole.
    """
    references = []
    value_parts = []
    raw_position = value_length = 0
    for reference in CHARACTER_REFERENCE.finditer(raw_value):
        text = unescape_reference(reference)
        value_length += reference.start() - raw_position
        references.append((value_length, value_length + len(text), *reference.span()))
        value_parts += [raw_value[raw_position : reference.start()], text]
        value_length += len(text)
        raw_position = reference.end()
    value_parts.append(raw_value[raw_position:])
    value_starts = [reference[0] for reference in references]

    def get_raw_position(position, is_end):
        # an end stops before a reference that starts there, a start begins it
        index = (bisect.bisect_left if is_end else bisect.bisect_right)(value_starts, position) - 1
        if index < 0:
            return position
        value_start, value_end, raw_start, raw_end = references[index]
        if position < value_end:
            return raw_end if is_end else raw_start
        return raw_end + position - value_end

    return "".join(value_parts), lambda start, end: (get_raw_position(start, False), get_raw_position(end, True))


def replace_value_urls(raw_value, read_urls, get_new_url, unescaped_value=None):
    """raw_value, an attribute value as stored, with each url read_urls finds
    in its unescaped value replaced where get_new_url(url) gives a new one,
    written escaped. unescaped_value is unescape_with_spans(raw_value) where
    the caller has it already.
    """
    value, get_raw_span = unescaped_value or unescape_with_spans(raw_value)
    new_parts = []
    raw_position = 0
    for start, end in read_urls(value):
        new_url = get_new_url(value[start:end])
        if new_url:
            raw_start, raw_end = get_raw_span(start, end)
            new_parts += [raw_value[raw_position:raw_start], html.escape(new_url)]
            raw_position = raw_end
    return "".join(new_parts) + raw_value[raw_position:]


def replace_urls(html_text, get_new_url, url_attributes=URL_ATTRIBUTES):
    """html_text with the urls of url_attributes replaced where
    get_new_url(url, kind=kind, attributes=attributes) gives a new one, kind
    IMAGE or FILE, the url and the attributes html-unescaped. Every other
    character stays as it is: a parser would serialise the whole text again.
    A comment is not read.
    """

    def replace_tag(tag_match):
        tag = tag_match.group(0)
        if tag_match.group(1) is None:
            return tag
        tag_name = tag_match.group(1).lower()
        attributes = {}
        url_values = []
        for attribute in TAG_ATTRIBUTE.finditer(tag, tag_match.end(1) - tag_match.start()):
            name = attribute.group(1).lower()
            value_group = next((group for group in (2, 3, 4) if attribute.group(group) is not None), None)
            # a browser reads the first of two attributes of a name
            if value_group is None or name in attributes:
                continue
            raw_value = attribute.group(value_group)
            url_reader = url_attributes.get((tag_name, name)) or url_attributes.get((None, name))
            if url_reader:
                unescaped_value = unescape_with_spans(raw_value)
                url_values.append((attribute.span(value_group), unescaped_value, *url_reader))
                attributes[name] = unescaped_value[0]
            else:
                attributes[name] = unescape(raw_value)
        new_parts = []
        tag_position = 0
        for (start, end), unescaped_value, read_urls, kind in url_values:
            get_value_url = partial(get_new_url, kind=kind, attributes=attributes)
            new_value = replace_value_urls(tag[start:end], read_urls, get_value_url, unescaped_value)
            new_parts += [tag[tag_position:start], new_value]
            tag_position = end
        return "".join(new_parts) + tag[tag_position:]

    return COMMENT_OR_START_TAG.sub(replace_tag, html_text)


def replace_image_sources(html_text, get_new_source):
    """html_text with the src of each <img> replaced where
    get_new_source(src, attributes) gives a new one: replace_urls for it alone.
    """
    return replace_urls(
        html_text,
        lambda url, kind, attributes: get_new_source(url, attributes),
        {("img", "src"): URL_ATTRIBUTES[("img", "src")]},
    )


def get_image_source(attachment):
    return "/web/image/%s?access_token=%s" % (attachment.id, attachment.access_token)


def get_access_token(link):
    """The access token link carries, read as the server reads its query:
    the first parameter whose name decodes to access_token.
    """
    for pair in urlsplit(link).query.split("&"):
        name, _separator, value = pair.partition("=")
        if unquote_plus(name) == "access_token":
            return unquote_plus(value)
    return None


def get_file_link(link, attachment):
    """link, a /web/content/<id> link of this database, pointed at attachment:
    its path, query and fragment as they are but for the id, and for the
    access token, added last in place of every parameter whose name decodes
    to access_token, as the server decodes it.
    """
    parts = urlsplit(link)
    query_pairs = [
        pair for pair in parts.query.split("&") if pair and unquote_plus(pair.partition("=")[0]) != "access_token"
    ]
    query_pairs.append("access_token=%s" % attachment.access_token)
    path = FILE_LINK_ID.sub(str(attachment.id), parts.path, count=1)
    return urlunsplit(("", "", path, "&".join(query_pairs), parts.fragment))


def get_image_marker(copy_links):
    """The pattern a text holds when a mover may change it: a substring test
    first, so that a save without images costs nothing.
    """
    return INLINE_IMAGE_OR_LINK_MARKER if copy_links else INLINE_IMAGE_MARKER


class ImageMover:
    """Gives the images of Html texts to their task as attachments, as core
    mail does with the images of a message: an inline image becomes an
    attachment and, with copy_links, an image or a file link of another task
    a copy of its attachment. An attachment the task has with the same
    contents, not one of a message, serves instead of a new one; another task
    never shares it.

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
        self._inline_attachments = {}
        self._attachment_copies = {}
        self._task_attachments = {}
        self._fallback_name_counts = Counter()
        self._base_hosts = {}

    def move(self, text, task):
        if not self.marker.search(text):
            return text
        return replace_urls(text, lambda url, kind, attributes: self._get_new_url(url, kind, attributes, task))

    def _get_new_url(self, url, kind, attributes, task):
        link = None
        inline_image = kind == IMAGE and INLINE_IMAGE.fullmatch(url)
        if inline_image:
            attachment = self._get_inline_image_attachment(inline_image, attributes, task)
        else:
            link = self.copy_links and self._get_local_link(url, task)
            link_match = link and LINK_PATTERNS[kind].fullmatch(link)
            attachment = link_match and self._get_attachment_copy(int(link_match.group(1)), link, kind, task)
        if not attachment:
            return None
        if self.dry_run:
            return DRY_RUN_SOURCE
        return get_file_link(link, attachment) if kind == FILE else get_image_source(attachment)

    def _get_local_link(self, url, task):
        """url from its path on, None for a link to another host: an absolute
        link is local on the host of task.get_base_url(), as the brief reads
        it (website gives a task its company's website domain).
        """
        # not at the top: html_markdown imports project_task_question, whose model must load after this mixin
        from .html_markdown import get_base_host, get_local_url, normalise_url

        if task.id not in self._base_hosts:
            self._base_hosts[task.id] = get_base_host(task.get_base_url())
        return get_local_url(normalise_url(url), self._base_hosts[task.id])

    def _get_inline_image_attachment(self, inline_image, attributes, task):
        mimetype, subtype, data = inline_image.groups()
        data = "".join(data.split())
        # a digest, not the data: a run over many tasks would keep every image in memory
        key = (task.id, hashlib.sha1(data.encode()).hexdigest())
        if key not in self._inline_attachments:
            self._inline_attachments[key] = self._attach_inline_image(data, mimetype, subtype, attributes, task)
        return self._inline_attachments[key]

    def _attach_inline_image(self, data, mimetype, subtype, attributes, task):
        try:
            raw = base64.b64decode(data, validate=True)
        except binascii.Error:
            return None
        if not raw:
            return None
        # core's attachment rules still apply: a user who may not edit views gets svg as text
        values = self._get_checked_contents(
            self.attachments, {"raw": raw, "mimetype": mimetype.lower()}, task, "An image stays inline"
        )
        if values is None:
            return None
        create = partial(self._create_image_attachment, values, len(raw), subtype, attributes, task)
        return self._get_task_attachment(values, task, create)

    def _create_image_attachment(self, values, size, subtype, attributes, task):
        self.image_count += 1
        self.byte_count += size
        if self.dry_run:
            return True
        return self.attachments.create(
            dict(
                values,
                name=self._get_image_name(attributes, subtype, task),
                res_model="project.task",
                res_id=task.id,
                access_token=self.attachments._generate_access_token(),
            )
        )

    def _get_attachment_copy(self, attachment_id, link, kind, task):
        attachment = self.attachments.browse(attachment_id).exists()
        if not (
            self._is_attachment_of_other_task(attachment, task)
            and self._is_readable(attachment, get_access_token(link))
        ):
            return None
        key = (task.id, attachment.id, kind)
        if key not in self._attachment_copies:
            if kind == FILE:
                # a file link downloads the file as uploaded: core would resize an image
                attachment = attachment.with_context(image_no_postprocess=True)
            raw = attachment.raw
            # a file gone reads as empty through the storage, as in _has_contents: no empty copy for the text to link
            values = raw and self._get_checked_contents(
                attachment,
                {"raw": raw, "mimetype": attachment.mimetype},
                task,
                "A link to attachment %s stays as it is" % attachment.id,
            )
            self._attachment_copies[key] = values and self._get_task_attachment(
                values, task, partial(self._copy_attachment, attachment, values, task)
            )
        return self._attachment_copies[key]

    def _copy_attachment(self, attachment, values, task):
        if self.dry_run:
            return True
        return attachment.copy(dict(values, res_id=task.id, access_token=self.attachments._generate_access_token()))

    def _get_task_attachment(self, values, task, create):
        """The attachment of task holding the contents of values, as core
        stores them: one the task has, given an access token if it has none,
        else create(). In a dry run, which creates nothing, True stands for an
        attachment create() counted, so that the same contents count once.
        """
        key = (task.id, values["mimetype"], self.attachments._compute_checksum(values["raw"]))
        if key not in self._task_attachments:
            self._task_attachments[key] = self._find_task_attachment(task, *key[1:]) or create()
        return self._task_attachments[key]

    def _find_task_attachment(self, task, mimetype, checksum):
        """The first attachment of task with these contents that may serve:
        not one of a message, which deleting the message deletes, nor one
        whose file is gone, and one the saving user may read, also under sudo
        (core saves a portal user's task in sudo), or the text would hand them
        the token and name of an attachment they cannot see.
        """
        candidates = self.attachments.search(
            [
                ("res_model", "=", "project.task"),
                ("res_id", "=", task.id),
                ("res_field", "=", False),
                ("mimetype", "=", mimetype),
                ("checksum", "=", checksum),
            ],
            order="id",
        )
        message_attachment_ids = self._get_message_attachment_ids(candidates)
        attachment = next(
            (
                candidate
                for candidate in candidates
                if candidate.id not in message_attachment_ids and self._has_contents(candidate)
            ),
            self.attachments,
        )
        if attachment and not self._can_read(attachment):
            return self.attachments
        if attachment and not attachment.access_token and not self.dry_run:
            attachment.access_token = self.attachments._generate_access_token()
        return attachment

    @staticmethod
    def _has_contents(attachment):
        """Whether the contents of attachment read as more than nothing,
        through the storage of attachments, which a module may keep elsewhere
        than the filestore: a file gone reads as empty.
        """
        if attachment.store_fname:
            return bool(attachment._file_read(attachment.store_fname))
        return bool(attachment.db_datas)

    def _get_message_attachment_ids(self, attachments):
        if not attachments:
            return set()
        self.env["mail.message"].flush_model(["attachment_ids"])
        self.env.cr.execute(
            "SELECT attachment_id FROM message_attachment_rel WHERE attachment_id IN %s", [tuple(attachments.ids)]
        )
        return {attachment_id for (attachment_id,) in self.env.cr.fetchall()}

    def _get_checked_contents(self, attachments, values, task, failure):
        """A copy of values as the create of an attachment makes them, or None
        where core fails to read the image: it reads any image that decodes,
        and a broken one raises whatever its reader meets. The text then stays
        as it is (failure says how, in the log) and the save passes. Checked
        before the create, not in a savepoint around it: a savepoint flushes,
        which runs the pending precommit hooks of the save (tracking) and
        would catch their errors.
        """
        try:
            return attachments._check_contents(dict(values))
        except (MemoryError, psycopg2.Error):
            raise
        except Exception as error:  # noqa: BLE001
            # the CPU time limit of a prefork worker is a plain Exception
            if type(error) is Exception:
                raise
            _logger.info("%s in a text of task %s: %r", failure, task.id, error)
            return None

    def _get_image_name(self, attributes, subtype, task):
        name = next((attributes[key].strip() for key in NAME_ATTRIBUTES if attributes.get(key, "").strip()), None)
        if name:
            return name
        self._fallback_name_counts[task.id] += 1
        return "image-%d.%s" % (self._fallback_name_counts[task.id], subtype.split("+")[0].lower())

    @staticmethod
    def _is_attachment_of_other_task(attachment, task):
        return (
            attachment.res_model == "project.task"
            and attachment.res_id
            and attachment.res_id != task.id
            and not attachment.res_field
        )

    def _is_readable(self, attachment, access_token):
        """Whether the user saving may see the attachment anyway: a copy must not
        hand anyone an image of a task they cannot read. In sudo only the
        attachment's own token counts: the text may come from anyone. A token
        that is not ASCII, the link's or the attachment's, matches nothing:
        consteq refuses to compare it, as it does for the server.
        """
        own_token = attachment.access_token or ""
        if access_token and access_token.isascii() and own_token.isascii() and consteq(own_token, access_token):
            return True
        return not self.env.su and self._can_read(attachment)

    def _can_read(self, attachment):
        """Whether the user saving may read attachment, as themself: their own
        rights, not those of a sudo the save runs in. The superuser, who runs
        scripts and crons, may.
        """
        if self.env.user._is_superuser():
            return True
        try:
            attachment.with_env(self.env(su=False)).check("read")
        except AccessError:
            return False
        return True


class ProjectTaskImageMixin(models.AbstractModel):
    """The images in the Html fields of a task, or of a line of a task (its
    task_id), and the files they link, are attachments of the task, served
    with an access token: an image pasted inline becomes one on create and
    write, and a new record, or a line moved to another task, that shows an
    image or links a file of another task gets a copy of it, which outlives
    that task. A write that links an image of another task leaves it linked.
    """

    _name = "project.task.image.mixin"
    _description = "Html Fields Keeping their Images on the Task"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._move_created_images(vals_list)
        return records

    def write(self, vals):
        former_image_tasks = self._get_former_image_tasks(vals)
        result = super().write(vals)
        self._move_written_images(vals, self._filter_image_task_changed(former_image_tasks))
        return result

    def _get_image_task(self):
        """The task that holds the images of this record as attachments."""
        return self.task_id

    @api.model
    def _get_image_task_fname(self):
        """The field whose write gives this record another image task, if any."""
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

    def _get_former_image_tasks(self, vals):
        """{record id: image task} when vals may give the records another one."""
        if self._get_image_task_fname() not in vals:
            return {}
        return {record.id: record._get_image_task() for record in self}

    def _filter_image_task_changed(self, former_image_tasks):
        return self.filtered(
            lambda record: record.id in former_image_tasks and record._get_image_task() != former_image_tasks[record.id]
        )

    def _move_written_images(self, vals, moved_records):
        # core write drops some keys silently (parent_path) and accepts any key on an empty recordset
        written_html_fnames = [fname for fname in vals if getattr(self._fields.get(fname), "type", None) == "html"]
        if written_html_fnames:
            image_fnames = [fname for fname in self._get_image_fields() if fname in written_html_fnames]
            (self - moved_records)._move_images(image_fnames, copy_links=False)
        if moved_records:
            # a line moved to another task copies the images of its old task, as a new line would
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
            task = record._get_image_task()
            texts = {}
            for fname in fnames:
                text = record[fname]
                new_text = text and mover.move(text, task)
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
