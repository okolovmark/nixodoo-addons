import math
import re
from urllib.parse import parse_qsl, unquote, urlsplit

from lxml import etree

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
NESTED_ITEM_CLASS = "oe-nested"
CELL_TAGS = ("td", "th")
# blocks written in a form of their own; any other block only holds what it holds
FORMED_BLOCK_TAGS = tuple(HEADING_LEVELS) + LIST_TAGS + ("table", "pre", "blockquote", "hr")
ROW_GROUP_TAGS = ("thead", "tbody", "tfoot")
LINK_SCHEMES = ("", "http", "https", "mailto")
IMAGE_SCHEMES = ("", "http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
# a colspan pads its row with empty cells: capped, so that a few bytes of HTML cannot make megabytes; a rowspan
# pads only the rows its row group has
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
# a line starting so is a list item, its number of nine digits at most, an empty one where nothing follows the
# marker: every other block escapes a line that would
LIST_START = re.compile(r" {0,3}(?:([-+*])|[0-9]{1,9}([.)]))(?=[ \n]|$)")
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
# The label at the start of a paragraph, which may run over lines: group 1 is "]:" where it opens a link reference
# definition, which CommonMark shows nothing of, and "" or "\" where it is still open at the end of the text.
LINK_LABEL = re.compile(r"\[(?:[^\[\]\\]|\\.)*(\]:|\\?\Z)?", re.DOTALL)
# the quote and list markers, and a checklist box, before the paragraph a block of a caption line opens with
CAPTION_CONTAINER_MARKERS = re.compile(r"(?:(?:>|[-+](?: \[[ x]\])?|[0-9]{1,9}[.)]) )*")
URL_TAB_OR_NEWLINE = re.compile(r"[\t\n\r]")
URL_EDGE_CHARACTERS = "".join(map(chr, range(0x21)))
URL_CONTROL_CHARACTERS = re.compile(r"[\x00-\x20\x7f]")
URL_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*):")
URL_PARTS = re.compile(r"(?:(https?):)?(?://([^/?#]*))?([^?#]*)(?:\?([^#]*))?", re.IGNORECASE)
URL_USERINFO = re.compile(r"((?:(?<![\w+.-])[a-z][a-z0-9+.-]*:)?//)[^/?#\s]*@", re.IGNORECASE)
# In text only an http(s) url, git+https too, loses its user: "//@ts-ignore" or "ssh://git@host" are no secret.
HTTP_USERINFO = re.compile(r"((?<![\w+.-])(?:[a-z][a-z0-9.-]*\+)?https?://)[^/?#\s]*@", re.IGNORECASE)
# what ends a link destination, or escapes the character after it there
LINK_TARGET_UNSAFE = re.compile(r"[\x00-\x20\x7f()<>\\]")
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
    other than http(s) or mailto keeps only its text; one with no text writes
    its url as a word of its own. Text that would open a Markdown block at the
    start of a line is escaped, and so is a "!" that would make the link
    after it an image; a backslash stays as it is written, the brief being
    read raw. heading_level is the level an <h1> takes, so that a text sits
    under the headings around it.
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
    children = list(element)
    return (
        bool(children)
        and not (element.text or "").strip()
        and all(child.tag in LIST_TAGS and not (child.tail or "").strip() for child in children)
    )


def _is_nested_list(element):
    """Whether element, right in a list, nests lists in it: a list, or a block other than a plain item or a quote
    holding nothing but lists; the editor nests a list in an oe-nested item of its own, while a plain one shows its
    marker, and Markdown writes a quote around its lists.
    """
    if element.tag in LIST_TAGS:
        return True
    if element.tag == "li":
        return _has_class(element, NESTED_ITEM_CLASS) and _is_list_only(element)
    return element.tag != "blockquote" and _is_list_only(element)


def _iter_list_items(element, skipped_elements):
    """The items under element, in a list or the list itself, outside the lists nested in it, its items and
    skipped_elements: those of that list.
    """
    descendants = list(element)
    while descendants:
        descendant = descendants.pop()
        if descendant.tag == "li":
            yield descendant
        elif descendant.tag not in LIST_TAGS and descendant not in skipped_elements:
            descendants.extend(descendant)


def _holds_list_item(element, skipped_elements):
    """Whether element, right in a list, holds an item of that list (_iter_list_items), which a browser numbers in
    it as in its list (_is_list_item).
    """
    return any(_is_list_item(item, skipped_elements) for item in _iter_list_items(element, skipped_elements))


def _get_item_holders(element, skipped_elements):
    """The elements between the list element and its items (_iter_list_items) holding an item of it, found in one
    walk of it.
    """
    item_holders = set()
    for item in _iter_list_items(element, skipped_elements):
        # the ancestors above one already found are found already
        for ancestor in item.iterancestors():
            if ancestor is element or ancestor in item_holders:
                break
            item_holders.add(ancestor)
    return item_holders


