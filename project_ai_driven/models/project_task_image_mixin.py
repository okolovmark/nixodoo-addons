import base64
import binascii
import bisect
import hashlib
import html
import logging
import re
from collections import Counter, defaultdict
from functools import partial
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import psycopg2

from odoo import api, models
from odoo.tools import consteq
from odoo.tools.misc import clean_context

_logger = logging.getLogger(__name__)

INLINE_IMAGE_MARKER = re.compile(r"data:image", re.IGNORECASE)
INLINE_IMAGE_OR_LINK_MARKER = re.compile(r"data:image|/web/image/|/web/content/", re.IGNORECASE)
# A comment is matched first and left whole, an unclosed one to the end. A tag's name holds no quote and ends
# where an attribute may start, and no unquoted part runs over a "<" (lxml quotes every value): otherwise a
# text of "<a<a<a..." or of '<a" "<a" "...' backtracks.
START_TAG_OR_COMMENT = re.compile(
    r"""<!--.*?(?:-->|\Z)|<([A-Za-z][^\s/<>"']*)(?=[\s/>])(?:[^<>"']|"[^"]*"|'[^']*')*>""", re.DOTALL
)
TAG_ATTRIBUTE = re.compile(r"""\s([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
# the pattern of html.unescape: references unescaped one by one give the value it gives
CHARACTER_REFERENCE = re.compile(r"&(?:#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)")
DECIMAL_REFERENCE = re.compile(r"&#([0-9]+);?")
# the digits of 0x10FFFF, the last code point: html.unescape gives U+FFFD for a number past it
CODE_POINT_DIGITS = 7
REPLACEMENT_CHARACTER = "\ufffd"
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
ATTACHMENT_ID = re.compile(r"[0-9]{1,10}")
# werkzeug's request.args decode a query so: invalid UTF-8 stays percent-encoded
QUERY_DECODING_ERRORS = "werkzeug.url_quote"
# The editor names a pasted file data-file-name, core mail data-filename.
NAME_ATTRIBUTES = ("data-filename", "data-file-name", "alt")
DRY_RUN_SOURCE = "/web/image/0"
IMAGE = "image"
FILE = "file"
LINK_PATTERNS = {IMAGE: IMAGE_LINK, FILE: FILE_LINK}
# Pre-filters on the fields a write names, before the image fields are listed: only an Html or Properties
# field saves a text, and only a many2one gives a record another container of its properties.
TEXT_FIELD_TYPES_PREFILTER = ("html", "properties")
CONTAINER_FIELD_TYPE_PREFILTER = "many2one"
# core names an html property so, and sanitizes the value of no other
HTML_PROPERTY_SUFFIX = "_html"


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
# the box the editor of Odoo 20 puts around a file link (renderStaticFileBox)
FILE_BOX_CLASS = "o_file_box"
# The editor of Odoo 20 names there the attachment an image was cropped or converted from: its crop starts
# from it and saves through /html_editor/modify_image/<data-original-id>.
ORIGINAL_ATTRIBUTES = ("data-original-id", "data-original-src", "data-mimetype-before-conversion")
FILE_BOX_ATTRIBUTES = {(None, "data-attachment-id"): (read_attribute_url, FILE)}


def unescape_reference(reference):
    """html.unescape(reference) of one character reference, without the int()
    it runs on a decimal number first: Python refuses one of more than 4300
    digits, leading zeros counted, and a stored text may hold one.
    """
    decimal_reference = DECIMAL_REFERENCE.fullmatch(reference)
    if not decimal_reference:
        return html.unescape(reference)
    digits = decimal_reference.group(1).lstrip("0")
    if len(digits) > CODE_POINT_DIGITS:
        return REPLACEMENT_CHARACTER
    return html.unescape("&#%s;" % (digits or "0"))


def unescape_value(raw_value):
    """html.unescape(raw_value), reference by reference: see unescape_reference."""
    return CHARACTER_REFERENCE.sub(lambda reference: unescape_reference(reference.group()), raw_value)


def unescape_with_spans(raw_value):
    """raw_value html-unescaped, and the function giving the span in raw_value
    of a span of the unescaped value: a character reference maps as a whole.
    """
    references = []
    value_parts = []
    raw_position = value_length = 0
    for reference in CHARACTER_REFERENCE.finditer(raw_value):
        text = unescape_reference(reference.group())
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


def replace_urls(html_text, get_new_url, url_attributes=URL_ATTRIBUTES, get_attribute_edits=None):
    """html_text with the urls of url_attributes replaced where
    get_new_url(url, kind=kind, attributes=attributes) gives a new one, kind
    IMAGE or FILE, the url and the attributes html-unescaped. In a tag whose
    urls change, get_attribute_edits(tag_name, attributes, new_urls), new_urls
    {(attribute name, url): new url}, gives its other attributes to change,
    {name: new value, or None to drop it}; one the tag lacks stays absent.
    Every other character stays as it is, comments whole: a parser would
    serialise the whole text again.
    """

    def replace_tag(tag_match):
        tag = tag_match.group(0)
        if tag_match.group(1) is None:
            return tag
        tag_name = tag_match.group(1).lower()
        attributes = {}
        url_values = []
        # (start, end of the name, end) of each attribute without urls, by name, duplicates included
        other_spans = defaultdict(list)
        for attribute in TAG_ATTRIBUTE.finditer(tag, tag_match.end(1) - tag_match.start()):
            name = attribute.group(1).lower()
            value_group = next((group for group in (2, 3, 4) if attribute.group(group) is not None), None)
            url_reader = url_attributes.get((tag_name, name)) or url_attributes.get((None, name))
            if not url_reader:
                other_spans[name].append((attribute.start(), attribute.end(1), attribute.end()))
            # a browser reads the first of two attributes of a name
            if value_group is None or name in attributes:
                continue
            raw_value = attribute.group(value_group)
            if url_reader:
                unescaped_value = unescape_with_spans(raw_value)
                url_values.append((name, attribute.span(value_group), unescaped_value, *url_reader))
                attributes[name] = unescaped_value[0]
            else:
                attributes[name] = unescape_value(raw_value)
        new_urls = {}
        replacements = []
        for name, (start, end), unescaped_value, read_urls, kind in url_values:

            def get_value_url(url, name=name, kind=kind):
                new_url = get_new_url(url, kind=kind, attributes=attributes)
                if new_url:
                    new_urls[(name, url)] = new_url
                return new_url

            replacements.append(
                (start, end, replace_value_urls(tag[start:end], read_urls, get_value_url, unescaped_value))
            )
        if new_urls and get_attribute_edits:
            for name, new_value in get_attribute_edits(tag_name, attributes, new_urls).items():
                for index, (start, name_end, end) in enumerate(other_spans.get(name, ())):
                    is_kept = new_value is not None and not index
                    replacements.append(
                        (start, end, tag[start:name_end] + '="%s"' % html.escape(new_value) if is_kept else "")
                    )
        new_parts = []
        tag_position = 0
        for start, end, new_text in sorted(replacements):
            new_parts += [tag[tag_position:start], new_text]
            tag_position = end
        return "".join(new_parts) + tag[tag_position:]

    return START_TAG_OR_COMMENT.sub(replace_tag, html_text)


def replace_image_sources(html_text, get_new_source):
    """html_text with the src of each <img> replaced where
    get_new_source(src, attributes) gives a new one: replace_urls for it alone.
    """
    return replace_urls(
        html_text,
        lambda url, kind, attributes: get_new_source(url, attributes),
        {("img", "src"): URL_ATTRIBUTES[("img", "src")]},
    )


def rename_file_boxes(html_text, file_copies):
    """html_text with each file box of the editor naming the copy of its
    attachment in file_copies {attachment id: copy id}: the box opens the
    attachment it names, without a token. Every other character stays.
    """

    def get_new_id(attachment_id, kind, attributes):
        if FILE_BOX_CLASS in attributes.get("class", "").split():
            return file_copies.get(attachment_id)
        return None

    return replace_urls(html_text, get_new_id, FILE_BOX_ATTRIBUTES)


def get_image_source(attachment):
    return "/web/image/%s?access_token=%s" % (attachment.id, attachment.access_token)


def decode_query_part(part):
    """A name or a value of a query as the server decodes it (werkzeug's request.args)."""
    return unquote_plus(part, errors=QUERY_DECODING_ERRORS)


def get_access_token(link):
    """The access token link carries, read as the server reads its query:
    the first parameter whose name decodes to access_token.
    """
    for pair in urlsplit(link).query.split("&"):
        name, _separator, value = pair.partition("=")
        if decode_query_part(name) == "access_token":
            return decode_query_part(value)
    return None


def get_file_link(link, attachment):
    """link, a /web/content/<id> link of this database, pointed at attachment:
    its path, query and fragment as they are but for the id, and for the
    access token, added last in place of every parameter whose name decodes
    to access_token, as the server decodes it.
    """
    parts = urlsplit(link)
    query_pairs = [
        pair for pair in parts.query.split("&") if pair and decode_query_part(pair.partition("=")[0]) != "access_token"
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
    """Gives the images of Html texts to the record holding the text as its
    attachments, as the html editor and mail do: an inline image becomes an
    attachment and, with copy_links, an image or a file link of another
    record of the image mixin a copy of its attachment. An attachment the
    record has with the same contents, not one of a message nor one whose file
    is gone, serves instead of a new one; another record never shares it.

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
        self._record_attachments = {}
        self._fallback_name_counts = Counter()
        self._base_hosts = {}

    def move(self, text, record):
        """text with its images, and with copy_links its file links, changed to attachments of record, the text
        as it is without any. A file box of the editor names the attachment its link opens then, and a copied
        image the copy of its original (ORIGINAL_ATTRIBUTES).
        """
        if not self.marker.search(text):
            return text
        file_copies = {}
        new_text = replace_urls(
            text,
            lambda url, kind, attributes: self._get_new_url(url, kind, attributes, record, file_copies),
            get_attribute_edits=partial(self._get_original_edits, record=record),
        )
        return rename_file_boxes(new_text, file_copies) if file_copies else new_text

    def _get_new_url(self, url, kind, attributes, record, file_copies):
        """The new url, None where url stays; a file link copied adds {source id: copy id} to file_copies."""
        link = None
        inline_image = kind == IMAGE and INLINE_IMAGE.fullmatch(url)
        if inline_image:
            attachment = self._get_inline_image_attachment(inline_image, attributes, record)
        else:
            link = self.copy_links and self._get_local_link(url, record)
            link_match = link and LINK_PATTERNS[kind].fullmatch(link)
            attachment = link_match and self._get_attachment_copy(
                self.attachments.browse(int(link_match.group(1))).exists(), get_access_token(link), kind, record
            )
        if not attachment:
            return None
        if self.dry_run:
            return DRY_RUN_SOURCE
        if kind == IMAGE:
            return get_image_source(attachment)
        file_copies[str(int(link_match.group(1)))] = str(attachment.id)
        return get_file_link(link, attachment)

    def _get_local_link(self, url, record):
        """url from its path on, None for a link to another host: an absolute
        link is local on the host the brief reads the texts of record with.
        """
        # not at the top: html_markdown imports project_task_question, whose model must load after this mixin
        from .html_markdown import get_base_host, get_local_url, normalise_url  # noqa: PLC0415

        key = (record._name, record.id)
        if key not in self._base_hosts:
            self._base_hosts[key] = get_base_host(record._get_image_base_url())
        return get_local_url(normalise_url(url), self._base_hosts[key])

    def _get_original_edits(self, tag_name, attributes, new_urls, record):
        """The ORIGINAL_ATTRIBUTES of an <img> whose src is copied from another
        record: renamed to the copy of the original, where it is an attachment
        of that record the saver may read too, else dropped, so that the crop
        starts from the copy. The original is read as the image is, by its own
        token in data-original-src, or by the token of the src when it is the
        image itself.
        """
        source = attributes.get("src")
        if tag_name != "img" or ("src", source) not in new_urls or not self.copy_links or self.dry_run:
            return {}
        link = self._get_local_link(source, record)
        image_link = link and IMAGE_LINK.fullmatch(link)
        if not image_link:
            return {}
        image = self.attachments.browse(int(image_link.group(1)))
        original_id = ATTACHMENT_ID.fullmatch(attributes.get("data-original-id", ""))
        original = self.attachments.browse(int(original_id.group()) if original_id else ()).exists()
        original_copy = None
        if original and (original.res_model, original.res_id) == (image.res_model, image.res_id):
            access_token = get_access_token(link if original == image else attributes.get("data-original-src", ""))
            original_copy = self._get_attachment_copy(original, access_token, IMAGE, record)
        if not original_copy:
            return dict.fromkeys(ORIGINAL_ATTRIBUTES)
        return {"data-original-id": str(original_copy.id), "data-original-src": get_image_source(original_copy)}

    def _get_inline_image_attachment(self, inline_image, attributes, record):
        mimetype, subtype, data = inline_image.groups()
        data = "".join(data.split())
        # a digest, not the data: a run over many records would keep every image in memory
        key = (record._name, record.id, hashlib.sha1(data.encode()).hexdigest())
        if key not in self._inline_attachments:
            self._inline_attachments[key] = self._attach_inline_image(data, mimetype, subtype, attributes, record)
        return self._inline_attachments[key]

    def _attach_inline_image(self, data, mimetype, subtype, attributes, record):
        try:
            raw = base64.b64decode(data, validate=True)
        except binascii.Error:
            return None
        if not raw:
            return None
        # core's attachment rules still apply: a user who may not edit views gets svg as text
        values = self._get_checked_contents(
            self.attachments, {"raw": raw, "mimetype": mimetype.lower()}, record, "An image stays inline"
        )
        if values is None:
            return None
        create = partial(self._create_image_attachment, values, len(raw), subtype, attributes, record)
        return self._get_record_attachment(values, record, create)

    def _create_image_attachment(self, values, size, subtype, attributes, record):
        self.image_count += 1
        self.byte_count += size
        if self.dry_run:
            return True
        return self._get_unprocessed_attachments().create(
            dict(
                values,
                name=self._get_image_name(attributes, subtype, record),
                res_model=record._name,
                res_id=record.id,
                access_token=self.attachments._generate_access_token(),
            )
        )

    def _get_attachment_copy(self, attachment, access_token, kind, record):
        if not (
            self._is_attachment_of_other_record(attachment, record) and self._is_readable(attachment, access_token)
        ):
            return None
        key = (record._name, record.id, attachment.id, kind)
        if key not in self._attachment_copies:
            self._attachment_copies[key] = self._copy_attachment(attachment, kind, record)
        return self._attachment_copies[key]

    def _copy_attachment(self, attachment, kind, record):
        # a file missing from the filestore reads empty: the link stays, as an empty inline image does
        if not self._has_contents(attachment):
            return None
        # a file link downloads the file as uploaded: core would resize an image
        attachments = self._get_unprocessed_attachments() if kind == FILE else self.attachments
        values = self._get_checked_contents(
            attachments,
            {"raw": attachment.raw, "mimetype": attachment.mimetype},
            record,
            "A link to attachment %s stays as it is" % attachment.id,
        )
        if values is None:
            return None
        return self._get_record_attachment(values, record, partial(self._create_copy, attachment, values, record))

    def _create_copy(self, attachment, values, record):
        """A copy of attachment for record, without the original core keeps of an image cropped: deleting the
        record of that original would delete the copy with it.
        """
        if self.dry_run:
            return True
        return attachment.with_context(image_no_postprocess=True).copy(
            dict(
                values,
                res_model=record._name,
                res_id=record.id,
                access_token=self.attachments._generate_access_token(),
                original_id=False,
            )
        )

    def _get_record_attachment(self, values, record, create):
        """The attachment of record holding the contents of values, as core
        stores them: one the record has, given an access token if it has none,
        else create(). In a dry run, which creates nothing, True stands for an
        attachment create() counted, so that the same contents count once.
        """
        key = (record._name, record.id, values["mimetype"], self.attachments._compute_checksum(values["raw"]))
        if key not in self._record_attachments:
            self._record_attachments[key] = self._find_record_attachment(record, *key[2:]) or create()
        return self._record_attachments[key]

    def _find_record_attachment(self, record, mimetype, checksum):
        """The first attachment of record with these contents that may serve:
        not one of a message, which deleting the message deletes, nor one
        whose file is gone from the filestore, which reads as empty, and one
        the saving user may read, also under sudo (core saves a portal user's
        task in sudo), or the text would hand them the token and name of an
        attachment they cannot see.
        """
        candidates = self.attachments.search(
            [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("res_field", "=", False),
                ("mimetype", "=", mimetype),
                ("checksum", "=", checksum),
            ],
            order="id",
        )
        for candidate in candidates - self._get_message_attachments(candidates):
            if self._can_read(candidate) and self._has_contents(candidate):
                if not self.dry_run:
                    candidate.generate_access_token()
                return candidate
        return self.attachments

    def _has_contents(self, attachment):
        """Whether attachment reads as more than nothing, through its storage
        (raw) and for itself alone: raw computed for a recordset reads every
        file of it.
        """
        return bool(attachment.with_prefetch().raw.size)

    def _get_message_attachments(self, attachments):
        if not attachments:
            return attachments
        messages = self.env["mail.message"].sudo().search([("attachment_ids", "in", attachments.ids)])
        return messages.attachment_ids & attachments

    def _get_checked_contents(self, attachments, values, record, failure):
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
            _logger.info("%s in a text of %s: %r", failure, record, error)
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

    def _is_readable(self, attachment, access_token):
        """Whether the user saving may see the attachment anyway: a copy must not
        hand anyone an image of a record they cannot read. In sudo only the
        attachment's own token counts: the text may come from anyone. A token
        that is not ASCII, the link's or the attachment's, counts for nothing:
        consteq raises on it, and so does the server's check of such a link.
        """
        own_token = attachment.access_token or ""
        if access_token and access_token.isascii() and own_token.isascii() and consteq(own_token, access_token):
            return True
        return not self.env.su and self._can_read(attachment)

    def _can_read(self, attachment):
        """Whether the user saving may read attachment, as themself: their own
        rights, not those of a sudo the save runs in. The superuser, who runs
        scripts and crons, may: its environment is always in sudo.
        """
        return attachment.with_env(self.env(su=False)).has_access("read")


class ProjectTaskImageMixin(models.AbstractModel):
    """The images in the Html fields of a task, a question row or a test case,
    and in the html properties of a task, are attachments of that record,
    served with an access token, as the html editor attaches the images it
    uploads: an image written inline becomes one on create and write, and a
    new record, or a line moved to another task, that shows an image or links
    a file of another such record gets a copy of it, which outlives that
    record. An attachment the record has with the same contents serves again.
    A write that links an image of another record leaves it linked: see ADR
    0011.
    """

    _name = "project.task.image.mixin"
    _description = "Html Fields Keeping their Images as Attachments"

    @api.model_create_multi
    def create(self, vals_list):
        """The inline images of the texts given become attachments of the new record, and the images and file
        links of other records it shows copies of theirs.
        """
        records = super().create(vals_list)
        records._move_created_images(vals_list)
        return records

    def write(self, vals):
        """The inline images of the texts written become attachments of the record; a record moved to another
        task also copies the images and file links of other records its texts show. A record whose container
        of its properties is written, or that follows one written, gets the images of the defaults core fills in.
        """
        former_tasks = self._get_former_tasks(vals)
        container_records = self._get_container_records(vals)
        result = super().write(vals)
        self._move_written_images(vals, self._filter_task_changed(former_tasks))
        self._move_default_property_images(container_records)
        return result

    @api.model
    def _get_task_fname(self):
        """The field whose write gives this record another task, if any."""
        return "task_id"

    def _get_image_base_url(self):
        """The base url the brief reads the texts of this record with: its task's (website gives a task the
        domain of its company's website), or its own. In sudo: the mover may run on a record its saver cannot read.
        """
        self.ensure_one()
        task_fname = self._get_task_fname()
        record = self.sudo()
        return (record[task_fname] if task_fname else record).get_base_url()

    @api.model
    def _get_image_fields(self):
        """The fields whose images are moved: the Html fields stored, not
        computed, and in one language (the column of a translated one holds
        every language), and the stored Properties fields, for their html
        properties.
        """
        return [
            fname
            for fname, field in self._fields.items()
            if (field.type == "html" and field.store and not field.compute and not field.translate)
            or (field.type == "properties" and field.store)
        ]

    def _get_marked_image_fnames(self, fnames, copy_links):
        """The fields of fnames whose text a mover may change on one of these
        records, read as stored (sanitized): a field the save named comes from
        the cache it just filled, without a query.
        """
        marker = get_image_marker(copy_links)
        return [
            fname
            for fname in fnames
            if any(marker.search(text) for record in self for text in record._get_image_texts(fname).values())
        ]

    def _get_image_texts(self, fname):
        """{key: text} of the texts of fname on this record: the text of an Html
        field under None, the values of the html properties of a Properties
        field under their names.
        """
        field = self._fields[fname]
        # in sudo: a create fills in property defaults its user may not read, as a portal user does
        stored_value = self.sudo()[fname]
        if field.type != "properties":
            return {None: stored_value} if stored_value else {}
        property_values = field.convert_to_cache(stored_value, self, validate=False) or {}
        return {
            name: value
            for name, value in property_values.items()
            if name.endswith(HTML_PROPERTY_SUFFIX) and isinstance(value, str) and value
        }

    def _get_moved_image_value(self, mover, fname):
        """The value of fname on this record with the images of its texts
        moved, None where no text changes: a Properties field gets all its
        values, those that are no html property as they are.
        """
        texts = self._get_image_texts(fname)
        new_texts = {key: mover.move(text, self) for key, text in texts.items()}
        if new_texts == texts:
            return None
        if self._fields[fname].type != "properties":
            return new_texts[None]
        return {**self._fields[fname].convert_to_cache(self.sudo()[fname], self, validate=False), **new_texts}

    def _move_created_images(self, vals_list):
        image_fnames = self._get_image_fields()
        # core fills a Properties field with the defaults of its container's html properties, whatever vals give
        fnames_list = [
            record._get_marked_image_fnames(
                [fname for fname in image_fnames if fname in vals or self._fields[fname].type == "properties"],
                copy_links=True,
            )
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
        image_fnames = self._get_written_image_fnames(vals)
        if image_fnames:
            (self - moved_records)._move_images(image_fnames, copy_links=False)
        if moved_records:
            # a line moved to another task copies the images of other records, as a new line would
            moved_records._move_images(self._get_image_fields(), copy_links=True)

    @api.model
    def _get_written_image_fnames(self, vals):
        """The image fields a write of vals names."""
        if not any(field.type in TEXT_FIELD_TYPES_PREFILTER for field in self._get_written_fields(vals)):
            return []
        return [fname for fname in self._get_image_fields() if fname in vals]

    @api.model
    def _get_written_fields(self, vals):
        # core write accepts any key on an empty recordset
        return [self._fields[fname] for fname in vals if fname in self._fields]

    @api.model
    def _get_properties_fnames(self):
        return [fname for fname in self._get_image_fields() if self._fields[fname].type == "properties"]

    def _get_container_records(self, vals):
        """The records, in sudo, whose Properties fields core recomputes after a write of
        vals: a write of their container, the same one included, or of one they follow.
        The recompute fills empty html properties with the defaults of the container.
        """
        if not any(field.type == CONTAINER_FIELD_TYPE_PREFILTER for field in self._get_written_fields(vals)):
            return self.browse()
        container_fnames = [self._fields[fname].definition_record for fname in self._get_properties_fnames()]
        if not container_fnames:
            return self.browse()
        return self._get_container_changing_records(vals, container_fnames).sudo()

    def _get_container_changing_records(self, vals, container_fnames):
        """The records whose containers of their properties vals may change: these, when vals name one."""
        return self if set(container_fnames) & vals.keys() else self.browse()

    def _move_default_property_images(self, records):
        """Move the images of the properties of these records, in sudo: a record that
        follows one written may be one the user cannot read. A text without an inline
        image is left as it is.
        """
        if records:
            records.sudo()._move_images(self._get_properties_fnames(), copy_links=False)

    def _move_images(self, image_fnames, copy_links):
        fnames = self._get_marked_image_fnames(image_fnames, copy_links)
        if fnames:
            self._store_texts(self._get_moved_image_texts(ImageMover(self.env, copy_links=copy_links), fnames))

    def _get_moved_image_texts(self, mover, fnames):
        """{record: {field name: new value}} for the records of self whose texts
        mover changes, the value of a Properties field its {property name:
        value}. The texts are read as stored: sanitized.
        """
        new_texts = {}
        for record in self:
            texts = {}
            for fname in fnames:
                new_value = record._get_moved_image_value(mover, fname)
                if new_value is not None:
                    texts[fname] = new_value
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
