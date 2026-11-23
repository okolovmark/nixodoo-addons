import re

from lxml import etree
from lxml import html as lxml_html

from .report_spec import ReportSpecError

# The page's layout and text, which is all a report page holds: what a binding fills, tables, lists,
# headings, sections and inline formatting. Anything else loses its tag and keeps its content.
PAGE_TAGS = (
    "a",
    "abbr",
    "article",
    "aside",
    "b",
    "blockquote",
    "br",
    "caption",
    "cite",
    "code",
    "col",
    "colgroup",
    "dd",
    "del",
    "details",
    "dfn",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "i",
    "ins",
    "kbd",
    "li",
    "main",
    "mark",
    "nav",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "samp",
    "section",
    "small",
    "span",
    "strong",
    "sub",
    "summary",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "time",
    "tr",
    "u",
    "ul",
    "var",
    "wbr",
)
# Taken out with their content: script, a style below the top of the page, foreign content
# (which a browser parses by other rules), and the document's title.
CONTENT_DROPPED_TAGS = ("script", "style", "svg", "math", "template", "title")
LAYOUT_ATTRIBUTES = ("id", "class", "style", "title", "lang", "dir", "align", "width")
CELL_ATTRIBUTES = ("colspan", "rowspan")
URL_ATTRIBUTES = ("href", "src", "action", "formaction", "data", "poster", "background", "ping", "srcset", "cite")
SCRIPT_URL = re.compile(r"^(javascript|vbscript):", re.IGNORECASE)
# Spaces and control characters a browser skips inside a URL.
URL_NOISE = re.compile(r"[\x00-\x20]+")
WRAPPER_TAG = "div"
MAX_CLEAN_PASSES = 3
# CSS that loads a file or a font, or runs script in an old browser: the frame's policy refuses the
# loads, and the sheet or style attribute that holds one goes whole before it.
CSS_LOADERS = re.compile(
    r"url\s*\(|image-set\s*\(|@import|@font-face|expression\s*\(|-moz-binding|behavior\s*:", re.IGNORECASE
)
CSS_NEWLINES = re.compile(r"\r\n|[\r\f]")
CSS_HEX_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n]?")
CSS_ESCAPE = re.compile(r"\\(.)", re.DOTALL)


def clean_page(page):
    """(page, removed) of a page as the model wrote it, with only its layout and text left.

    An allow-list: the tags of PAGE_TAGS, a <style> at the top of the page or
    in a full document's head, and layout attributes; a style sheet or style
    attribute with CSS that loads a file or a font goes whole, and a link
    keeps its text, not its href.
    The rest goes and `removed` names it, by kind. The page returned
    is a serialization of the clean tree, every text escaped, so the frame
    parses what was checked. A page that was mostly script is refused.

    A tag that goes leaves its children in its parent, a nesting a parser may
    not keep (<p><font><div>): the copy runs again on its own output until it
    comes back the same, MAX_CLEAN_PASSES times at most.
    """
    removed = {"elements": [], "attributes": [], "css": []}
    try:
        encoded_page = page.encode()
    except UnicodeEncodeError as error:
        raise ReportSpecError(
            "The page holds characters no HTML holds, such as half of a surrogate pair: remove them."
        ) from error
    clean_html, script_chars = _clean_pass(encoded_page, removed)
    if script_chars * 2 > len(page):
        raise ReportSpecError(
            "The page is mostly script, and no script of yours runs in a report: lay the page out in HTML and"
            " CSS, and fill its figures, tables and charts with bindings."
        )
    for _pass in range(MAX_CLEAN_PASSES - 1):
        next_html = _clean_pass(clean_html.encode(), removed)[0]
        if next_html == clean_html:
            break
        clean_html = next_html
    return clean_html, {kind: names for kind, names in removed.items() if names}


def _clean_pass(encoded_page, removed):
    """(page, script characters) of one parse, copy and serialization of the page; see `clean_page`."""
    container = etree.Element(WRAPPER_TAG)
    try:
        document = lxml_html.document_fromstring(encoded_page, parser=lxml_html.HTMLParser(encoding="utf-8"))
    except etree.ParserError:
        document = None
    script_chars = 0
    if document is not None:
        removed["attributes"] += document.attrib.keys()
        for part in document:
            if isinstance(part.tag, str) and part.tag in ("head", "body"):
                removed["attributes"] += part.attrib.keys()
                script_chars += _copy_content(part, container, removed, is_top=True)
            elif isinstance(part.tag, str):
                script_chars += _copy_element(part, container, removed, is_top=True)
    if (container.text or "").strip():
        # A parser wraps leading text in a <p> of its own extent; a span keeps the page a fixed point.
        lead = etree.Element("span")
        lead.text, container.text = container.text, None
        container.insert(0, lead)
    else:
        container.text = None
    serialized = lxml_html.tostring(container, encoding="unicode")
    return serialized[len(f"<{WRAPPER_TAG}>") : -len(f"</{WRAPPER_TAG}>")], script_chars