def _is_list_item(element, skipped_elements):
    """Whether the block element, right in a list and nesting no list in it (_is_nested_list), is numbered in it: an
    item other than an unmarked one (_is_unmarked_item), or a block holding an item (_holds_list_item).
    """
    if element.tag == "li":
        return not _has_class(element, NESTED_ITEM_CLASS)
    return _holds_list_item(element, skipped_elements)


def _is_unmarked_item(element):
    """Whether element, right in a list and nesting no list in it (_is_nested_list), is an oe-nested item: the editor
    shows it as a block, with no marker, and a browser gives it no number.
    """
    return element.tag == "li" and _has_class(element, NESTED_ITEM_CLASS)


def _is_stray_item(element):
    """Whether element is an item with no list around it, as an HTML parser leaves the items after a block that
    closed their list: a browser still shows it as a list item, with the bullet a list item has by default.
    """
    return element.tag == "li" and next(element.iterancestors(*LIST_TAGS), None) is None


def _normalise_space(text):
    return WHITESPACE.sub(" ", text or "")


def _escape_brackets(text):
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _get_backslash_run_start(text, index):
    """The start of the run of backslashes right before index in text, walked back: slicing the text before each of
    many indexes would copy it each time.
    """
    run_start = index
    while run_start and text[run_start - 1] == "\\":
        run_start -= 1
    return run_start


def _is_escaped(text, index):
    """Whether a backslash escapes the character of text at index: an odd run of them stands right before it."""
    return (index - _get_backslash_run_start(text, index)) % 2 == 1


class _LinkMarkup(str):
    """Markdown opening with the markup of a link, which a "!" right before it would make an image."""

    __slots__ = ()


class _BangLinkMarkup(str):
    """Markdown opening with backslashes if any, a "!" and the markup of a link, the "!" left as written: backslashes
    written before the text would join its run. None of the run was put in for the "!", so its escape goes in once,
    where the run is complete (_join_inline), and none is ever taken out.
    """

    __slots__ = ()


def _join_inline(parts, is_complete=False):
    """The inline parts, text and markup, joined, a "!" right before a _LinkMarkup, or opening a _BangLinkMarkup,
    escaped unless backslashes escape it already. Where nothing but backslashes stands before that "!" in the text,
    those before the text count too: it is left as written and the text marked a _BangLinkMarkup, as a text opening
    with a link's markup is a _LinkMarkup, unless is_complete, only markup or a line start coming before the text.
    """
    joined_text = "".join(parts)
    pieces = []
    opening_type = str
    piece_start = part_start = 0
    for part in filter(None, parts):
        bang = None
        if isinstance(part, _BangLinkMarkup):
            # its own backslashes are not walked again: a run deep in nested elements would be walked at each level
            bang, run_end = part_start + part.index("!"), part_start
        elif isinstance(part, _LinkMarkup):
            if not part_start:
                opening_type = _LinkMarkup
            elif joined_text[part_start - 1] == "!":
                bang = run_end = part_start - 1
        if bang is not None:
            run_start = _get_backslash_run_start(joined_text, run_end)
            if not (run_start or is_complete):
                opening_type = _BangLinkMarkup
            elif (bang - run_start) % 2 == 0:
                pieces += (joined_text[piece_start:bang], "\\")
                piece_start = bang
        part_start += len(part)
    pieces.append(joined_text[piece_start:])
    return opening_type("".join(pieces))


def _escape_label(text):
    return _escape_brackets(" ".join(_strip_tokens_in_text(text).split()))


def _escape_line_starts(lines, is_followed_on=False):
    """lines, those of a paragraph, each with what would open a Markdown block at its start escaped. A link
    reference definition opens one only where a CommonMark paragraph starts, on the first line or after an empty
    one; elsewhere an escaped bracket would be read on in a label left open above it. Where is_followed_on, text
    joined onto the last line may close a label left open at the end, which is escaped too.
    """
    text = "\n".join(lines)
    escaped_lines = []
    line_start = 0
    is_paragraph_start = True
    for line in lines:
        label = LINK_LABEL.match(text, line_start) if is_paragraph_start else None
        label_end = label and label.group(1)
        is_definition = label_end == "]:" or (is_followed_on and label_end is not None)
        escaped_lines.append("\\" + line if is_definition else _escape_line_start(line))
        is_paragraph_start = not line
        line_start += len(line) + 1
    return escaped_lines


def _escape_line_start(line):
    ordered_list_number = ORDERED_LIST_NUMBER.match(line)
    if ordered_list_number:
        return line[: ordered_list_number.end()] + "\\" + line[ordered_list_number.end() :]
    return "\\" + line if BLOCK_MARKUP.match(line) else line


