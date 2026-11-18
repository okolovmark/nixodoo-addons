from functools import partial

from odoo import api, fields, models
from odoo.osv import expression

from .html_markdown import get_linked_attachment_ids, html_to_markdown

# Declared by other modules, so read only where the model has them.
OPTIONAL_BRIEF_FIELDS = ("planned_date_start", "planned_date_end", "effective_hours", "uat_user_ids")
BRIEF_TASK_FIELDS = (
    "key",
    "name",
    "project_id",
    "stage_id",
    "date_last_stage_update",
    "task_type",
    "planned_hours",
    "tshirt_estimate",
    "priority_level",
    "date_deadline",
    "user_ids",
    "create_uid",
    "create_date",
    "resolution",
    "develops_this_odoo",
    "module_ids",
    "module_names",
    "tag_ids",
    "parent_id",
    "child_ids",
    "depend_on_ids",
    "dependent_ids",
    "description",
    "solution",
    "testcase_general_comments",
)
BRIEF_LINE_FIELDS = {
    "pull_request_ids": ("url", "state", "merge_date", "deploy_date"),
    "question_ids": (
        "sequence",
        "round",
        "state",
        "addressed_to_id",
        "question",
        "answer",
        "answered_by_id",
        "answered_on",
    ),
    "checklist_item_ids": ("state", "name", "user_id", "reviewer_id", "deadline"),
    "testcase_ids": (
        "sequence",
        "name",
        "state",
        "verified_by_id",
        "verified_on",
        "verification_kind",
        "test_reference",
        "steps_to_execute",
        "expected_results",
        "comments",
    ),
    "session_ids": ("date", "user_id", "session_id", "outcome"),
}
RELATED_TASK_FIELDS = (
    ("Parent", "parent_id"),
    ("Sub-task", "child_ids"),
    ("Blocked by", "depend_on_ids"),
    ("Blocking", "dependent_ids"),
)
BRIEF_NOTE_LIMIT = 10
TEXT_HEADING_LEVEL = 3
ROW_TEXT_HEADING_LEVEL = 4
TASK_FORM_LINK = "%s/web#id=%d&model=project.task&view_type=form"


def filter_readable(records):
    """The records the user of their environment may read: none of a model they may not read."""
    if not records.check_access_rights("read", raise_exception=False):
        return records.browse()
    return records._filter_access_rules("read")


def format_value(record, fname):
    """The value of a field of record as one line of text, "" when empty, so that no name opens a block of
    the brief. A many-to-one shows its name as a read of record does, whoever may read its target.
    """
    return " ".join(_get_value_text(record, fname).split())


def _get_value_text(record, fname):
    field = record._fields[fname]
    value = record[fname]
    if not value:
        return ""
    if field.type == "datetime":
        return "%s UTC" % fields.Datetime.to_string(value)
    if field.type == "date":
        return fields.Date.to_string(value)
    if field.type == "selection":
        return dict(field._description_selection(record.env)).get(value, value)
    if field.type == "many2one":
        return value.sudo().display_name or ""
    if field.type in ("one2many", "many2many"):
        return ", ".join(filter_readable(value).mapped("display_name"))
    if field.type == "float":
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def join_parts(*parts, separator=", "):
    return separator.join(part for part in parts if part)


def get_section(title, body):
    return "## %s\n\n%s" % (title, body) if body else ""


