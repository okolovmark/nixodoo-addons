import math
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from .project_task_question import SEPARATING_TAGS, parse_html

BLOCK_TAGS = tuple(tag for tag in SEPARATING_TAGS if tag != "br") + (
    "body",
    "caption",
    "details",
    "html",
    "main",
    "nav",
    "summary",
)
SKIPPED_TAGS = ("head", "noscript", "script", "style", "template", "title")
EMPHASIS_MARKS = {"b": "**", "strong": "**", "i": "*", "em": "*", "del": "~~", "s": "~~", "strike": "~~"}
HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
LIST_TAGS = ("ol", "ul")
# CommonMark joins a list to a list right before it with the same marker: such a list takes the other one.
# The other bullet is not *, with which an item "**" would make a rule.
LIST_MARKERS = {"ul": ("-", "+"), "ol": (".", ")")}
CHECKLIST_CLASS = "o_checklist"
CHECKED_ITEM_CLASS = "o_checked"
CELL_TAGS = ("td", "th")
TABLE_PART_TAGS = ("thead", "tbody", "tfoot", "tr")
LINK_SCHEMES = ("", "http", "https", "mailto")
IMAGE_SCHEMES = ("", "http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
# merged cells pad a table with empty ones: a colspan is capped, so that a few bytes of HTML cannot make megabytes;
# a rowspan fills only the rows its row group has
MAX_SPAN = 100
MAX_TABLE_COLUMNS = 100
NOT_MOVED_IMAGE = "[image not yet moved to an attachment]"
WHITESPACE = re.compile(r"\s+")
REPEATED_SPACES = re.compile(r" {2,}")
LINE_BREAK_RUN = re.compile(r"\n{3,}")
BACKTICK_RUN = re.compile(r"`+")
# the id of an attachment as the editor writes it, at most ten digits: int() refuses over 4300
ATTACHMENT_ID = re.compile(r"[0-9]{1,10}")
HTML_WHITESPACE = "\t\n\f\r "
# an integer as HTML reads it past its leading ASCII white space: a sign, then the digits up to the first other one
HTML_INTEGER = re.compile(r"([-+]?)([0-9]+)")
# a CommonMark list number has nine digits at most
MAX_LIST_NUMBER = 999999999
# a line starting so is a list item, its number of nine digits at most: every other block escapes a line that would
LIST_START = re.compile(r" {0,3}(?:([-+*])|[0-9]{1,9}([.)])) ")
THEMATIC_BREAK = "---"
# "- ---" is a rule, not an item holding one
BULLET_ITEM_BREAK = "* * *"
# how a line of a heading, quote, table or code block written here starts; a paragraph has such a start escaped
OTHER_BLOCK_LINE = re.compile(r"#{1,6} |>|\||`{3,}$")
# The block before the first block of a list item, as get_blocks passes it on: unlike None, the start of a text, it
# stands for the item's marker, which indents what follows it on its line.
ITEM_START = ""
TASK_LIST_BOX = re.compile(r"\[[ xX]\](?:\s|$)")
# A heading, quote, list item, code fence of three or more, rule, setext underline, table row, table
# delimiter row or HTML block (a tag, comment or declaration) when a line starts with it. A backtick run with a
# backtick after it is a code span, not a fence.
BLOCK_MARKUP = re.compile(
    r"#+(?:\s|$)|>|[-+*](?:\s|$)|~{3}|`{3,}[^`]*$|[-=]+\s*$|([*_])\s*(?:\1\s*){2,}$|<[A-Za-z/!?]|\|"
    r"|[\s:-]*-[\s:-]*\|[\s|:-]*$"
)
ORDERED_LIST_NUMBER = re.compile(r"[0-9]{1,9}(?=[.)](?:\s|$))")
URL_TAB_OR_NEWLINE = re.compile(r"[\t\n\r]")
URL_EDGE_CHARACTERS = "".join(map(chr, range(0x21)))
URL_CONTROL_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
URL_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*):")
URL_PARTS = re.compile(r"(?:(https?):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?", re.IGNORECASE)
URL_USERINFO = re.compile(r"((?:(?<![\w+.-])[a-z][a-z0-9+.-]*:)?//)[^/?#\s]*@", re.IGNORECASE)
# In text only an http(s) url, git+https too, loses its user: "//@ts-ignore" or "ssh://git@host" are no secret.
HTTP_USERINFO = re.compile(r"((?<![\w+.-])(?:[a-z][a-z0-9.-]*\+)?https?://)[^/?#\s]*@", re.IGNORECASE)
LINK_TARGET_UNSAFE = re.compile(r"[\x00-\x20\x7f()<>]")
ATTACHMENT_PATH = re.compile(r"/web/(?:image|content)/(?:ir\.attachment/)?([0-9]{1,10})(?:[-/].*)?", re.DOTALL)
ATTACHMENT_ROUTE = re.compile(r"/web/(?:image|content)/?")
TOKEN_NAME = re.compile(r"token|secret|signature|passw|credential|api_?key|^(?:key|sig|auth)$")
PORTAL_SIGNATURE_NAMES = ("hash", "pid")
TOKEN_HINT = re.compile(
    r"token|secret|signature|passw|credential|api(?:_|%5f)?key|[?&#](?:key|sig|auth|hash)=|//[^/?#\s]*@", re.IGNORECASE
)
TEXT_SEGMENT = re.compile(r"[^\s()\[\]{}<>\"']+")
TEXT_WORD = re.compile(r"\S+")
# after a url segment of the same word, a segment right after one of these is the rest of that url, which a
# bracket or a quote in it split off; not a brace, which ends a placeholder of code: f"{url}?access_token={t}"
URL_SPLITTERS = (")", "]", "'", '"')
# What makes the address of a segment a url's: a scheme, a leading / or a host.
URL_ADDRESS = re.compile(r"(?<![\w+.-])[a-z][a-z0-9+.-]*:|^/|(?<![\w.-])[\w-]+(?:\.[\w-]+)+(?:[:/]|$)", re.IGNORECASE)