def _escape_line_end(line):
    """line, one of its paragraph's but the last, with a space after a backslash it ends in: CommonMark reads that
    one as a hard line break and shows no backslash.
    """
    is_hard_break = line.endswith("\\") and not _is_escaped(line, len(line) - 1)
    return line + " " if is_hard_break else line


def _quote_line(line):
    """line in a quote, its end stripped but for the space _escape_line_end put after a backslash."""
    quoted_line = "> " + line
    return quoted_line if line.endswith("\\ ") else quoted_line.rstrip()


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


def _get_text_content(element, skipped_elements):
    """The text of element on one line as a browser shows it, a space where a line break or one of skipped_elements
    stands and on each side of a block, which breaks the line around it.
    """
    parts = [element.text or ""]
    for child in element:
        if child.tag == "br" or child in skipped_elements:
            parts.append(" ")
        elif isinstance(child.tag, str) and child.tag not in SKIPPED_TAGS:
            child_text = _get_text_content(child, skipped_elements)
            parts.append(" %s " % child_text if _is_block(child) else child_text)
        parts.append(child.tail or "")
    return "".join(parts)


def _get_preformatted_text(element, skipped_elements):
    """The text of element with its line breaks as a browser shows it; one of skipped_elements, blocks, only breaks
    its line.
    """
    parts = [element.text or ""]
    for child in element:
        if isinstance(child.tag, str) and child.tag not in SKIPPED_TAGS:
            if child.tag == "br":
                parts.append("\n")
            elif child not in skipped_elements:
                parts.append(_get_preformatted_text(child, skipped_elements))
            if _is_block(child):
                parts.append("\n")
        parts.append(child.tail or "")
    return "".join(parts)


def _indent_lines(lines, indent):
    return [indent + line if line else line for line in lines]


def _join_lines(lines):
    return ["\n".join(lines)] if lines else []


def _get_last_line(lines, last_line=""):
    """The last of lines at their least indentation, where their outermost blocks stand, else "": a list may open
    with the lines of an item nested in one of its own (oe-nested), further in. Given last_line, that of the lines
    above them, it is that of all: growing lines are read on from where they were read, not all over again.
    """
    # + 1: a line at the indentation of last_line comes after it
    least_indent = len(last_line) - len(last_line.lstrip(" ")) + 1 if last_line else math.inf
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
    return _is_paragraph_line(_get_last_line((block or "").split("\n")))


def _is_paragraph_line(line):
    return bool(line) and not (LIST_START.match(line) or OTHER_BLOCK_LINE.match(line) or line == THEMATIC_BREAK)


def _escape_task_box(line):
    """line, the first of a list item's text, with a box GFM would read in it escaped."""
    return "\\" + line if TASK_LIST_BOX.match(line) else line


def _cannot_interrupt_paragraph(block):
    """Whether block opens with a list CommonMark lets interrupt no paragraph, reading it as text of one right
    above it: an ordered one numbered from other than 1, or one opening with an empty item.
    """
    list_start = LIST_START.match(block)
    if not list_start:
        return False
    is_empty_item = not block.startswith(" ", list_start.end())
    # the number before the delimiter, "" for a bullet
    return is_empty_item or list_start.group().strip()[:-1] not in ("", "1")


