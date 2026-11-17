import logging
from ast import literal_eval
from datetime import datetime, timedelta
from unittest.mock import patch

from lxml import etree
from markupsafe import Markup

from odoo.exceptions import AccessError

from ..models.mail_message import NOTE_CATEGORIES, get_note_category
from .common import MODULE, ProjectAiDrivenCommon

ACTION = "%s.mail_message_action_task_note" % MODULE
# Bodies as a note is posted with them, and the category each one opens with.
CATEGORY_OPENINGS = {
    "<p><b>Requirement Change</b> - the scope is cut</p>": "requirement_change",
    "<p><b>Design Decision \u2014 defect 2</b></p><p>Kept as it is.</p>": "design_decision",
    "<p><strong>bug fix</strong>: the total is rounded</p>": "bug_fix",
    "<p><b>Review Fix</b> after the second review</p>": "review_fix",
    "<p><b>User Feedback:</b> a filter is missing</p>": "user_feedback",
    "<p><b>Progress: Test Case Verification 2/2</b></p>": "progress",
    "<p><b>REVISED</b></p><ul><li>the estimate</li></ul>": "revised",
    "<p><b>Deploy \u2013 prod</b> on Monday</p>": "deploy",
    # the sanitizer stores a no-break space as &nbsp;, and drops a comment
    "<p><b>Bug\u00a0Fix</b>: the total</p>": "bug_fix",
    "<p>\u00a0<b>Progress</b> - in review</p>": "progress",
    "<p><b>Progress\u00a0</b>- in review</p>": "progress",
    "<p><b>Progress&#160;</b>- in review</p>": "progress",
    "<!-- pasted --><p><b>Progress</b> - in review</p>": "progress",
    "<p><b>Bug Fix / Review Fix</b></p>": "bug_fix",
    "<p><b>Review Fix + Bug Fix</b></p>": "review_fix",
    "<p><b>Bug Fix (round 2)</b></p>": "bug_fix",
    # the sanitizer stores inside a <span> a body of more than one node that opens with an inline
    # tag and holds no block tag
    "<b>Progress</b> \u2014 Test Case Verification: 7/7 passed.": "progress",
    "<b>Review Fix</b> - posted without a paragraph": "review_fix",
    "<span><b>Progress</b> - posted without a paragraph</span>": "progress",
    '<SPAN class="x"><b>Deploy</b> - prod</SPAN>': "deploy",
    "<span>\u00a0<b>Deploy</b> - prod</span>": "deploy",
    "<strong>User Feedback:</strong> a filter is missing": "user_feedback",
    "<b>Bug Fix / Review Fix</b> in one": "bug_fix",
}
NO_CATEGORY_OPENINGS = (
    "<p>Shipped.</p><p><b>Progress</b> - in a later paragraph</p>",
    "<p><b>Done</b>: the tests</p>",
    '<p><strong><span style="color:#080">Done</span></strong>: relabel</p>',
    "<p><strong>TODO</strong>: write the tests</p>",
    "<p><b>Code Change</b> - a pull request</p>",
    "<p><b>Test Case Verification:</b> 1/1 passed.</p>",
    "<p><b>Progress report</b> without a separator</p>",
    "<p><b>Deploy notes</b></p>",
    "<p><b>Deployment</b> of the module</p>",
    "<p>Progress, not in bold</p>",
    "<b>Progress report</b> without a separator",
    "<b>Deploy notes</b> for prod",
    "<span><p><b>Progress</b> - two wrappers</p></span>",
    "<p><span><b>Progress</b> - two wrappers</span></p>",
    # a <div> stays when it is not the whole body
    "<div><b>Progress</b> - in a block</div><p>Shipped.</p>",
    # letters re.IGNORECASE would fold onto ASCII ones
    "<p><b>REV\u0130SED</b> plan</p>",
    "<p><b>Bug F\u0131x</b>: the total</p>",
    "<p><b>Progre\u017fs</b> - in review</p>",
)
# Bodies the sanitizer would rewrite, read by the rule as given.
RAW_CATEGORY_OPENINGS = {
    "  \n<p>\n<b>Progress</b></p>": "progress",
    '<P class="x"><STRONG style="color: red">Design\n Decision</STRONG></P>': "design_decision",
    "<p>\u00a0<b>Bug\u00a0Fix\u00a0\u2014\u00a0total</b></p>": "bug_fix",
    "<p><b>\tDeploy:</b></p>": "deploy",
    "<b>Review Fix</b> without a paragraph": "review_fix",
    "<span><b>Progress</b></span>": "progress",
    '<SPAN class="x"><b>Deploy</b></SPAN>': "deploy",
    "<span>&nbsp;<b>Deploy</b></span>": "deploy",
    " \n<Span>\n<strong>User\u00a0Feedback:</strong></Span>": "user_feedback",
    "&nbsp;<span><b>Deploy</b> - prod</span>": "deploy",
    "\u00a0<span><b>Progress</b></span>": "progress",
    "<Span\n><b>Review Fix</b></Span>": "review_fix",
    "<span><b>Bug Fix (round 2)</b></span>": "bug_fix",
    "<span><b>Review Fix + Bug Fix</b></span>": "review_fix",
}
RAW_NO_CATEGORY_OPENINGS = (
    "<p><p><b>Progress</b></p></p>",
    "<div><b>Progress</b></div>",
    "<pre><b>Progress</b></pre>",
    "<p><bdi>Progress</bdi></p>",
    "<span><p><b>Progress</b></p></span>",
    "<p><span><b>Progress</b></span></p>",
    "<span><span><b>Progress</b></span></span>",
    "<small><b>Progress</b></small>",
    "<spanx><b>Progress</b></spanx>",
    "<span><b>Progress report</b></span>",
    "<span><b>Deploy notes</b></span>",
)
TRACKING_BODY = "<p><b>Progress</b> - the deadline moved</p>"