def _append_text(target, text):
    if not text:
        return
    if len(target):
        target[-1].tail = (target[-1].tail or "") + text
    else:
        target.text = (target.text or "") + text


def _copy_content(source, target, removed, is_top=False):
    """Copy what may stay of the source's text and children into the target; the characters of script left out."""
    _append_text(target, source.text)
    script_chars = 0
    for child in source:
        if isinstance(child.tag, str):
            script_chars += _copy_element(child, target, removed, is_top)
        _append_text(target, child.tail)
    return script_chars


def _copy_element(element, target, removed, is_top):
    """Copy one element into the target as far as it may stay, without its tail; comments and processing
    instructions never do. The characters of script left out."""
    tag = element.tag.rpartition(":")[2].lower()
    script_chars = _script_attribute_chars(element)
    if tag == "style" and is_top and "<" not in (element.text or ""):
        removed["attributes"] += element.attrib.keys()
        loaders = _css_loaders(element.text or "")
        if loaders:
            removed["css"] += loaders
            removed["elements"].append(tag)
        else:
            etree.SubElement(target, "style").text = element.text or ""
    elif tag in CONTENT_DROPPED_TAGS:
        removed["elements"].append(tag)
        if tag == "script":
            script_chars += len(lxml_html.tostring(element, encoding="unicode", with_tail=False))
    elif tag in PAGE_TAGS:
        clean_element = etree.SubElement(target, tag)
        for name, value in element.attrib.items():
            loaders = _css_loaders(value) if name.lower() == "style" else []
            removed["css"] += loaders
            if loaders or not _is_page_attribute(tag, name.lower()):
                removed["attributes"].append(name)
                continue
            try:
                clean_element.set(name.lower(), value)
            except ValueError:
                # A name or value XML cannot hold, such as a control character in it.
                removed["attributes"].append(name)
        script_chars += _copy_content(element, clean_element, removed)
    else:
        removed["elements"].append(tag)
        # libxml2 nests what follows an unclosed <embed> inside it: that content may be the page's own.
        script_chars += _copy_content(element, target, removed)
    return script_chars


def _css_unescaped(css):
    """CSS with its escapes read, as a browser reads a name."""

    def hex_character(match):
        code_point = int(match.group(1), 16)
        return chr(code_point) if 0 < code_point <= 0x10FFFF and not 0xD800 <= code_point <= 0xDFFF else "\ufffd"

    return CSS_ESCAPE.sub(r"\1", CSS_HEX_ESCAPE.sub(hex_character, css))


def _css_loaders(css):
    """The loaders a style sheet or attribute holds, as written or with its escapes read, in its comments and
    strings too: so neither an escape nor a comment that a string opens hides one. The CSS itself goes out as it
    was written or not at all, never as read here."""
    # A browser reads CR LF, CR and FF as one LF first: an escape's one trailing space may be a CR LF.
    css = CSS_NEWLINES.sub("\n", css)
    return sorted(
        {
            re.sub(r"\s+", "", match.group(0).lower())
            for text in (css, _css_unescaped(css))
            for match in CSS_LOADERS.finditer(text)
        }
    )


def _is_page_attribute(tag, name):
    # No href, a fragment's neither: a srcdoc frame resolves "#x" against the app's URL, a navigation
    # the frame's policy blocks, which leaves an error page in place of the report.
    if name in LAYOUT_ATTRIBUTES or name.startswith("aria-"):
        return True
    return name in CELL_ATTRIBUTES and tag in ("td", "th")


def _script_attribute_chars(element):
    """The characters of the element's event handlers and script URLs."""
    return sum(
        len(name) + len(value)
        for name, value in element.attrib.items()
        if name.rpartition(":")[2].lower().startswith("on")
        or (name.rpartition(":")[2].lower() in URL_ATTRIBUTES and SCRIPT_URL.match(URL_NOISE.sub("", value)))
    )


def page_element_ids(page):
    """The ids of the page's elements, which bindings fill."""
    try:
        document = lxml_html.document_fromstring(page.encode(), parser=lxml_html.HTMLParser(encoding="utf-8"))
    except etree.ParserError:
        return set()
    return {element.get("id") for element in document.iter() if isinstance(element.tag, str) and element.get("id")}