def _is_under(block, previous_block):
    """Whether block goes right under previous_block: anything does under the marker of an empty item
    (ITEM_START), as its content; a list does, but for one _cannot_interrupt_paragraph under a paragraph; any
    other block goes a paragraph apart.
    """
    if previous_block == ITEM_START:
        return True
    return bool(LIST_START.match(block)) and not (
        _cannot_interrupt_paragraph(block) and _ends_in_paragraph(previous_block)
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


def _get_list_number(value):
    """The number value gives an ordered list or item as HTML reads it, as near as CommonMark writes it, else None:
    0 for a negative one, which it has no way to write; none for one of ten digits or more.
    """
    number = _get_html_integer(value)
    return None if number is None or abs(number) == math.inf else max(number, 0)


def _get_first_number(element, children, skipped_elements):
    """The number a browser gives the first item of the ordered list: its start, else the count of its items where
    it is reversed, its children as _iter_list_children gives them, else 1.
    """
    start = _get_list_number(element.get("start"))
    if start is not None:
        return start
    if "reversed" in element.attrib:
        return sum(
            not _is_nested_list(child) and _is_list_item(child, skipped_elements) for _paragraphs, child in children
        )
    return 1


def _get_span(cell, attribute, maximum):
    """The span the attribute of cell gives as HTML reads it, at most maximum: 1 where it gives none or one
    below 0, 0 where it gives 0.
    """
    span = _get_html_integer(cell.get(attribute))
    if span is None or span < 0:
        return 1
    return min(span, maximum)


def _read_table(table):
    """(rows, captions, end table) of the table as a browser builds it, outside the tables in its cells and what
    it hides: each row as (row group, cells), where a browser moves a row, a cell or a caption out of the blocks
    around it into the table; a cell outside a row in a row it implies, up to the next row, row group or caption;
    a caption ends the row and the row group it stands in. The end table is the first table outside its cells and
    captions, where a browser ends the table: what follows it is no part of it. Limits, where a browser ends what
    holds it: a row in a row keeps the later cells of that row in it, a cell or a caption in a cell, or a cell in a
    caption, is part of its text, and the text after a row in a cell stays in the cell.
    """
    rows = []
    captions = []
    # the cells of each row that has not ended
    row_cells = {}
    implied_row = None
    caption_count = 0
    # (element, the row it is in: the table outside the rows, None in a cell or a caption; its row group)
    descendants = [(child, table, table) for child in reversed(table)]
    while descendants:
        element, row, row_group = descendants.pop()
        tag = element.tag
        if not isinstance(tag, str) or tag in SKIPPED_TAGS:
            continue
        if tag == "table":
            if row is None:
                continue
            return rows, captions, element
        if tag == "tr":
            implied_row = None
            row_cells[element] = []
            rows.append(((row_group, caption_count), row_cells[element]))
            row = element
        elif tag in ROW_GROUP_TAGS:
            if row is table:
                implied_row = None
            row_group = element
        elif row is None:
            pass
        elif tag == "caption":
            captions.append(element)
            caption_count += 1
            row_cells.pop(row, None)
            row = None
        elif tag in CELL_TAGS:
            cells = row_cells.get(row)
            if cells is None:
                if implied_row is None or implied_row[0] != (row_group, caption_count):
                    implied_row = ((row_group, caption_count), [])
                    rows.append(implied_row)
                cells = implied_row[1]
            cells.append(element)
            row = None
        descendants.extend((child, row, row_group) for child in reversed(element))
    return rows, captions, None


def _get_table_row(cells):
    return "| %s |" % " | ".join(cells)


class _MarkdownWriter:
    def __init__(self, attachment_names, heading_level, base_url):
        self.attachment_names = attachment_names
        self.heading_level = heading_level
        self.base_host = get_base_host(base_url)
        self.link_depth = 0
        self.cell_depth = 0
        self.is_in_opening_list = False
        # whether an unmarked item's text may be joined onto the last paragraph written (_get_list)
        self.is_followed_on = False
        # the cells and captions of the table whose loose blocks are written: they stand in the table (_read_table)
        self.table_parts = set()
        # {end table: the blocks of the table it ends (_read_table), written right before it}
        self.ended_table_blocks = {}

    def get_blocks(self, element, get_child_blocks=None, previous_block=None):
        """The Markdown blocks of what element holds: a paragraph for each run
        of text and inline elements, the blocks of each block element as
        get_child_blocks(child, the block before it) gives them, and one list
        for each run of stray items (_is_stray_item) with no text between them.
        previous_block is the block before the first, in the container the
        blocks go to.
        """
        blocks = []
        inline_parts = [self._get_text(element.text)]
        stray_items = []
        for child in element:
            if not _is_block(child):
                inline_parts.extend((self._get_inline(child), self._get_text(child.tail)))
                continue
            paragraph = self._get_paragraph(inline_parts)
            is_stray_item = _is_stray_item(child)
            if stray_items and (paragraph or not is_stray_item):
                blocks.extend(self._get_stray_list(stray_items, (blocks or [previous_block])[-1]))
                stray_items = []
            blocks.extend(paragraph)
            if is_stray_item:
                stray_items.append(child)
            else:
                child_blocks = (get_child_blocks or self._get_element_blocks)(child, (blocks or [previous_block])[-1])
                blocks.extend(child_blocks)
            inline_parts = [self._get_text(child.tail)]
        if stray_items:
            blocks.extend(self._get_stray_list(stray_items, (blocks or [previous_block])[-1]))
        blocks.extend(self._get_paragraph(inline_parts))
        return blocks

    def _get_text(self, text):
        clean_text = _strip_tokens_in_text(_normalise_space(text))
        return _escape_brackets(clean_text) if self.link_depth else clean_text

    def _get_paragraph(self, inline_parts):
        inline_text = _join_inline(inline_parts, is_complete=True)
        lines = [REPEATED_SPACES.sub(" ", line).strip() for line in inline_text.split("\n")]
        # a cell is one line inside its row, where nothing opens a block
        if not self.cell_depth:
            lines = _escape_line_starts(lines, self.is_followed_on)
            lines = [_escape_line_end(line) if next_line else line for line, next_line in zip(lines, lines[1:] + [""])]
        # a run of line breaks shows as one empty line
        paragraph = LINE_BREAK_RUN.sub("\n\n", "\n".join(lines)).strip("\n")
        return [paragraph] if paragraph else []

    def _get_line(self, element):
        return " ".join(" ".join(self.get_blocks(element)).split())

    def _get_caption(self, caption):
        """The caption as one line (_get_line), escaped where it opens a definition after the markers of its first
        block: each block escapes one it opens itself, not a label it leaves open which the blocks after it close.
        """
        caption_line = self._get_line(caption)
        paragraph_start = CAPTION_CONTAINER_MARKERS.match(caption_line).end()
        label = LINK_LABEL.match(caption_line, paragraph_start)
        if label and label.group(1) == "]:":
            return caption_line[:paragraph_start] + "\\" + caption_line[paragraph_start:]
        return caption_line

    def _get_element_blocks(self, element, previous_block=None):
        tag = element.tag
        if tag in HEADING_LEVELS:
            heading_text = " ".join(self._get_inline_content(element, is_complete=True).split())
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
            return ["\n".join(map(_quote_line, quoted_text.split("\n")))] if quoted_text else []
        if tag == "hr":
            return [THEMATIC_BREAK]
        if element in self.table_parts:
            return []
        return self.get_blocks(element, previous_block=previous_block)

    def _get_inline(self, element):
        tag = element.tag
        if not isinstance(tag, str) or tag in SKIPPED_TAGS:
            return ""
        # a part of a table, a block, still keeps the words around it apart
        if element in self.table_parts:
            return " "
        if tag == "br":
            return "\n"
        if tag == "img":
            return self._get_image(element)
        if tag == "code":
            return _get_code_span(_get_text_content(element, self.table_parts))
        if tag == "a":
            return self._get_link(element)
        if tag in EMPHASIS_MARKS:
            return _emphasize(self._get_inline_content(element, is_complete=True), EMPHASIS_MARKS[tag])
        # a block inside an inline element still keeps its words apart from those around it
        if tag in BLOCK_TAGS:
            return " %s " % self._get_inline_content(element, is_complete=True)
        return self._get_inline_content(element)

    def _get_inline_content(self, element, is_complete=False):
        parts = [self._get_text(element.text)]
        for child in element:
            parts.append(self._get_inline(child))
            parts.append(self._get_text(child.tail))
        return _join_inline(parts, is_complete)

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
        url = normalise_url(element.get("href"))
        is_link = bool(url) and _get_scheme(url) in LINK_SCHEMES
        self.link_depth += 1
        content = self._get_inline_content(element, is_complete=is_link)
        self.link_depth -= 1
        if not is_link:
            return content
        if not content:
            # a browser shows nothing of it: its url, which the reader may want, stands apart from the words around it
            return " %s " % self._get_link_markup(element, url, content)
        # the outer spaces of its text stay outside the link, keeping it apart from the words around it, as that of a
        # part of a table in it does
        leading_space = content[: len(content) - len(content.lstrip())]
        trailing_space = content[len(content.rstrip()) :]
        link = leading_space + self._get_link_markup(element, url, content.strip()) + trailing_space
        return _LinkMarkup(link) if link.startswith("[") else link

    def _get_link_markup(self, element, url, link_text):
        clean_url = strip_url_tokens(url)
        is_bare = not link_text or link_text in (url, clean_url)
        attachment_id = self._get_local_attachment_id(url)
        if attachment_id:
            if is_bare:
                return self._get_attachment_pointer("file", attachment_id, element.get("title"))
            return "[%s](attachment %d)" % (link_text, attachment_id)
        if is_bare:
            return _get_link_target(clean_url)
        return "[%s](%s)" % (link_text, _get_link_target(clean_url))

    def _get_stray_list(self, items, previous_block):
        """The run of stray items as the bullet list a browser shows them as."""
        children = [([], item) for item in items] + [([], None)]
        return self._get_list(etree.Element("ul"), previous_block, children=children)

    def _get_list(self, element, previous_block=None, is_opening=False, children=None):
        """The list as Markdown, an editor's checklist as a task list; its
        children as _iter_list_children gives them, or those given. Text and
        blocks other than items right inside it stand between its parts, after
        which it goes on as at the start of a text, or as after a list where they
        end in one; a block holding an item stands for what it holds
        (_iter_list_children). A list right after a list of its marker takes the
        other marker of LIST_MARKERS. An ordered item has the number a browser
        gives it, by a value or down a reversed list too: where that is not the
        one after the item above, which CommonMark would give, the item opens a
        list of the other delimiter. An empty ordered item is its number alone
        where an item or nested lines follow it in its part, which CommonMark
        would number otherwise; one ending a part changes no number and is left
        out. An empty bullet item is its marker, and its box in a checklist,
        where an unmarked item (_is_unmarked_item) right after it goes on under
        it, and else left out. An unmarked item goes on under the item before it
        in its part, its text on that item's last paragraph, or on the line of
        its box, as GFM draws a box only before text on its line, and stands
        between the parts where it has no item before it. A box before any other
        block stands alone above it, a task in the raw text only. Nested lines
        before its first item stand two spaces in, which CommonMark reads at its
        level; none where it opens a list (is_opening) or an item (ITEM_START),
        or stands in a block between the parts of such a list, which indents
        them, or follows a list, under whose last item they would go.
        """
        is_ordered = element.tag == "ol"
        is_checklist = element.tag == "ul" and _has_class(element, CHECKLIST_CLASS)
        *children, (trailing_blocks, _end) = children or self._iter_list_children(element)
        number = _get_first_number(element, children, self.table_parts) if is_ordered else None
        step = -1 if "reversed" in element.attrib else 1
        # the number CommonMark gives the last item of the run of lines it numbers on
        shown_number = None
        is_opening = is_opening or previous_block == ITEM_START or self.is_in_opening_list
        blocks = []
        # the lines since the start of the list or the last part of it between its items
        lines = []
        # _get_last_line of lines, and how many of them it has read
        last_line, read_line_count = "", 0
        # the indent of the content of the last item of lines, None before their first item
        indent = None
        # the last block under the last item, which a nested list follows in the same container
        item_last_block = None
        # the marker character of lines and whether they go on from a list, read from the block before them as they
        # start: a list writing nothing reads nothing of it, or empty lists after a long block take quadratic time
        marker_character = is_after_list = None
        # (number, lines, last block) of the empty items after lines, written only where a line follows them in the
        # part: of a bullet list, the last only, where an unmarked item follows it
        empty_items = []
        was_followed_on = self.is_followed_on
        followed_on_flags = self._get_followed_on_flags(children, was_followed_on and not trailing_blocks)
        for (loose_blocks, child), is_followed_on in zip(children, followed_on_flags):
            self.is_followed_on = is_followed_on
            is_nested_list = _is_nested_list(child)
            is_item = not is_nested_list and _is_list_item(child, self.table_parts)
            # an empty item waiting to be written is an item before it
            is_follow_on = (
                not (is_nested_list or is_item or loose_blocks)
                and _is_unmarked_item(child)
                and (indent is not None or bool(empty_items))
            )
            if not (is_ordered or is_follow_on):
                empty_items = []
            if not (is_nested_list or is_item or is_follow_on):
                # a browser numbers items only: another block stands between the list's parts, as loose text does;
                # what follows a block reads only its last line at its level, so the lines are not joined for it
                if loose_blocks:
                    block_before = loose_blocks[-1]
                elif lines:
                    last_line, read_line_count = _get_last_line(lines[read_line_count:], last_line), len(lines)
                    block_before = last_line
                else:
                    block_before = previous_block
                was_in_opening_list, self.is_in_opening_list = self.is_in_opening_list, is_opening
                loose_blocks = loose_blocks + self._get_element_blocks(child, block_before)
                self.is_in_opening_list = was_in_opening_list
            if loose_blocks:
                blocks.extend(_join_lines(lines) + loose_blocks)
                previous_block = blocks[-1]
                lines = []
                last_line, read_line_count = "", 0
                indent = item_last_block = shown_number = None
                empty_items = []
            # (number, lines, last block) of the items to write, the empty items waiting put before this one only once
            # it writes: made at every block after a run of them, they would cost its length squared
            new_items = []
            if is_nested_list:
                if empty_items:
                    nested_previous_block = ITEM_START
                else:
                    nested_previous_block = item_last_block if lines else previous_block
                nested_blocks = []
                for nested_list in [child] if child.tag in LIST_TAGS else child:
                    nested_blocks += self._get_list(nested_list, nested_previous_block, indent is None)
                    nested_previous_block = (nested_blocks or [nested_previous_block])[-1]
                if not nested_blocks:
                    continue
            elif is_item:
                item_number = None
                if is_ordered:
                    value = _get_list_number(child.get("value")) if child.tag == "li" else None
                    number = number if value is None else value
                    item_number = max(number, 0)
                    number += step
                # the box is part of the item's text: its nested lists stay under the list marker; the editor draws
                # it on an item right in a bullet checklist only
                box = None
                if is_checklist and child.tag == "li" and child.getparent() is element:
                    box = "[x]" if _has_class(child, CHECKED_ITEM_CLASS) else "[ ]"
                item_blocks = self._get_element_blocks(child, ITEM_START)
                if not item_blocks:
                    empty_items.append((item_number, [box or ""], box or ITEM_START))
                    continue
                item_lines = _join_item_blocks(item_blocks).split("\n")
                if box:
                    if BLOCK_MARKUP.match(item_lines[0]) or LIST_START.match(item_lines[0]):
                        # a blank line ends the box's paragraph: a rule would underline it, a list join it
                        item_lines[0:0] = [box, ""]
                    else:
                        # cmark-gfm ticks a box whose line holds [x]: the text stays as written, the brief is read raw
                        item_lines[0] = "%s %s" % (box, item_lines[0])
                elif not is_ordered and item_lines[0] == THEMATIC_BREAK:
                    item_lines[0] = BULLET_ITEM_BREAK
                else:
                    item_lines[0] = _escape_task_box(item_lines[0])
                new_items.append((item_number, item_lines, item_blocks[-1]))
            elif is_follow_on:
                follow_blocks = self._get_element_blocks(child, empty_items[-1][2] if empty_items else item_last_block)
                if not follow_blocks:
                    continue
            else:
                continue
            new_items[0:0] = empty_items
            if not lines:
                previous_marker = _get_last_list_marker((previous_block or "").split("\n"))
                marker_character = _get_list_marker(element.tag, previous_marker)
                is_after_list = previous_marker is not None
            empty_items = []
            for item_number, item_lines, last_block in new_items:
                if is_ordered:
                    is_new_run = shown_number is None or item_number != shown_number + 1
                    # an item right under an ordered list of its marker would go on with its numbers
                    if is_new_run and _get_last_list_marker(lines) == marker_character:
                        marker_character = _get_list_marker(element.tag, marker_character)
                    shown_number = item_number
                    marker = "%d%s " % (min(item_number, MAX_LIST_NUMBER), marker_character)
                else:
                    marker = marker_character + " "
                first_line = marker + item_lines[0] if item_lines[0] else marker.rstrip()
                # the first only: a later item goes on with the list, whatever the item above it ends with
                if indent is None and lines and not _is_under(first_line, item_last_block):
                    lines.append("")
                item_last_block = last_block
                indent = " " * len(marker)
                lines.append(first_line)
                lines.extend(_indent_lines(item_lines[1:], indent))
            if is_nested_list:
                nested_lines = _join_item_blocks(nested_blocks).split("\n")
                # right under an empty item's number, the first line is that item's text
                if item_last_block == ITEM_START:
                    nested_lines[0] = _escape_task_box(nested_lines[0])
                elif lines and not _is_under(nested_lines[0], item_last_block):
                    lines.append("")
                if indent is not None:
                    nested_lines = _indent_lines(nested_lines, indent)
                elif not (is_opening or is_after_list):
                    nested_lines = _indent_lines(nested_lines, "  ")
                lines.extend(nested_lines)
                item_last_block = nested_blocks[-1]
            elif is_follow_on:
                follow_lines = _join_item_blocks(follow_blocks).split("\n")
                # text goes on the item's last paragraph, as after a line break in it
                is_on_paragraph = _ends_in_paragraph(item_last_block) and _is_paragraph_line(follow_lines[0])
                if item_last_block == ITEM_START:
                    follow_lines[0] = _escape_task_box(follow_lines[0])
                elif new_items and is_on_paragraph:
                    # an empty item's box: GFM reads it as a task's only with text after it on its line
                    lines[-1] += " " + follow_lines.pop(0)
                elif is_on_paragraph:
                    lines[-1] = _escape_line_end(lines[-1])
                elif not _is_under(follow_lines[0], item_last_block):
                    lines.append("")
                lines.extend(_indent_lines(follow_lines, indent))
                item_last_block = follow_blocks[-1]
        self.is_followed_on = was_followed_on
        return blocks + _join_lines(lines) + trailing_blocks

    def _get_followed_on_flags(self, children, is_list_followed_on):
        """Whether an unmarked item may go on the last paragraph of each of children, (loose blocks, child) of a
        list: one after it does past the children that may write nothing (an item with no text of its own, a nested
        list, a block holding nothing), and so does the list's own follow-on (is_list_followed_on) past the last. Text before a
        child, or an item's own text, stands between them.
        """
        flags = []
        is_followed_on = is_list_followed_on
        for loose_blocks, child in reversed(children):
            flags.append(is_followed_on)
            if loose_blocks:
                is_followed_on = False
            elif _is_unmarked_item(child) and not _is_nested_list(child):
                is_followed_on = True
            elif is_followed_on and child.tag == "li" and self._get_text(child.text).strip():
                is_followed_on = False
        flags.reverse()
        return flags

    def _iter_list_children(self, element):
        """(paragraphs, child) for each block right in the list element, paragraphs those of the text before it,
        then (paragraphs, None) for the text after the last. A block holding an item (_get_item_holders), other than
        an item or one of FORMED_BLOCK_TAGS, stands for what it holds, its text paragraphs apart: a browser shows
        the item in the list and the text as text right in the list. A part of the table whose blocks are written
        (table_parts) is left out: a browser moves it out of the list into the table.
        """
        item_holders = None
        paragraphs = []
        inline_parts = [self._get_text(element.text)]
        # the list and the blocks standing for what they hold walked into, each with its children not walked yet
        containers = [(element, iter(element))]
        while containers:
            container, children = containers[-1]
            for child in children:
                if not _is_block(child):
                    inline_parts.extend((self._get_inline(child), self._get_text(child.tail)))
                    continue
                paragraphs += self._get_paragraph(inline_parts)
                if child in self.table_parts:
                    inline_parts = [self._get_text(child.tail)]
                    continue
                if child.tag != "li" and child.tag not in FORMED_BLOCK_TAGS:
                    # the list walked once, at the first such block: searched block by block, a chain of blocks
                    # costs its length times what it holds
                    if item_holders is None:
                        item_holders = _get_item_holders(element, self.table_parts)
                    if child in item_holders:
                        containers.append((child, iter(child)))
                        inline_parts = [self._get_text(child.text)]
                        break
                yield paragraphs, child
                paragraphs = []
                inline_parts = [self._get_text(child.tail)]
            else:
                containers.pop()
                paragraphs += self._get_paragraph(inline_parts)
                inline_parts = [self._get_text(container.tail)] if containers else []
        yield paragraphs, None

    def _get_table(self, element, previous_block=None):
        """What the table holds outside its parts, which a browser shows before it, its captions, then the table
        (_read_table); these go right before the table that ends it, if any, after which the rest of what it holds is
        no part of it. A limit: an item or a quote that table stands in, which a browser closes there, keeps the rest
        of what it holds.
        """
        blocks = self.ended_table_blocks.pop(element, [])
        table_rows, captions, end_table = _read_table(element)
        outer_table_parts = self.table_parts
        self.table_parts = set(captions).union(*(cells for _row_group, cells in table_rows))
        table_blocks = list(filter(None, map(self._get_caption, captions)))
        rows = self._get_table_rows(table_rows)
        if rows:
            width = max(len(cells) for cells in rows)
            rows = [cells + [""] * (width - len(cells)) for cells in rows]
            lines = [_get_table_row(rows[0]), _get_table_row(["---"] * width)]
            lines.extend(_get_table_row(cells) for cells in rows[1:])
            table_blocks.append("\n".join(lines))
        if end_table is not None:
            self.ended_table_blocks[end_table] = table_blocks
        blocks += self.get_blocks(element, previous_block=(blocks or [previous_block])[-1])
        # an end table in an inline element or code writes no blocks of its own: the table goes after the rest
        blocks += self.ended_table_blocks.pop(end_table, []) if end_table is not None else table_blocks
        self.table_parts = outer_table_parts
        return blocks

    def _get_table_rows(self, table_rows):
        """The cells of each row of table_rows (_read_table), a cell that
        spans columns or rows followed or underlaid by empty ones, so that every
        row keeps its columns. The table is at most MAX_TABLE_COLUMNS wide: the
        text of a cell beyond joins the last column. A limit: a span over rows
        right in the table runs on past an empty row group between them, where a
        browser ends it.
        """
        rows = []
        spanned_rows = {}
        previous_row_group = None
        for row_group, row_cells in table_rows:
            # HTML ends a row span with its row group: a thead, tbody or tfoot, or a run of rows right in the table,
            # which a caption ends too
            if row_group != previous_row_group:
                spanned_rows = {}
            previous_row_group = row_group
            covered_columns = set(spanned_rows)
            new_spans = {}
            cells = []
            for cell in row_cells:
                while len(cells) in covered_columns:
                    cells.append("")
                # rowspan="0" spans the rest of the row group, as HTML reads it
                rowspan = _get_span(cell, "rowspan", math.inf) or math.inf
                cell_text = self._get_cell(cell)
                if len(cells) >= MAX_TABLE_COLUMNS:
                    cells[-1] = " ".join(filter(None, (cells[-1], cell_text)))
                    continue
                for offset in range(_get_span(cell, "colspan", MAX_TABLE_COLUMNS - len(cells)) or 1):
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
        code_text = _strip_tokens_in_text(_get_preformatted_text(element, self.table_parts)).strip("\n")
        if not code_text.strip():
            return []
        fence = _get_fence(code_text, 3)
        return ["%s\n%s\n%s" % (fence, code_text, fence)]