def html_to_markdown(html_content, attachment_names=None, heading_level=1, base_url=None):
    """html_content as Markdown, whitespace normalised and no markup left.

    An image or a link naming an attachment in any /web/image or /web/content
    url form, relative or on the host of base_url, becomes a pointer to it,
    ![image: <name>](attachment <id>) or [<text>](attachment <id>), the name
    taken from attachment_names {id: name} or else from the element; an inline
    data image NOT_MOVED_IMAGE. Any other url, and one that stands as a word of
    the text, keeps all but its query and fragment parameters named like a token
    and its user and password, in the text those of an http(s) url only; a token
    written loose in text or code stays as it is written. A link of a scheme
    other than http(s) or mailto keeps only its text. Text that would open a
    Markdown block at the start of a line is escaped. heading_level is the
    level an <h1> takes, so that a text sits under the headings around it.
    """
    if not html_content or not str(html_content).strip():
        return ""
    # None is a document without a body, which shows nothing
    root = parse_html(html_content)
    if root is None:
        return ""
    return "\n\n".join(_MarkdownWriter(attachment_names or {}, heading_level, base_url).get_blocks(root))


def get_linked_attachment_ids(html_content):
    """The ids of the attachments the images and links of html_content name, in any url form and on any host."""
    if not html_content or "/web" not in str(html_content):
        return set()
    root = parse_html(html_content)
    if root is None:
        return set()
    urls = [image.get("src") for image in root.iter("img")] + [link.get("href") for link in root.iter("a")]
    return {attachment_id for attachment_id in map(get_attachment_id, filter(None, urls)) if attachment_id}


def get_attachment_id(url):
    """The id of the attachment a /web/image or /web/content url names, relative or absolute on any host, else None."""
    _scheme, _authority, path, query = URL_PARTS.match(normalise_url(url)).groups()
    attachment_path = ATTACHMENT_PATH.fullmatch(path)
    if attachment_path:
        return int(attachment_path.group(1))
    if ATTACHMENT_ROUTE.fullmatch(path):
        parameters = {}
        for name, value in parse_qsl(query or ""):
            parameters.setdefault(name, value)
        attachment_id = parameters.get("id", "")
        if parameters.get("model", "ir.attachment") == "ir.attachment" and ATTACHMENT_ID.fullmatch(attachment_id):
            return int(attachment_id)
    return None


