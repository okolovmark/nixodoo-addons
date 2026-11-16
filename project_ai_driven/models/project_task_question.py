import textwrap

from lxml import etree
from lxml import html as lxml_html

from odoo import api, fields, models

QUESTION_STATES = {"open": "Open", "answered": "Answered", "dropped": "Dropped"}
ANSWER_STAMP_FIELDS = ("answered_by_id", "answered_on")
# What shows without text, as core's is_html_empty counts it.
MEDIA_TAGS = ("img", "iframe", "video")
ICON_TAGS = ("i", "span")
ICON_CLASSES = ("fa", "fab", "fad", "far", "oi")
# A reader sees the text of these apart from the text around them.
SEPARATING_TAGS = (
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
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
    "li",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "ul",
)
DISPLAY_NAME_WIDTH = 80
DISPLAY_NAME_PLACEHOLDER = " ..."


def parse_html(html_content):
    """html_content as the children of a <div>, or None where lxml reads it as a
    whole document it cannot cut down to a fragment.
    """
    try:
        return lxml_html.fragment_fromstring(str(html_content), create_parent="div")
    # lxml asserts one body: a document with none or two raises AssertionError, or IndexError under -O
    except (AssertionError, IndexError, etree.ParserError):
        return None


def get_visible_text(element):
    """The text a reader sees in an lxml element: words apart across a line
    break or a block, whitespace and no-break spaces collapsed.
    """
    return " ".join("".join(_iter_text(element)).split())


def _iter_text(element):
    yield element.text or ""
    for child in element:
        if isinstance(child.tag, str):
            separator = " " if child.tag in SEPARATING_TAGS else ""
            yield separator
            yield from _iter_text(child)
            yield separator
        yield child.tail or ""


def get_html_text(html_content):
    root = parse_html(html_content) if html_content else None
    return get_visible_text(root) if root is not None else ""


def _shorten_text(text, width):
    """text cut at a word to fit width; textwrap gives the placeholder alone
    where the first word is longer, so that word is cut instead.
    """
    # break_on_hyphens off: a hyphenated host would otherwise end the name after "https://my-".
    short_text = textwrap.shorten(text, width, placeholder=DISPLAY_NAME_PLACEHOLDER, break_on_hyphens=False)
    if len(text) > width and short_text == DISPLAY_NAME_PLACEHOLDER.lstrip():
        return text[: width - len(DISPLAY_NAME_PLACEHOLDER)] + DISPLAY_NAME_PLACEHOLDER
    return short_text


def has_content(element):
    """Whether an lxml element shows a reader anything: text, media or an icon."""
    return bool(get_visible_text(element)) or has_media(element)


def has_media(element):
    """Whether an lxml element is or holds an image, a video, an embedded frame or an icon."""
    return any(
        node.tag in MEDIA_TAGS or (node.tag in ICON_TAGS and any(name in node.classes for name in ICON_CLASSES))
        for node in element.iter(etree.Element)
    )


def is_blank_html(html_content):
    """Whether Html shows a reader nothing; Html that lxml cannot read as a fragment counts as content."""
    if not html_content or not str(html_content).strip():
        return True
    root = parse_html(html_content)
    return root is not None and not has_content(root)


class ProjectTaskQuestion(models.Model):
    _name = "project.task.question"
    _description = "Task Question"
    _inherit = ["project.task.access.mixin"]
    _order = "sequence, id"

    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True, index=True)
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    sequence = fields.Integer(default=10)
    round = fields.Char(help="The round of questions this one was asked in: a name or a date.")
    question = fields.Html()
    addressed_to_id = fields.Many2one("res.users", "Addressed to", domain=[("share", "=", False)])
    answer = fields.Html()
    answered_by_id = fields.Many2one("res.users", "Answered by")
    answered_on = fields.Datetime("Answered on")
    state = fields.Selection(list(QUESTION_STATES.items()), string="Status", required=True, default="open")

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._prepare_new_row_values(vals) for vals in vals_list])

    def write(self, vals):
        answered_rows = self._filter_newly_answered(vals)
        if answered_rows:
            super(ProjectTaskQuestion, answered_rows).write(self._prepare_state_values({**vals, "state": "answered"}))
        # the other rows a write of Answered reaches were answered already: they keep their stamp
        if vals.get("state") != "answered":
            vals = self._prepare_state_values(vals)
        return super(ProjectTaskQuestion, self - answered_rows).write(vals)

    def name_get(self):
        return [
            (
                record.id,
                _shorten_text(get_html_text(record.question) or get_html_text(record.answer), DISPLAY_NAME_WIDTH)
                or record.task_id.display_name,
            )
            for record in self
        ]

    @api.model
    def _prepare_new_row_values(self, vals):
        if "state" not in vals and not self._is_blank_answer(vals.get("answer")):
            vals = {**vals, "state": "answered"}
        return self._prepare_state_values(vals)

    def _filter_newly_answered(self, vals):
        """The rows vals makes Answered: those it sets Answered that were not,
        or, without a state, the open rows it gives an answer.
        """
        if vals.get("state") == "answered":
            return self.filtered(lambda record: record.state != "answered")
        if "state" in vals or self._is_blank_answer(vals.get("answer")):
            return self.browse()
        return self.filtered(lambda record: record.state == "open")

    @api.model
    def _is_blank_answer(self, answer):
        """Whether answer shows nothing as the field stores it: sanitized, so a
        whole document or an embedded frame can be stored blank.
        """
        return is_blank_html(self._fields["answer"].convert_to_cache(answer, self.browse()))

    @api.model
    def _prepare_state_values(self, vals):
        state_vals = dict(vals)
        for field_name, value in self._get_answer_stamp(vals.get("state")).items():
            state_vals.setdefault(field_name, value)
        return state_vals

    @api.model
    def _get_answer_stamp(self, state):
        """Answered is stamped with the user who gives it (sudo keeps the user)
        and now; Open takes the stamp back; Dropped leaves it.
        """
        if state == "answered":
            return {"answered_by_id": self.env.user.id, "answered_on": fields.Datetime.now()}
        if state == "open":
            return dict.fromkeys(ANSWER_STAMP_FIELDS, False)
        return {}