class ProjectTask(models.Model):
    _inherit = "project.task"

    brief = fields.Text(
        compute="_compute_brief",
        exportable=False,
        help="The whole task in Markdown for an agent, in one read: its attributes, related tasks, pull"
        " requests, description, questions, solution, checklist, test cases with their comments, sessions,"
        " the last 10 internal notes and its attachments. Nothing is cut but the notes. Leaves out what the"
        " reader may not read, and is left out of a read that names no fields. An attachment a text shows"
        " or links by a relative url or one on this database's host, as get_base_url() gives it (a"
        " website's domain where it applies), is a pointer, whatever the form of its url: ![image: <name>]"
        "(attachment <id>) or ![image](attachment <id>) for an image, [<link text>](attachment <id>),"
        " [file: <name>](attachment <id>) or [file](attachment <id>) for a link. An image not yet moved to an"
        " attachment shows as [image not yet moved to an attachment], one linked by another url as a"
        " Markdown image. Tokens and credentials are removed from urls only: the url of a link or an image,"
        " and one that stands in the text as a word of its own, loses its query and fragment parameters"
        " named like a token, with the hash and pid that sign a portal link, and its user and password (in"
        " the text, those of an http(s) url only). A token written loose in text or code, outside a url, is"
        " left as written; a url the text splits with markup (bold, a line break), or that holds a brace, <"
        " or >, or a bracket or a quote in its query, may keep its token. A link"
        " of a scheme other than http(s) or mailto keeps only its text. Text that would start a Markdown"
        " heading, quote, list, code fence, table or HTML block is escaped. Times in UTC.",
    )

    # Meant to be read, not watched: what lies beyond these fields shows on the next read.
    @api.depends(lambda self: self._get_brief_depends())
    @api.depends_context("uid", "allowed_company_ids", "lang")
    def _compute_brief(self):
        # a read checks this already, an attribute read from the cache does not
        self.check_access_rights("read")
        self.filtered("id").check_access_rule("read")
        readable_fnames = self._get_brief_readable_fnames()
        for record in self:
            record.brief = record._get_brief(readable_fnames) if record.id else False

    def check_field_access_rights(self, operation, field_names):
        readable_fnames = super().check_field_access_rights(operation, field_names)
        # a read naming no fields would compute every task's brief; read() cannot drop it itself:
        # project's read() refuses a portal user an explicit field list it lets through as None
        if not field_names:
            return [fname for fname in readable_fnames if fname != "brief"]
        return readable_fnames

    @api.model
    def _get_brief_depends(self):
        line_fnames = [
            "%s.%s" % (fname, line_fname)
            for fname, line_fnames in BRIEF_LINE_FIELDS.items()
            for line_fname in line_fnames
        ]
        optional_fnames = [fname for fname in OPTIONAL_BRIEF_FIELDS if fname in self._fields]
        return [*BRIEF_TASK_FIELDS, *optional_fnames, *line_fnames]

    def _get_brief_readable_fnames(self):
        """The fields of the brief the reader may read by their groups, as a read checks them; listed, not the
        fields of a read naming none, which another module may leave fields out of.
        """
        fnames = [*BRIEF_TASK_FIELDS, *OPTIONAL_BRIEF_FIELDS, *BRIEF_LINE_FIELDS]
        brief_fields = [self._fields[fname] for fname in fnames if fname in self._fields]
        return {
            field.name
            for field in brief_fields
            if self.env.su or not field.groups or self.user_has_groups(field.groups)
        }

    def _get_brief(self, readable_fnames):
        self.ensure_one()
        lines = {fname: self._get_brief_records(fname, readable_fnames) for fname in BRIEF_LINE_FIELDS}
        notes = self._get_brief_notes()
        attachments = self._get_brief_attachments()
        general_comments = self.testcase_general_comments if "testcase_general_comments" in readable_fnames else False
        texts = [
            self.description,
            self.solution,
            general_comments,
            *lines["question_ids"].mapped("question"),
            *lines["question_ids"].mapped("answer"),
            *lines["testcase_ids"].mapped("steps_to_execute"),
            *lines["testcase_ids"].mapped("expected_results"),
            *lines["testcase_ids"].mapped("comments"),
            *notes.mapped("body"),
        ]
        to_markdown = partial(
            html_to_markdown,
            attachment_names=self._get_brief_attachment_names(texts, attachments),
            base_url=self.get_base_url(),
        )
        sections = [
            self._get_brief_header(readable_fnames),
            self._get_brief_related_tasks(readable_fnames),
            self._get_brief_pull_requests(lines["pull_request_ids"]),
            get_section("Description", to_markdown(self.description, heading_level=TEXT_HEADING_LEVEL)),
            self._get_brief_questions(lines["question_ids"], to_markdown),
            get_section("Solution", to_markdown(self.solution, heading_level=TEXT_HEADING_LEVEL)),
            self._get_brief_checklist(lines["checklist_item_ids"]),
            self._get_brief_test_cases(lines["testcase_ids"], general_comments, to_markdown),
            self._get_brief_sessions(lines["session_ids"]),
            self._get_brief_notes_section(notes, to_markdown),
            self._get_brief_attachments_section(attachments),
        ]
        return "\n\n".join(section for section in sections if section) + "\n"

    def _get_brief_records(self, fname, readable_fnames):
        """The records of a relational field the reader may read: none where the field's groups keep it from them."""
        if fname not in readable_fnames:
            return self.env[self._fields[fname].comodel_name]
        return filter_readable(self[fname])

    def _get_brief_notes(self):
        """The last internal notes with a body the reader may read, oldest first."""
        domain = expression.AND(
            [
                self.env["mail.message"]._get_task_note_domain(),
                [("task_id", "=", self.id), ("body", "not in", ["", False])],
            ]
        )
        notes = self.env["mail.message"].search(domain, order="date desc, id desc", limit=BRIEF_NOTE_LIMIT)
        return notes[::-1]

    def _get_brief_attachments(self):
        return self.env["ir.attachment"].search([("res_model", "=", self._name), ("res_id", "=", self.id)], order="id")

    def _get_brief_attachment_names(self, texts, attachments):
        """{attachment id: name} of the attachments the texts show or link that the reader may read."""
        linked_ids = set().union(*map(get_linked_attachment_ids, texts))
        attachment_names = {attachment.id: attachment.name for attachment in attachments if attachment.id in linked_ids}
        other_ids = linked_ids - set(attachment_names)
        if other_ids:
            other_attachments = self.env["ir.attachment"].search([("id", "in", list(other_ids))])
            attachment_names.update((attachment.id, attachment.name) for attachment in other_attachments)
        return attachment_names

    def _get_brief_key(self, readable_fnames):
        """The task's key, else its id."""
        return (format_value(self, "key") if "key" in readable_fnames else "") or "#%d" % self.id

    def _get_brief_header(self, readable_fnames):
        def value(fname):
            return format_value(self, fname) if fname in readable_fnames else ""

        since = value("date_last_stage_update")
        estimate = value("planned_hours")
        size = value("tshirt_estimate")
        planned_start = value("planned_date_start")
        planned_end = value("planned_date_end")
        hours_spent = value("effective_hours")
        created = value("create_date")
        rows = [
            ("Project", value("project_id")),
            ("Stage", join_parts(value("stage_id"), since and "since %s" % since)),
            ("Type", value("task_type")),
            ("Estimate", join_parts(estimate and "%s h" % estimate, size and "size %s" % size)),
            ("Priority", value("priority_level")),
            (
                "Planned",
                join_parts(
                    planned_start and "from %s" % planned_start, planned_end and "to %s" % planned_end, separator=" "
                ),
            ),
            ("Deadline", value("date_deadline")),
            ("Hours spent", hours_spent and "%s h" % hours_spent),
            ("Assignees", value("user_ids")),
            ("UAT users", value("uat_user_ids")),
            ("Author", join_parts(value("create_uid"), created and "created %s" % created)),
            ("Resolution", value("resolution")),
            ("Modules", value("module_names") if self.develops_this_odoo else ""),
            ("Tags", value("tag_ids")),
            ("Link", TASK_FORM_LINK % (self.get_base_url(), self.id)),
        ]
        title = "# %s %s" % (self._get_brief_key(readable_fnames), " ".join(self.name.split()))
        return "\n".join([title, ""] + ["- %s: %s" % (label, text) for label, text in rows if text])

    def _get_brief_related_tasks(self, readable_fnames):
        lines = []
        for label, fname in RELATED_TASK_FIELDS:
            for task in self._get_brief_records(fname, readable_fnames):
                stage = format_value(task, "stage_id")
                task_name = join_parts(task._get_brief_key(readable_fnames), format_value(task, "name"), separator=" ")
                lines.append("- %s: %s%s" % (label, task_name, " (%s)" % stage if stage else ""))
        return get_section("Related tasks", "\n".join(lines))

    def _get_brief_pull_requests(self, pull_requests):
        lines = [
            "- %s"
            % join_parts(
                format_value(pull_request, "url"),
                format_value(pull_request, "state"),
                pull_request.merge_date and "merged %s" % format_value(pull_request, "merge_date"),
                pull_request.deploy_date and "deployed %s" % format_value(pull_request, "deploy_date"),
            )
            for pull_request in pull_requests
        ]
        return get_section("Pull requests", "\n".join(lines))

    def _get_brief_questions(self, questions, to_markdown):
        blocks = []
        for question in questions:
            answered_on = format_value(question, "answered_on")
            details = [
                ("Round", format_value(question, "round")),
                ("Addressed to", format_value(question, "addressed_to_id")),
                (
                    "Answered by",
                    join_parts(format_value(question, "answered_by_id"), answered_on and "on %s" % answered_on),
                ),
            ]
            blocks.append("### Question %d (%s)" % (question.id, format_value(question, "state")))
            blocks.extend(self._get_brief_details(details))
            blocks.extend(
                self._get_brief_texts(question, (("Question", "question"), ("Answer", "answer")), to_markdown)
            )
        return get_section("Questions and answers", "\n\n".join(blocks))

    def _get_brief_checklist(self, items):
        """Progress counts the items not cancelled: a cancelled item is no work left nor done."""
        if not items:
            return ""
        counted_items = items.filtered(lambda item: item.state != "cancelled")
        done_count = len(counted_items.filtered(lambda item: item.state == "done"))
        lines = []
        for item in items:
            deadline = format_value(item, "deadline")
            details = join_parts(
                "assigned to %s" % format_value(item, "user_id"),
                item.reviewer_id and "reviewer %s" % format_value(item, "reviewer_id"),
                deadline and "deadline %s" % deadline,
            )
            lines.append(
                "- %d %s: %s (%s)" % (item.id, format_value(item, "state"), format_value(item, "name"), details)
            )
        title = "Checklist (%d done of %d, cancelled not counted)" % (done_count, len(counted_items))
        return get_section(title, "\n".join(lines))

    def _get_brief_test_cases(self, test_cases, general_comments, to_markdown):
        general_markdown = to_markdown(general_comments, heading_level=ROW_TEXT_HEADING_LEVEL)
        blocks = ["### General comments\n\n%s" % general_markdown] if general_markdown else []
        for test_case in test_cases.sorted(lambda record: (record.sequence, record.id)):
            verified_on = format_value(test_case, "verified_on")
            details = [
                (
                    "Verified by",
                    join_parts(
                        format_value(test_case, "verified_by_id"),
                        verified_on and "on %s" % verified_on,
                        format_value(test_case, "verification_kind"),
                    ),
                ),
                ("Test reference", format_value(test_case, "test_reference")),
            ]
            blocks.append(
                "### Test case %d: %s (%s)"
                % (test_case.id, format_value(test_case, "name"), format_value(test_case, "state"))
            )
            blocks.extend(self._get_brief_details(details))
            texts = (("Steps", "steps_to_execute"), ("Expected results", "expected_results"), ("Comments", "comments"))
            blocks.extend(self._get_brief_texts(test_case, texts, to_markdown))
        return get_section("Test cases", "\n\n".join(blocks))

    def _get_brief_sessions(self, sessions):
        lines = []
        for session in sessions:
            outcome = format_value(session, "outcome")
            session_text = join_parts(
                format_value(session, "date"),
                format_value(session, "user_id"),
                "session %s" % format_value(session, "session_id"),
            )
            lines.append("- %s%s" % (session_text, ": %s" % outcome if outcome else ""))
        return get_section("Sessions", "\n".join(lines))

    def _get_brief_notes_section(self, notes, to_markdown):
        blocks = []
        for note in notes:
            author = format_value(note, "author_id") or format_value(note, "email_from")
            blocks.append(
                "### %s" % join_parts(format_value(note, "date"), author, format_value(note, "note_category"))
            )
            blocks.append(to_markdown(note.body, heading_level=ROW_TEXT_HEADING_LEVEL))
        return get_section(
            "Internal notes (last %d)" % BRIEF_NOTE_LIMIT, "\n\n".join(block for block in blocks if block)
        )

    def _get_brief_attachments_section(self, attachments):
        lines = [
            "- %s (%s)"
            % (
                format_value(attachment, "name"),
                join_parts(
                    "attachment %d" % attachment.id,
                    format_value(attachment, "mimetype"),
                    "%d bytes" % attachment.file_size,
                ),
            )
            for attachment in attachments
        ]
        return get_section("Attachments", "\n".join(lines))

    @staticmethod
    def _get_brief_details(details):
        lines = ["- %s: %s" % (label, text) for label, text in details if text]
        return ["\n".join(lines)] if lines else []

    @staticmethod
    def _get_brief_texts(record, labelled_fnames, to_markdown):
        texts = []
        for label, fname in labelled_fnames:
            markdown = to_markdown(record[fname], heading_level=ROW_TEXT_HEADING_LEVEL)
            if markdown:
                texts.append("%s:\n\n%s" % (label, markdown))
        return texts
