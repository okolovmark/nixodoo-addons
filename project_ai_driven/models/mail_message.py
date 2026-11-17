import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression

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
# Grouped as superuser: a many2many group-by would skip the record rules of its comodel.
TASK_MESSAGE_GROUPBY_FIELDS = ("note_category", "task_id", "author_id", "date", "model", "message_type", "subtype_id")
# Spelled out: PostgreSQL and Python do not read \s alike, and the Html sanitizer stores U+00A0 as &nbsp;.
SPACE = "(?:[ \t\r\n\u00a0]|&nbsp;)"


def _any_case(text):
    """text as a pattern of its letters in either case: no flag folds case, as
    Python, PostgreSQL and its collations each fold letters outside ASCII their
    own way.
    """
    return "".join("[%s%s]" % (char.upper(), char.lower()) if char.isalpha() else char for char in text)


_BODY_START = "^%s*" % SPACE
_SPAN = _any_case("span")
SPAN_OPENING = "%s<%s" % (_BODY_START, _SPAN)


def _get_note_category_pattern(label):
    # the Html sanitizer stores inside a <span> a body of more than one node that opens with an
    # inline tag and holds no block tag
    return (
        "{body_start}(?:<(?:{p}|{span})(?:{space}[^>]*)?>{space}*)?<(?:{b}|{strong})(?:{space}[^>]*)?>{space}*"
        "{label}{space}*(?:</(?:{b}|{strong})>|[-:/+(\u2013\u2014])"
    ).format(
        body_start=_BODY_START,
        space=SPACE,
        p=_any_case("p"),
        span=_SPAN,
        b=_any_case("b"),
        strong=_any_case("strong"),
        label=(SPACE + "+").join(_any_case(word) for word in label.split()),
    )


# Read by Python and by PostgreSQL alike: the opening of a note of each category.
NOTE_CATEGORY_PATTERNS = {category: _get_note_category_pattern(label) for category, label in NOTE_CATEGORIES.items()}
NOTE_CATEGORY_REGEXES = {category: re.compile(pattern) for category, pattern in NOTE_CATEGORY_PATTERNS.items()}


def get_note_category(body):
    """The category whose name opens body in bold, or False."""
    body_text = str(body or "")
    return next((category for category, regex in NOTE_CATEGORY_REGEXES.items() if regex.match(body_text)), False)