def strip_url_tokens(url):
    """url without its user and password and its query and fragment parameters named like a token, with the
    hash and pid that sign an Odoo portal link; the rest as it was.
    """
    return _strip_url_parameters(URL_USERINFO.sub(r"\1", url))


def _strip_url_parameters(url):
    address, hash_mark, fragment = url.partition("#")
    address, question_mark, query = address.partition("?")
    return address + _strip_parameters(question_mark, query) + _strip_parameters(hash_mark, fragment)


def _strip_parameters(separator, parameters):
    clean_parameters = _drop_token_parameters(parameters)
    # only a query or fragment emptied here loses its separator: "use?" or "# note" is no url
    return separator + clean_parameters if clean_parameters or not parameters else ""


def _drop_token_parameters(parameters):
    """parameters, joined by &, without those named like a token and without the hash and pid that sign
    a portal link: with a token, or both together where no token is; as they were where nothing goes.
    """
    parameter_list = parameters.split("&")
    names = [unquote(parameter.partition("=")[0]).lower() for parameter in parameter_list]
    is_token = [bool(TOKEN_NAME.search(name)) for name in names]
    if any(is_token) or set(PORTAL_SIGNATURE_NAMES) <= set(names):
        is_token = [is_name_token or name in PORTAL_SIGNATURE_NAMES for is_name_token, name in zip(is_token, names)]
    if not any(is_token):
        return parameters
    return "&".join(parameter for parameter, is_name_token in zip(parameter_list, is_token) if not is_name_token)


def _strip_tokens_in_text(text):
    """text without the tokens of its urls; a url is one only where it looks like one, so that "use?",
    "valide ? Oui" or a token=X of code stays as it was.
    """
    if not text or not TOKEN_HINT.search(text):
        return text
    return TEXT_WORD.sub(lambda word: _strip_tokens_in_word(word.group()), HTTP_USERINFO.sub(r"\1", text))


def _strip_tokens_in_word(word):
    """word without the tokens of its urls; a segment a bracket or a quote split off is the rest of a url
    only after a url in the same word, so that '"?access_token=" + token' of code stays as it was.
    """
    parts = []
    end = 0
    has_url = False
    for segment in TEXT_SEGMENT.finditer(word):
        is_url_rest = has_url and word[segment.start() - 1 : segment.start()] in URL_SPLITTERS
        parts += [word[end : segment.start()], _strip_tokens_in_segment(segment.group(), is_url_rest)]
        end = segment.end()
        has_url = has_url or bool(URL_ADDRESS.search(segment.group().partition("#")[0].partition("?")[0]))
    parts.append(word[end:])
    return "".join(parts)


def _strip_tokens_in_segment(segment, is_url_rest):
    address = segment.partition("#")[0].partition("?")[0]
    parameters = segment[len(address) :]
    # parameters joined by & make a query whatever its address, and so does the rest of a url a bracket or
    # a quote split off ("a%20(1).png?access_token=" gives ".png?access_token="), even after an &
    if "=" in parameters and ("&" in parameters or is_url_rest or URL_ADDRESS.search(address)):
        return _strip_url_parameters(segment)
    if is_url_rest and segment.startswith("&"):
        return _drop_token_parameters(segment)
    return segment


def _get_address(scheme, authority):
    """(host, port) of the authority of a url, the port None where it is the default of scheme; None where
    the authority names no host or cannot be read.
    """
    try:
        url_parts = urlsplit("//" + authority)
        port = url_parts.port
    except ValueError:
        return None
    if not url_parts.hostname:
        return None
    return url_parts.hostname, None if port == DEFAULT_PORTS.get(scheme.lower()) else port


def get_base_host(base_url):
    """(scheme, (host, port)) of base_url as get_local_url compares it, the (host, port) None where it names none."""
    scheme, authority = URL_PARTS.match(normalise_url(base_url)).group(1, 2)
    scheme = scheme or ""
    return scheme, _get_address(scheme, authority) if authority is not None else None


