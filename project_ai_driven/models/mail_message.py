import re

from odoo import api, fields, models

NOTE_CATEGORIES = {
    "requirement_change": "Requirement Change",
    "design_decision": "Design Decision",
    "bug_fix": "Bug Fix",
    "review_fix": "Review Fix",
    "user_feedback": "User Feedback",
    "progress": "Progress",
    "revised": "Revised",
    "deploy": "Deploy",
}
NOTE_MODEL = "project.task"
NOTE_SUBTYPE = "mail.mt_note"
# Spelled out: \s matches every Unicode space, and the Html sanitizer stores U+00A0 as &nbsp;.
SPACE = "(?:[ \t\r\n\u00a0]|&nbsp;)"


def _any_case(text):
    """text as a pattern of its letters in either case: re.IGNORECASE also
    matches letters outside ASCII that it folds onto them, as U+017F onto s.
    """
    return "".join("[%s%s]" % (char.upper(), char.lower()) if char.isalpha() else char for char in text)


def _get_note_category_pattern(label):
    # the Html sanitizer stores inside a <span> a body of more than one node that opens with an
    # inline tag and holds no block tag
    return (
        "{body_start}(?:<(?:{p}|{span})(?:{space}[^>]*)?>{space}*)?<(?:{b}|{strong})(?:{space}[^>]*)?>{space}*"
        "{label}{space}*(?:</(?:{b}|{strong})>|[-:/+(\u2013\u2014])"
    ).format(
        body_start="^%s*" % SPACE,
        space=SPACE,
        p=_any_case("p"),
        span=_any_case("span"),
        b=_any_case("b"),
        strong=_any_case("strong"),
        label=(SPACE + "+").join(_any_case(word) for word in label.split()),
    )


NOTE_CATEGORY_REGEXES = {
    category: re.compile(_get_note_category_pattern(label)) for category, label in NOTE_CATEGORIES.items()
}


def get_note_category(body):
    """The category whose name opens body in bold, or False."""
    body_text = str(body or "")
    return next((category for category, regex in NOTE_CATEGORY_REGEXES.items() if regex.match(body_text)), False)


class MailMessage(models.Model):
    _inherit = "mail.message"

    task_id = fields.Many2one(
        "project.task", compute="_compute_task_id", store=True, precompute=True, index="btree_not_null"
    )
    note_category = fields.Selection(
        list(NOTE_CATEGORIES.items()),
        "Category",
        compute="_compute_note_category",
        store=True,
        precompute=True,
        index="btree_not_null",
    )

    @api.depends("model", "res_id")
    def _compute_task_id(self):
        task_messages = self.filtered(lambda message: message.model == NOTE_MODEL)
        # res_id is no foreign key: it can name a task deleted since
        existing_task_ids = set(self.env[NOTE_MODEL].browse(task_messages.mapped("res_id")).exists().ids)
        for message in self:
            is_task_message = message.model == NOTE_MODEL and message.res_id in existing_task_ids
            message.task_id = message.res_id if is_task_message else False

    @api.depends("body", "model", "subtype_id", "message_type")
    def _compute_note_category(self):
        """An internal note of a task, of any message type but tracking, whose
        body opens, after at most one <p> or <span>, with a category in <b> or
        <strong>: alone in it, or followed there by a separator and more words.
        """
        note_subtype_id = self.env["ir.model.data"]._xmlid_to_res_id(NOTE_SUBTYPE)
        body_field = self._fields["body"]
        for message in self:
            is_task_note = (
                message.model == NOTE_MODEL
                and message.subtype_id.id == note_subtype_id
                and message.message_type != "tracking"
            )
            if not is_task_note:
                message.note_category = False
                continue
            # precomputed, a new message holds its body as given: the column gets it sanitized
            body = message.body if message.id else body_field.convert_to_cache(message.body, message)
            message.note_category = get_note_category(body)

    @api.model
    def _get_task_note_domain(self):
        """The internal notes of the tasks the user can read: no message to one
        user, no tracking of changed fields, none without a body or a file.

        The task access leaf drops the notes core lets a user read on a task
        they cannot: their own and those sent to them. The model leaf keeps the
        access check of _search in SQL.
        """
        return [
            ("model", "=", NOTE_MODEL),
            ("task_id", "access", "read"),
            ("subtype_id", "=", self.env["ir.model.data"]._xmlid_to_res_id(NOTE_SUBTYPE)),
            ("message_type", "not in", ["user_notification", "tracking"]),
            "|",
            ("body", "not in", ["", False]),
            ("attachment_ids", "!=", False),
        ]