class MailMessage(models.Model):
    _inherit = "mail.message"

    task_id = fields.Many2one(
        "project.task", compute="_compute_task_id", store=True, precompute=True, index="btree_not_null"
    )
    is_task_readable = fields.Boolean(compute="_compute_is_task_readable", search="_search_is_task_readable")
    note_category = fields.Selection(
        list(NOTE_CATEGORIES.items()),
        "Category",
        compute="_compute_note_category",
        store=True,
        precompute=True,
        index="btree_not_null",
        group_expand="_group_expand_note_category",
    )

    @api.depends("model", "res_id")
    def _compute_task_id(self):
        task_messages = self.filtered(lambda message: message.model == NOTE_MODEL)
        # res_id is no foreign key: it can name a task deleted since
        existing_task_ids = set(self.env[NOTE_MODEL].browse(task_messages.mapped("res_id")).exists().ids)
        for message in self:
            is_task_message = message.model == NOTE_MODEL and message.res_id in existing_task_ids
            message.task_id = message.res_id if is_task_message else False

    @api.depends_context("uid", "allowed_company_ids")
    @api.depends("task_id")
    def _compute_is_task_readable(self):
        tasks = self.task_id
        if not tasks.check_access_rights("read", raise_exception=False):
            tasks = tasks.browse()
        readable_tasks = tasks._filter_access_rules("read")
        for message in self:
            message.is_task_readable = message.task_id in readable_tasks

    def _search_is_task_readable(self, operator, value):
        """The messages of the tasks the user can read, archived ones included,
        as SQL: _search drops the others only after its limit, which could
        leave a page of the list empty.
        """
        if operator not in ("=", "!=") or not isinstance(value, int) or value not in (0, 1):
            raise UserError(_("Whether the task of a message is readable is searched as true or false only."))
        tasks = self.env[NOTE_MODEL].with_context(active_test=False)
        readable_tasks = tasks._search([]) if tasks.check_access_rights("read", raise_exception=False) else []
        if (operator == "=") == bool(value):
            return [("task_id", "in", readable_tasks)]
        return ["|", ("task_id", "=", False), ("task_id", "not in", readable_tasks)]

    @api.depends("body", "model", "subtype_id", "tracking_value_ids")
    def _compute_note_category(self):
        """An internal note of a task, of any message type, that tracks no
        field, whose body opens, after at most one <p> or <span>, with a
        category in <b> or <strong>: alone in it, or followed there by a
        separator and more words.
        """
        note_subtype_id = self.env["ir.model.data"]._xmlid_to_res_id(NOTE_SUBTYPE)
        body_field = self._fields["body"]
        for message in self:
            is_task_note = (
                message.model == NOTE_MODEL
                and message.subtype_id.id == note_subtype_id
                and not message.tracking_value_ids
            )
            if not is_task_note:
                message.note_category = False
                continue
            # precomputed, a new message holds its body as given: the column gets it sanitized
            body = message.body if message.id else body_field.convert_to_cache(message.body, message)
            message.note_category = get_note_category(body)

    @api.model
    def _group_expand_note_category(self, categories, domain, order):
        return [category for category in NOTE_CATEGORIES if category in categories]

    @api.model
    def _read_group_raw(self, domain, fields, groupby, offset=0, limit=None, orderby=False, lazy=True):
        """Core groups messages for administrators alone: a grouped read skips
        the document check of _search. A project user groups the messages of a
        domain on is_task_readable, see _get_readable_task_message_query, by
        TASK_MESSAGE_GROUPBY_FIELDS; the domain of each group is built on the
        caller's, as the client sends it back to read the group.
        """
        is_task_message_read = self._is_task_message_domain(domain) and self._is_task_message_groupby(groupby)
        if self.env.is_admin() or not is_task_message_read:
            return super()._read_group_raw(
                domain, fields, groupby, offset=offset, limit=limit, orderby=orderby, lazy=lazy
            )
        readable_query = self._get_readable_task_message_query(domain)
        groups = super(MailMessage, self.sudo())._read_group_raw(
            [("id", "in", readable_query)], fields, groupby, offset=offset, limit=limit, orderby=orderby, lazy=lazy
        )
        caller_domain = expression.normalize_domain(domain)
        for group in groups:
            # core ends the domain of a group with the one it grouped: the query, which stays here
            if "__domain" in group:
                group["__domain"] = group["__domain"][:-1] + caller_domain
        return groups

    @api.model
    def _is_task_message_domain(self, domain):
        return self.env.user.has_group("project.group_project_user") and any(
            expression.is_leaf(element) and element[0] == "is_task_readable" for element in domain
        )

    @api.model
    def _is_task_message_groupby(self, groupby):
        groupby_specs = [groupby] if isinstance(groupby, str) else groupby
        return all(
            isinstance(spec, str) and spec.split(":")[0] in TASK_MESSAGE_GROUPBY_FIELDS for spec in groupby_specs
        )

    @api.model
    def _get_readable_task_message_query(self, domain):
        """The messages of domain on tasks the user can read, messages to one
        user aside, as an SQL query run as the user: what the document check
        of _search keeps of them.
        """
        self.check_access_rights("read")
        readable_domain = expression.AND(
            [domain, [("is_task_readable", "=", True), ("message_type", "!=", "user_notification")]]
        )
        self._flush_search(readable_domain)
        query = self._where_calc(readable_domain)
        self._apply_ir_rules(query, "read")
        return query

    @api.model
    def _get_task_note_domain(self):
        """The internal notes of tasks their chatter shows: no message to one
        user, no change of fields tracked without a note or a file.
        """
        return [
            ("is_task_readable", "=", True),
            ("subtype_id", "=", self.env["ir.model.data"]._xmlid_to_res_id(NOTE_SUBTYPE)),
            ("message_type", "!=", "user_notification"),
            "|",
            ("body", "not in", ["", False]),
            ("attachment_ids", "!=", False),
        ]