def get_local_url(url, base_host):
    """url, normalised, from its path on where it names this database, else None: a relative url, or one on
    the host of base_host (get_base_host) by any scheme, as Odoo picks the database by host. The url's port
    and base_host's must be equal, each counted as none where it is the default of its own scheme (of
    base_host's for a protocol-relative url), written or left out. Callers read http(s) and relative urls
    only.
    """
    url_parts = URL_PARTS.match(url)
    scheme, authority = url_parts.group(1, 2)
    if authority is not None:
        base_scheme, base_address = base_host
        address = _get_address(scheme or base_scheme, authority)
        if address is None or address != base_address:
            return None
    return url[url_parts.start(3) :]


def normalise_url(url):
    """url as a browser reads it: tabs and line breaks dropped, outer spaces and control characters stripped."""
    return URL_TAB_OR_NEWLINE.sub("", url or "").strip(URL_EDGE_CHARACTERS)


def _get_scheme(url):
    """The scheme of url, "" for a relative url; no space or control character can hide it."""
    scheme = URL_SCHEME.match(URL_CONTROL_CHARACTERS.sub("", url).lower())
    return scheme.group(1) if scheme else ""


def _is_block(element):
    return isinstance(element.tag, str) and element.tag in BLOCK_TAGS


def _has_class(element, class_name):
    return class_name in (element.get("class") or "").split()


def _is_list_only(element):
    """Whether element holds nothing but lists: the editor nests a list in an item of its own."""
    children = list(element)
    return (
        bool(children)
        and not (element.text or "").strip()
        and all(child.tag in LIST_TAGS and not (child.tail or "").strip() for child in children)
    )


def _normalise_space(text):
    return WHITESPACE.sub(" ", text or "")


def _escape_brackets(text):
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _escape_label(text):
    return _escape_brackets(" ".join(_strip_tokens_in_text(text).split()))


def _escape_line_start(line):
    ordered_list_number = ORDERED_LIST_NUMBER.match(line)
    if ordered_list_number:
        return line[: ordered_list_number.end()] + "\\" + line[ordered_list_number.end() :]
    return "\\" + line if BLOCK_MARKUP.match(line) else line


def _get_link_target(url):
    return LINK_TARGET_UNSAFE.sub(lambda character: "%%%02X" % ord(character.group()), url)


def _get_fence(text, minimum):
    return "`" * max([minimum] + [len(run) + 1 for run in BACKTICK_RUN.findall(text)])


def _emphasize(content, mark):
    """content between mark, its outer spaces kept outside: "** a **" is no emphasis."""
    stripped_content = content.strip()
    if not stripped_content:
        return content
    leading_space = content[: len(content) - len(content.lstrip())]
    trailing_space = content[len(content.rstrip()) :]
    return leading_space + mark + stripped_content + mark + trailing_space


def _get_code_span(text):
    code_text = _strip_tokens_in_text(_normalise_space(text))
    if not code_text.strip():
        return ""
    fence = _get_fence(code_text, 1)
    padding = " " if code_text.startswith("`") or code_text.endswith("`") else ""
    return fence + padding + code_text + padding + fence


def _get_preformatted_text(element):
    parts = [element.text or ""]
    for child in element:
        if isinstance(child.tag, str):
            parts.append("\n" if child.tag == "br" else _get_preformatted_text(child))
            if _is_block(child):
                parts.append("\n")
        parts.append(child.tail or "")
    return "".join(parts)


def _indent_lines(lines, indent):
    return [indent + line if line else line for line in lines]


def _join_lines(lines):
    return ["\n".join(lines)] if lines else []


def _get_last_line(lines):
    """The last of lines at their least indentation, where their outermost blocks stand, else "": a list may open
    with the lines of an item nested in one of its own (oe-nested), further in.
    """
    last_line, least_indent = "", math.inf
    for line in reversed(lines):
        indent = len(line) - len(line.lstrip(" "))
        if indent < least_indent and line.strip():
            last_line, least_indent = line, indent
            if not indent:
                break
    return last_line