class NoteCategoryCase(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Message = cls.env["mail.message"]

    def _log_note(self, body, record=None, message_type="comment", subtype_xmlid="mail.mt_note"):
        return (record or self.task).message_post(
            body=Markup(body), message_type=message_type, subtype_xmlid=subtype_xmlid
        )

    def _log_tracking(self, body=None):
        """The message of a change of a tracked field of the task, logged with
        body when one is given.
        """
        self._flush_tracking()
        if body:
            self.task._track_set_log_message(Markup(body))
        self.task.date_deadline = (self.task.date_deadline or datetime(2026, 12, 1)) + timedelta(days=1)
        self._flush_tracking()
        return self.task.message_ids.filtered(lambda message: message.message_type == "tracking")[0]

    def _categories(self, messages):
        return [message.note_category for message in messages]

    def _stored_values(self, messages):
        self.env.invalidate_all()
        return [(message.task_id.id, message.note_category) for message in messages]

    def _computed_values(self, messages):
        """The values the computes give messages as they are stored."""
        self.env.invalidate_all()
        for field_name in ("task_id", "note_category"):
            self.env.add_to_compute(self.Message._fields[field_name], messages)
        computed_values = [(message.task_id.id, message.note_category) for message in messages]
        self.env.flush_all()
        return computed_values

    def _assert_categories(self, notes, categories, message=None):
        self.assertEqual(self._stored_values(notes), [(self.task.id, category) for category in categories], message)
        self.assertEqual(
            self._computed_values(notes), [(self.task.id, category) for category in categories], "from the stored body"
        )


class TestNoteCategory(NoteCategoryCase):
    def test_a_note_opening_with_a_category_in_bold_has_it(self):
        for message_type in ("comment", "notification"):
            notes = self.Message.concat(self._log_note(body, message_type=message_type) for body in CATEGORY_OPENINGS)
            self._assert_categories(notes, CATEGORY_OPENINGS.values(), message_type)

    def test_a_note_posted_with_an_inline_body_has_its_category(self):
        note = self._log_note("<b>Progress</b> \u2014 Test Case Verification: 7/7 passed.")
        self.assertEqual(str(note.body), "<span><b>Progress</b> \u2014 Test Case Verification: 7/7 passed.</span>")
        self._assert_categories(note, ["progress"])

    def test_a_note_opening_otherwise_has_none(self):
        notes = self.Message.concat(self._log_note(body) for body in NO_CATEGORY_OPENINGS)
        self._assert_categories(notes, [False] * len(NO_CATEGORY_OPENINGS))

    def test_a_category_counts_only_on_an_internal_note_of_a_task(self):
        body = "<p><b>Progress</b> - shipped</p>"
        to_followers = self._log_note(body, subtype_xmlid="mail.mt_comment")
        on_a_project = self._log_note(body, record=self.project)
        to_one_user = self.task.message_notify(partner_ids=self.user_dev.partner_id.ids, body=Markup(body))
        self.assertEqual(
            (to_one_user.message_type, to_one_user.subtype_id), ("user_notification", self.env.ref("mail.mt_note"))
        )
        self.assertEqual(self._categories(to_followers | on_a_project | to_one_user), [False, False, "progress"])

    def test_tracking_has_no_category(self):
        tracking = self._log_tracking(TRACKING_BODY)
        self.assertEqual(tracking.subtype_id, self.env.ref("mail.mt_note"))
        self.assertTrue(str(tracking.body).startswith(TRACKING_BODY), "the logged message, then the tracked values")
        self.assertEqual(get_note_category(tracking.body), "progress")
        self.assertEqual(self._categories(tracking), [False])

    def test_an_edited_note_is_categorized_again(self):
        note = self._log_note("<p><b>Progress</b> - in review</p>")
        self.task._message_update_content(note, body=Markup("<p><b>Deploy</b> - prod</p>"))
        self.assertEqual(note.note_category, "deploy")
        self.task._message_update_content(note, body=Markup("<p>Nothing to report</p>"))
        self.assertFalse(note.note_category)

    def test_a_message_moved_retyped_or_resubtyped_gets_its_task_and_category_again(self):
        note = self._log_note("<p><b>Progress</b> - moved</p>", record=self.project)
        self.assertEqual(self._stored_values(note), [(False, False)])
        note.write({"model": "project.task", "res_id": self.task.id})
        self.assertEqual(self._stored_values(note), [(self.task.id, "progress")])
        note.subtype_id = self.env.ref("mail.mt_comment")
        self.assertEqual(self._stored_values(note), [(self.task.id, False)])
        note.subtype_id = self.env.ref("mail.mt_note")
        self.assertEqual(self._stored_values(note), [(self.task.id, "progress")])
        note.message_type = "tracking"
        self.assertEqual(self._stored_values(note), [(self.task.id, False)])

    def test_a_new_message_is_inserted_with_its_task_and_category(self):
        self.env.flush_all()
        with self.assertLogs("odoo.sql_db", logging.DEBUG) as logs:
            messages = self._log_note("<p><b>Deploy</b> - prod</p>") | self._log_note(
                "<p><b>Deploy</b> - prod</p>", record=self.project
            )
            self.env.flush_all()
        updates = [
            record.getMessage()
            for record in logs.records
            if 'UPDATE "mail_message"' in record.getMessage()
            and ('"task_id"' in record.getMessage() or '"note_category"' in record.getMessage())
        ]
        self.assertEqual(updates, [])
        self.assertEqual(self._stored_values(messages), [(self.task.id, "deploy"), (False, False)])

    def test_a_message_naming_a_missing_task_has_no_task_and_is_no_task_note(self):
        missing_task_id = self.env["project.task"].with_context(active_test=False).search([], limit=1).id + 10**9
        message = self.Message.create(
            {
                "model": "project.task",
                "res_id": missing_task_id,
                "reply_to": "reply@example.com",
                "body": Markup("<p><b>Deploy</b></p>"),
                "subtype_id": self.env.ref("mail.mt_note").id,
            }
        )
        self.assertEqual(self._stored_values(message), [(False, "deploy")])
        self.assertFalse(self.Message.search([*self.Message._get_task_note_domain(), ("id", "=", message.id)]))

    def test_the_rule_reads_raw_bodies(self):
        bodies = {**RAW_CATEGORY_OPENINGS, **dict.fromkeys(RAW_NO_CATEGORY_OPENINGS, False)}
        self.assertEqual({body: get_note_category(body) for body in bodies}, bodies)


class TestTaskNotes(NoteCategoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.action = cls.env.ref(ACTION)
        cls.hidden_project = cls.env["project.project"].create(
            {"name": "Followers Only", "privacy_visibility": "followers", "user_id": cls.user_lead.id},
        )
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.hidden_project.id})

    def _action_domain(self, tasks=None):
        return literal_eval(self.action.domain) + [("res_id", "in", (tasks or self.task | self.hidden_task).ids)]

    def _action_notes(self, user=None, tasks=None):
        return self.Message.with_user(user or self.env.user).search(self._action_domain(tasks))

    def _log_files(self, record=None):
        return (record or self.task).message_post(
            body="", attachments=[("plan.txt", b"the plan")], subtype_xmlid="mail.mt_note"
        )

    def _log_task_messages(self):
        """The notes of the task the list shows, and its other messages."""
        notes = (
            self._log_note("<p><b>Progress</b> - logged</p>")
            | self._log_note("<p>Posted over the API</p>", message_type="notification")
            | self._log_files()
        )
        self._log_note("<p><b>Progress</b> - to the followers</p>", subtype_xmlid="mail.mt_comment")
        self.task.message_notify(partner_ids=self.user_dev.partner_id.ids, body=Markup("<p>Assigned</p>"))
        self._log_tracking()
        self._log_note("<p><b>Progress</b> - on the project</p>", record=self.project)
        return notes

    def _arch(self, xmlid):
        return etree.fromstring(self.env.ref("%s.%s" % (MODULE, xmlid)).arch)

    def test_the_action_lists_the_internal_notes_of_tasks_and_no_tracking(self):
        notes = self._log_task_messages()
        tracking = self.task.message_ids.filtered(lambda message: message.message_type == "tracking")
        self.assertFalse(notes[-1].body, "a note of files alone")
        self.assertEqual(
            (tracking.subtype_id, bool(tracking.body)), (self.env.ref("mail.mt_note"), True), "a note with a body"
        )
        self.assertEqual(literal_eval(self.action.domain), self.Message._get_task_note_domain())
        self.assertEqual(self._action_notes(), notes)

    def test_the_notes_button_counts_the_notes_of_its_task_and_lists_them(self):
        notes = self._log_task_messages()
        self._log_note("<p><b>Deploy</b></p>", record=self.hidden_task)
        self.assertEqual((self.task | self.hidden_task).mapped("note_count"), [3, 1])
        self.assertEqual(self.task.with_user(self.user_dev).note_count, 3, "counted by a project user")

        notes_action = self.task.action_open_notes()

        self.assertEqual(notes_action["res_model"], "mail.message")
        self.assertEqual(self.Message.search(notes_action["domain"]), notes)

    def test_the_notes_button_counts_a_note_posted_since_it_was_read(self):
        self.assertEqual(self.task.note_count, 0)
        self._log_note("<p>Posted after the form was read</p>")
        self.assertEqual(self.task.note_count, 1)

    def test_a_user_who_cannot_read_a_task_does_not_find_its_notes(self):
        visible_note = self._log_note("<p><b>Progress</b> - visible</p>")
        self._log_note("<p><b>Progress</b> - hidden</p>", record=self.hidden_task)
        with self.assertRaises(AccessError):
            self.hidden_task.with_user(self.user_dev).check_access("read")
        self.assertEqual(self._action_notes(self.user_dev), visible_note)

    def test_a_page_of_the_list_holds_the_notes_the_user_can_read_filtered_in_sql(self):
        visible_note = self._log_note("<p><b>Progress</b> - visible</p>")
        self._log_note("<p><b>Progress</b> - hidden, and newer</p>", record=self.hidden_task)
        user_messages = self.Message.with_user(self.user_dev)
        # without a model to read the access from, _search filters pages of messages in Python
        with patch.object(type(user_messages), "_filtered_access", side_effect=AssertionError("filtered in Python")):
            first_page = user_messages.search(literal_eval(self.action.domain), limit=1)
        self.assertEqual(first_page, visible_note)

    def test_the_task_of_a_message_is_found_by_its_current_name(self):
        task_note = self._log_note("<p>On the task</p>")
        project_note = self._log_note("<p>On the project</p>", record=self.project)
        messages = task_note | project_note
        former_name = self.task.display_name
        self.task.name = "Renamed Task"
        self.assertEqual((task_note.task_id, project_note.task_id), (self.task, self.env["project.task"]))
        self.assertEqual(self.Message.search([("task_id", "in", self.task.ids)]), self.task.message_ids)
        self.assertEqual(self.Message.search([("task_id", "ilike", "Renamed Task")]), self.task.message_ids)
        self.assertFalse(self.Message.search([("task_id", "ilike", former_name), ("id", "in", messages.ids)]))
        self.assertEqual(self.Message.search([("task_id", "=", False), ("id", "in", messages.ids)]), project_note)
        self.assertEqual(self.Message.search([("task_id", "!=", False), ("id", "in", messages.ids)]), task_note)

    def test_a_project_user_groups_the_notes_core_lets_them_read_by_category_and_task(self):
        self._log_note("<p><b>Progress</b> - visible</p>")
        self._log_note("<p><b>Deploy</b> - visible</p>")
        self._log_note("<p><b>Progress</b> - hidden</p>", record=self.hidden_task)
        self.task.name = "Renamed Task"
        user_messages = self.Message.with_user(self.user_dev).with_context(read_group_expand=True)

        category_groups = user_messages.formatted_read_group(self._action_domain(), ["note_category"], ["__count"])
        task_groups = user_messages.formatted_read_group(self._action_domain(), ["task_id"], ["__count"])

        self.assertEqual(
            [(group["note_category"], group["__count"]) for group in category_groups], [("progress", 1), ("deploy", 1)]
        )
        self.assertEqual(
            [(group["task_id"], group["__count"]) for group in task_groups],
            [((self.task.id, "Renamed Task"), 2)],
        )
        all_task_groups = self.Message.formatted_read_group(self._action_domain(), ["task_id"], ["__count"])
        self.assertEqual(
            {group["task_id"][0]: group["__count"] for group in all_task_groups},
            {self.task.id: 2, self.hidden_task.id: 1},
            "the hidden task to its project members",
        )

    def test_categories_group_in_the_order_of_the_selection_the_notes_without_one_last(self):
        notes = (
            self._log_note("<p><b>Deploy</b> - prod</p>")
            | self._log_note("<p>Plain</p>")
            | self._log_note("<p><b>Progress</b> - in review</p>")
        )
        category_groups = self.Message.with_context(read_group_expand=True).formatted_read_group(
            [("id", "in", notes.ids)], ["note_category"], ["__count"]
        )
        self.assertEqual([group["note_category"] for group in category_groups], ["progress", "deploy", False])
        self.assertEqual(self.Message._fields["note_category"].string, "Category")

    def test_the_menu_is_for_project_users(self):
        menu = self.env.ref("%s.mail_message_menu_task_note" % MODULE)
        self.assertEqual((menu.action, menu.group_ids), (self.action, self.env.ref("project.group_project_user")))

    def test_the_search_view_filters_and_groups_by_category_and_task(self):
        arch = etree.fromstring(self.action.search_view_id.arch)
        self.assertEqual(
            {node.get("name"): literal_eval(node.get("domain")) for node in arch.xpath("//filter[@domain]")},
            {category: [("note_category", "=", category)] for category in NOTE_CATEGORIES},
        )
        self.assertEqual(
            [literal_eval(node.get("context"))["group_by"] for node in arch.xpath("//filter[@context]")],
            ["note_category", "task_id"],
        )
        self.assertEqual(arch.xpath("/search/field/@name"), ["body", "task_id", "author_id"])

    def test_the_views_show_the_task_offer_no_change_and_leave_mails_the_defaults(self):
        view_flags = {
            "mail_message_view_tree_task_note": {"create": "0", "delete": "0", "export_xlsx": "0"},
            "mail_message_view_kanban_task_note": {
                "create": "0",
                "delete": "0",
                "records_draggable": "0",
                "group_create": "0",
                "group_edit": "0",
                "group_delete": "0",
            },
            "mail_message_view_form_task_note": {"create": "0", "edit": "0", "delete": "0"},
        }
        for xmlid, flags in view_flags.items():
            arch = self._arch(xmlid)
            self.assertEqual({name: arch.get(name) for name in flags}, flags, xmlid)
            self.assertTrue(arch.xpath("//field[@name='task_id']"), xmlid)
            self.assertFalse(arch.xpath("//field[@name='record_name']"), xmlid)
        form_arch = self._arch("mail_message_view_form_task_note")
        self.assertEqual(form_arch.xpath("//field[@name='attachment_ids']/@widget"), ["many2many_binary"])
        default_views = {
            "list": "mail.view_message_tree",
            "form": "mail.mail_message_view_form",
            "search": "mail.view_message_search",
        }
        for view_type, xmlid in default_views.items():
            self.assertEqual(self.Message.get_view(view_type=view_type)["id"], self.env.ref(xmlid).id, view_type)

    def test_the_task_form_has_the_notes_button_for_project_users(self):
        arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        buttons = arch.xpath("//div[@name='button_box']/button[@name='action_open_notes']")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get("groups"), "project.group_project_user")
        self.assertEqual(buttons[0].xpath(".//field/@name"), ["note_count"])