def _ends_in_paragraph(block):
    """Whether block ends in a paragraph, at its least indentation: a list does where loose text of a list nested
    at its level ends it.
    """
    last_line = _get_last_line((block or "").split("\n"))
    return bool(last_line) and not (
        LIST_START.match(last_line) or OTHER_BLOCK_LINE.match(last_line) or last_line == THEMATIC_BREAK
    )


def _opens_list_not_from_one(block):
    """Whether block opens with an ordered list numbered from other than 1, which CommonMark reads as text of
    a paragraph right above it.
    """
    list_start = LIST_START.match(block)
    # the number before the delimiter, "" for a bullet
    return bool(list_start) and list_start.group().strip()[:-1] not in ("", "1")


def _is_under(block, previous_block):
    """Whether block goes right under previous_block: a list does, but for an ordered one not from 1 under a
    paragraph; any other block goes a paragraph apart.
    """
    return bool(LIST_START.match(block)) and not (
        _opens_list_not_from_one(block) and _ends_in_paragraph(previous_block)
    )


def _join_item_blocks(blocks):
    parts = blocks[:1]
    for block in blocks[1:]:
        parts.extend(("\n" if _is_under(block, parts[-1]) else "\n\n", block))
    return "".join(parts)


def _get_list_marker(tag, previous_marker):
    """The marker character of a list of tag right after a list of previous_marker, which is None where no list
    is right before it.
    """
    default_marker, other_marker = LIST_MARKERS[tag]
    return other_marker if previous_marker == default_marker else default_marker


def _get_last_list_marker(lines):
    """The marker character of the list lines end in, else None."""
    list_start = LIST_START.match(_get_last_line(lines))
    return list_start and (list_start.group(1) or list_start.group(2))


def _get_html_integer(value):
    """The integer value gives as HTML reads it, else None: one of ten digits or more, past any list number or
    table, is infinite.
    """
    integer = HTML_INTEGER.match((value or "").lstrip(HTML_WHITESPACE))
    if not integer:
        return None
    digits = integer.group(2).lstrip("0")
    size = math.inf if len(digits) > 9 else int(digits or 0)
    return -size if integer.group(1) == "-" else size


def _get_list_start(element):
    """The number of the first item of the list as HTML reads its start, as near as CommonMark writes it: 0 for
    a negative one, which it has no way to write; 1 where it gives none or one of ten digits or more.
    """
    start = _get_html_integer(element.get("start"))
    if start is None or abs(start) == math.inf:
        return 1
    return max(start, 0)


def _get_span(cell, attribute, max_span, zero_span=1):
    """The span attribute of cell gives as HTML reads it, at most max_span: 1 where it gives none or one below 0,
    zero_span where it gives 0.
    """
    span = _get_html_integer(cell.get(attribute))
    if span is None or span < 0:
        return 1
    return min(span, max_span) or zero_span


def _get_table_row(cells):
    return "| %s |" % " | ".join(cells)


class _MarkdownWriter:
    def __init__(self, attachment_names, heading_level, base_url):
        self.attachment_names = attachment_names
        self.heading_level = heading_level
        self.base_host = get_base_host(base_url)
        self.link_depth = 0
        self.cell_depth = 0

    def get_blocks(self, element, get_child_blocks=None, previous_block=None):
        """The Markdown blocks of what element holds: a paragraph for each run
        of text and inline elements, the blocks of each block element as
        get_child_blocks(child, the block before it) gives them. previous_block
        is the block before the first, in the container the blocks go to.
        """
        blocks = []
        inline_parts = [self._get_text(element.text)]
        for child in element:
            if _is_block(child):
                blocks.extend(self._get_paragraph(inline_parts))
                inline_parts = []
                child_blocks = (get_child_blocks or self._get_element_blocks)(child, (blocks or [previous_block])[-1])
                blocks.extend(child_blocks)
            else:
                inline_parts.append(self._get_inline(child))
            inline_parts.append(self._get_text(child.tail))
        blocks.extend(self._get_paragraph(inline_parts))
        return blocks

    def _get_text(self, text):
        clean_text = _strip_tokens_in_text(_normalise_space(text))
        return _escape_brackets(clean_text) if self.link_depth else clean_text

    def _get_paragraph(self, inline_parts):
        lines = [REPEATED_SPACES.sub(" ", line).strip() for line in "".join(inline_parts).split("\n")]
        # a cell is one line inside its row, where nothing opens a block
        if not self.cell_depth:
            lines = [_escape_line_start(line) for line in lines]
        # a run of line breaks shows as one empty line
        paragraph = LINE_BREAK_RUN.sub("\n\n", "\n".join(lines)).strip("\n")
        return [paragraph] if paragraph else []

    def _get_line(self, element):
        return " ".join(" ".join(self.get_blocks(element)).split())

    def _get_element_blocks(self, element, previous_block=None):
        tag = element.tag
        if tag in HEADING_LEVELS:
            heading_text = " ".join(self._get_inline_content(element).split())
            level = min(HEADING_LEVELS[tag] + self.heading_level - 1, 6)
            return ["#" * level + " " + heading_text] if heading_text else []
        if tag in LIST_TAGS:
            return self._get_list(element, previous_block)
        if tag == "table":
            return self._get_table(element, previous_block)
        if tag == "pre":
            return self._get_code_block(element)
        if tag == "blockquote":
            quoted_text = "\n\n".join(self.get_blocks(element))
            return ["\n".join(("> " + line).rstrip() for line in quoted_text.split("\n"))] if quoted_text else []
        if tag == "hr":
            return [THEMATIC_BREAK]
        return self.get_blocks(element, previous_block=previous_block)

    def _get_inline(self, element):
        tag = element.tag
        if not isinstance(tag, str) or tag in SKIPPED_TAGS:
            return ""
        if tag == "br":
            return "\n"
        if tag == "img":
            return self._get_image(element)
        if tag == "code":
            return _get_code_span(element.text_content())
        if tag == "a":
            return self._get_link(element)
        content = self._get_inline_content(element)
        if tag in EMPHASIS_MARKS:
            return _emphasize(content, EMPHASIS_MARKS[tag])
        # a block inside an inline element still keeps its words apart from those around it
        return " %s " % content if tag in BLOCK_TAGS else content

    def _get_inline_content(self, element):
        parts = [self._get_text(element.text)]
        for child in element:
            parts.append(self._get_inline(child))
            parts.append(self._get_text(child.tail))
        return "".join(parts)

    def _get_local_attachment_id(self, url):
        """The id of the attachment url names in this database: by a relative url or one on the host of base_url."""
        if get_local_url(url, self.base_host) is None:
            return None
        return get_attachment_id(url)

    def _get_attachment_pointer(self, kind, attachment_id, element_name):
        name = " ".join((self.attachment_names.get(attachment_id) or element_name or "").split())
        label = "%s: %s" % (kind, _escape_label(name)) if name else kind
        return "[%s](attachment %d)" % (label, attachment_id)

    def _get_image(self, element):
        source = normalise_url(element.get("src"))
        if not source:
            return ""
        scheme = _get_scheme(source)
        if scheme == "data":
            return NOT_MOVED_IMAGE
        alt_text = element.get("alt") or ""
        if scheme not in IMAGE_SCHEMES:
            return _escape_label(alt_text)
        attachment_id = self._get_local_attachment_id(source)
        if attachment_id:
            return "!" + self._get_attachment_pointer("image", attachment_id, element.get("title") or alt_text)
        return "![%s](%s)" % (_escape_label(alt_text), _get_link_target(strip_url_tokens(source)))

    def _get_link(self, element):
        self.link_depth += 1
        content = self._get_inline_content(element)
        self.link_depth -= 1
        url = normalise_url(element.get("href"))
        if not url or _get_scheme(url) not in LINK_SCHEMES:
            return content
        clean_url = strip_url_tokens(url)
        link_text = content.strip()
        is_bare = not link_text or link_text in (url, clean_url)
        attachment_id = self._get_local_attachment_id(url)
        if attachment_id:
            if is_bare:
                return self._get_attachment_pointer("file", attachment_id, element.get("title"))
            return "[%s](attachment %d)" % (link_text, attachment_id)
        if is_bare:
            return _get_link_target(clean_url)
        return "[%s](%s)" % (link_text, _get_link_target(clean_url))

    def _get_list(self, element, previous_block=None, is_opening=False):
        """The list as Markdown, an editor's checklist as a task list. Text and
        rules right inside it stand between its parts, after which it goes on as
        at the start of a text. A list right after a list of its marker takes the
        other marker of LIST_MARKERS. Nested lines before its first item stand
        two spaces in, which CommonMark reads at its level; none where it opens a
        list (is_opening) or an item (ITEM_START), which indents them, or follows
        a list, under whose last item they would go.
        """
        is_ordered = element.tag == "ol"
        is_checklist = _has_class(element, CHECKLIST_CLASS)
        previous_marker = _get_last_list_marker((previous_block or "").split("\n"))
        marker_character = _get_list_marker(element.tag, previous_marker)
        number = _get_list_start(element)
        is_opening = is_opening or previous_block == ITEM_START
        blocks = []
        # the lines since the start of the list or the last paragraph or rule right inside it
        lines = []
        # the indent of the content of the last item of lines, None before their first item
        indent = None
        # the last block under the last item, which a nested list follows in the same container
        item_last_block = None
        is_after_list = previous_marker is not None
        inline_parts = [self._get_text(element.text)]
        for child in element:
            if not _is_block(child):
                inline_parts.extend((self._get_inline(child), self._get_text(child.tail)))
                continue
            is_nested_list = child.tag in LIST_TAGS or _is_list_only(child)
            item_blocks = [] if is_nested_list else self._get_element_blocks(child, ITEM_START)
            # a browser numbers items only: a rule in another block is a rule between the list's parts
            is_rule = child.tag != "li" and item_blocks == [THEMATIC_BREAK]
            loose_blocks = self._get_paragraph(inline_parts) + ([THEMATIC_BREAK] if is_rule else [])
            inline_parts = [self._get_text(child.tail)]
            if loose_blocks:
                blocks.extend(_join_lines(lines) + loose_blocks)
                lines = []
                indent = item_last_block = None
                is_after_list = False
                marker_character = _get_list_marker(element.tag, None)
            if is_rule:
                continue
            if is_nested_list:
                nested_previous_block = previous_block if is_after_list and not lines else item_last_block
                nested_blocks = []
                for nested_list in [child] if child.tag in LIST_TAGS else child:
                    nested_blocks += self._get_list(nested_list, nested_previous_block, indent is None)
                    nested_previous_block = (nested_blocks or [nested_previous_block])[-1]
                if nested_blocks:
                    nested_lines = _join_item_blocks(nested_blocks).split("\n")
                    if lines and not _is_under(nested_lines[0], item_last_block):
                        lines.append("")
                    if indent is not None:
                        nested_lines = _indent_lines(nested_lines, indent)
                    elif not (is_opening or is_after_list):
                        nested_lines = _indent_lines(nested_lines, "  ")
                    lines.extend(nested_lines)
                    item_last_block = nested_blocks[-1]
                continue
            if not item_blocks:
                continue
            # the first item right under an ordered list of its marker would go on with its numbers
            if is_ordered and indent is None and _get_last_list_marker(lines) == marker_character:
                marker_character = _get_list_marker(element.tag, marker_character)
            if is_ordered:
                marker = "%d%s " % (min(number, MAX_LIST_NUMBER), marker_character)
            else:
                marker = marker_character + " "
            # the first only: a later item goes on with the list, whatever the item above it ends with
            if indent is None and lines and not _is_under(marker, item_last_block):
                lines.append("")
            item_last_block = item_blocks[-1]
            indent = " " * len(marker)
            item_lines = _join_item_blocks(item_blocks).split("\n")
            # the box is part of the item's text: its nested lists stay under the list marker
            if is_checklist and child.tag == "li":
                box = "[x]" if _has_class(child, CHECKED_ITEM_CLASS) else "[ ]"
                if BLOCK_MARKUP.match(item_lines[0]) or LIST_START.match(item_lines[0]):
                    # a blank line ends the box's paragraph: a rule would underline it, a list join it
                    item_lines[0:0] = [box, ""]
                else:
                    item_lines[0] = "%s %s" % (box, item_lines[0])
            elif TASK_LIST_BOX.match(item_lines[0]):
                item_lines[0] = "\\" + item_lines[0]
            # an item's rule only: one in another block right in the list stays a rule between its items
            elif not is_ordered and child.tag == "li" and item_lines[0] == THEMATIC_BREAK:
                item_lines[0] = BULLET_ITEM_BREAK
            lines.append(marker + item_lines[0])
            lines.extend(_indent_lines(item_lines[1:], indent))
            number += 1
        return blocks + _join_lines(lines) + self._get_paragraph(inline_parts)

    def _get_table(self, element, previous_block=None):
        """What the table holds outside its cells, its caption, then the table."""
        blocks = self.get_blocks(element, self._get_loose_table_blocks, previous_block)
        caption = next((child for child in element if child.tag == "caption"), None)
        caption_line = self._get_line(caption) if caption is not None else ""
        if caption_line:
            blocks.append(caption_line)
        rows = self._get_table_rows(element)
        if rows:
            width = max(len(cells) for cells in rows)
            rows = [cells + [""] * (width - len(cells)) for cells in rows]
            lines = [_get_table_row(rows[0]), _get_table_row(["---"] * width)]
            lines.extend(_get_table_row(cells) for cells in rows[1:])
            blocks.append("\n".join(lines))
        return blocks

    def _get_loose_table_blocks(self, element, previous_block=None):
        """The blocks of what a table holds outside its caption and cells, which a browser shows before it."""
        if element.tag in TABLE_PART_TAGS:
            return self.get_blocks(element, self._get_loose_table_blocks, previous_block)
        if element.tag in CELL_TAGS or element.tag == "caption":
            return []
        return self._get_element_blocks(element, previous_block)

    def _get_table_rows(self, element):
        """The cells of each row of the table, a cell that spans columns or rows
        followed or underlaid by empty ones, so that every row keeps its columns.
        A cell spans rows to the end of its row group at most (its thead, tbody
        or tfoot), to that end with a rowspan of zero, as a browser shows it.
        The table is at most MAX_TABLE_COLUMNS wide: the text of a cell beyond
        joins the last column.
        """
        rows = []
        spanned_rows = {}
        row_group = None
        for row in element.iter("tr"):
            if next(row.iterancestors("table"), None) is not element:
                continue
            if row.getparent() is not row_group:
                row_group = row.getparent()
                spanned_rows = {}
            covered_columns = set(spanned_rows)
            new_spans = {}
            cells = []
            for cell in row:
                if not (isinstance(cell.tag, str) and cell.tag in CELL_TAGS):
                    continue
                while len(cells) in covered_columns:
                    cells.append("")
                rowspan = _get_span(cell, "rowspan", math.inf, zero_span=math.inf)
                cell_text = self._get_cell(cell)
                if len(cells) >= MAX_TABLE_COLUMNS:
                    cells[-1] = " ".join(filter(None, (cells[-1], cell_text)))
                    continue
                for offset in range(min(_get_span(cell, "colspan", MAX_SPAN), MAX_TABLE_COLUMNS - len(cells))):
                    if rowspan > 1:
                        new_spans[len(cells)] = rowspan - 1
                    cells.append("" if offset else cell_text)
            cells.extend([""] * (max(covered_columns, default=-1) + 1 - len(cells)))
            spanned_rows = {column: count - 1 for column, count in spanned_rows.items() if count > 1}
            spanned_rows.update(new_spans)
            if cells:
                rows.append(cells)
        return rows

    def _get_cell(self, cell):
        self.cell_depth += 1
        cell_line = self._get_line(cell)
        self.cell_depth -= 1
        return cell_line.replace("|", "\\|")

    def _get_code_block(self, element):
        code_text = _strip_tokens_in_text(_get_preformatted_text(element)).strip("\n")
        if not code_text.strip():
            return []
        fence = _get_fence(code_text, 3)
        return ["%s\n%s\n%s" % (fence, code_text, fence)]
