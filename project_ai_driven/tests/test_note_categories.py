import importlib.util
import json
import logging
from ast import literal_eval
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from lxml import etree
from markupsafe import Markup

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression
from odoo.tests.common import tagged

from ..hooks import FORMER_MODULE, LEGACY_MODULE, MODULE, fill_task_messages, pre_init_hook
from ..models.mail_message import NOTE_CATEGORIES, get_note_category
from .common import ProjectAiDrivenCommon

MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.8.0" / "pre-migration.py"
HOOKS_LOGGER = "odoo.addons.%s.hooks" % MODULE
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
    # the sanitizer wraps a body opening without a block so
    "<span><b>Progress</b> - posted without a paragraph</span>",
    "<b>Review Fix</b> - posted without a paragraph",
    # letters Python folds onto ASCII ones when it ignores case, and PostgreSQL does not
    "<p><b>REV\u0130SED</b> plan</p>",
    "<p><b>Bug F\u0131x</b>: the total</p>",
    "<p><b>Progre\u017fs</b> - in review</p>",
)
# Bodies only SQL writes as they are: the sanitizer of the field would rewrite them.
RAW_CATEGORY_OPENINGS = {
    "  \n<p>\n<b>Progress</b></p>": "progress",
    '<P class="x"><STRONG style="color: red">Design\n Decision</STRONG></P>': "design_decision",
    "<p>\u00a0<b>Bug\u00a0Fix\u00a0\u2014\u00a0total</b></p>": "bug_fix",
    "<p><b>\tDeploy:</b></p>": "deploy",
    "<b>Review Fix</b> without a paragraph": "review_fix",
}
RAW_NO_CATEGORY_OPENINGS = (
    "<p><p><b>Progress</b></p></p>",
    "<div><b>Progress</b></div>",
    "<pre><b>Progress</b></pre>",
    "<p><bdi>Progress</bdi></p>",
)
TRACKING_BODY = "<p><b>Progress</b> - the deadline moved</p>"


def _load_script(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


class NoteCategoryCase(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Message = cls.env["mail.message"]

    def _log_note(self, body, record=None, message_type="comment", subtype_xmlid="mail.mt_note"):
        return (record or self.task).message_post(
            body=Markup(body), message_type=message_type, subtype_xmlid=subtype_xmlid
        )

    def _log_tracking(self, body):
        """The message of a change of a tracked field of the task, logged with body."""
        self._flush_tracking()
        self.task._track_set_log_message(Markup(body))
        self.task.date_deadline = (self.task.date_deadline or date(2026, 12, 1)) + timedelta(days=1)
        self._flush_tracking()
        return self.task.message_ids.filtered("tracking_value_ids")[0]

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


@tagged("post_install", "-at_install")
class TestNoteCategory(NoteCategoryCase):
    def test_a_note_opening_with_a_category_in_bold_has_it(self):
        for message_type in ("comment", "notification"):
            notes = self.Message.concat(
                *(self._log_note(body, message_type=message_type) for body in CATEGORY_OPENINGS)
            )
            self._assert_categories(notes, CATEGORY_OPENINGS.values(), message_type)

    def test_a_note_opening_otherwise_has_none(self):
        notes = self.Message.concat(*(self._log_note(body) for body in NO_CATEGORY_OPENINGS))
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
        self.assertEqual((tracking.subtype_id, str(tracking.body)), (self.env.ref("mail.mt_note"), TRACKING_BODY))
        self.assertEqual(self._categories(tracking), [False])

    def test_an_edited_note_is_categorized_again(self):
        note = self._log_note("<p><b>Progress</b> - in review</p>")
        self.task._message_update_content(note, Markup("<p><b>Deploy</b> - prod</p>"))
        self.assertEqual(note.note_category, "deploy")
        self.task._message_update_content(note, Markup("<p>Nothing to report</p>"))
        self.assertFalse(note.note_category)

    def test_a_message_moved_or_resubtyped_gets_its_task_and_category_again(self):
        note = self._log_note("<p><b>Progress</b> - moved</p>", record=self.project)
        self.assertEqual(self._stored_values(note), [(False, False)])
        note.write({"model": "project.task", "res_id": self.task.id})
        self.assertEqual(self._stored_values(note), [(self.task.id, "progress")])
        note.subtype_id = self.env.ref("mail.mt_comment")
        self.assertEqual(self._stored_values(note), [(self.task.id, False)])
        note.subtype_id = self.env.ref("mail.mt_note")
        self.assertEqual(self._stored_values(note), [(self.task.id, "progress")])

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

    def test_a_message_naming_a_missing_task_has_no_task(self):
        missing_task_id = self.env["project.task"].with_context(active_test=False).search([], limit=1).id + 10**9
        message = self.Message.create(
            {
                "model": "project.task",
                "res_id": missing_task_id,
                "record_name": "Deleted Task",
                "reply_to": "reply@example.com",
                "body": Markup("<p><b>Deploy</b></p>"),
                "subtype_id": self.env.ref("mail.mt_note").id,
            }
        )
        self.assertEqual(self._stored_values(message), [(False, "deploy")])

    def test_the_rule_reads_raw_bodies(self):
        bodies = {**RAW_CATEGORY_OPENINGS, **dict.fromkeys(RAW_NO_CATEGORY_OPENINGS, False)}
        self.assertEqual({body: get_note_category(body) for body in bodies}, bodies)


@tagged("post_install", "-at_install")
class TestTaskMessageFill(NoteCategoryCase):
    """The fill runs over the whole database; the messages of a test are the
    only ones its run can change, since the update filled the others.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.other_task = cls.env["project.task"].create({"name": "Other Task", "project_id": cls.project.id})

    def _create_messages(self):
        """A task note of every opening, posted, or written by SQL for the raw
        ones, and category openings where no category counts. Returns the
        messages and the (task id, category) each one has.
        """
        posted_bodies = {**CATEGORY_OPENINGS, **dict.fromkeys(NO_CATEGORY_OPENINGS, False)}
        raw_bodies = {**RAW_CATEGORY_OPENINGS, **dict.fromkeys(RAW_NO_CATEGORY_OPENINGS, False)}
        expected_values = {}
        raw_messages = {}
        for message_type in ("comment", "notification"):
            for body, category in posted_bodies.items():
                expected_values[self._log_note(body, message_type=message_type)] = (self.task.id, category)
            for body, category in raw_bodies.items():
                message = self._log_note("<p>to be replaced</p>", message_type=message_type)
                raw_messages[message] = body
                expected_values[message] = (self.task.id, category)
        for body in CATEGORY_OPENINGS:
            expected_values[self._log_note(body, subtype_xmlid="mail.mt_comment")] = (self.task.id, False)
            expected_values[self._log_note(body, record=self.project)] = (False, False)
        expected_values[self._log_tracking(TRACKING_BODY)] = (self.task.id, False)
        self.env.flush_all()
        for message, body in raw_messages.items():
            self.env.cr.execute("UPDATE mail_message SET body = %s WHERE id = %s", (body, message.id))
        self.env.invalidate_all()
        return self.Message.concat(*expected_values), list(expected_values.values())

    def _set_columns(self, messages, task_id, category):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE mail_message SET task_id = %s, note_category = %s WHERE id IN %s",
            (task_id, category, tuple(messages.ids)),
        )
        self.env.invalidate_all()

    def _count_filled(self):
        self.env.cr.execute(
            "SELECT (SELECT count(*) FROM mail_message WHERE task_id IS NOT NULL),"
            " (SELECT count(*) FROM mail_message WHERE note_category IS NOT NULL)"
        )
        return self.env.cr.fetchone()

    def _category_counts(self, categories):
        return {**dict.fromkeys(NOTE_CATEGORIES, 0), False: 0, **Counter(categories)}

    def test_the_sql_fill_gives_what_the_computes_give(self):
        messages, expected_values = self._create_messages()
        self.assertEqual(self._computed_values(messages), expected_values, "the computes")
        self._set_columns(messages, None, None)

        task_counts, category_counts = fill_task_messages(self.env.cr)

        self.assertEqual(self._stored_values(messages), expected_values)
        self.assertEqual(
            task_counts, {"given": sum(1 for task_id, _category in expected_values if task_id), "cleared": 0}
        )
        self.assertEqual(
            category_counts, self._category_counts(category for _task_id, category in expected_values if category)
        )

    def test_the_fill_clears_what_the_computes_do_not_give(self):
        messages, expected_values = self._create_messages()
        self._set_columns(messages, self.other_task.id, "deploy")

        task_counts, category_counts = fill_task_messages(self.env.cr)

        self.assertEqual(self._stored_values(messages), expected_values)
        self.assertEqual(
            task_counts,
            {
                "given": sum(1 for task_id, _category in expected_values if task_id),
                "cleared": sum(1 for task_id, _category in expected_values if not task_id),
            },
        )
        self.assertEqual(
            category_counts,
            self._category_counts(category for _task_id, category in expected_values if category != "deploy"),
        )

    def test_a_second_fill_changes_nothing_and_logs_one_line(self):
        messages, expected_values = self._create_messages()
        self._set_columns(messages, None, None)
        fill_task_messages(self.env.cr)
        filled_counts = self._count_filled()

        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            task_counts, category_counts = fill_task_messages(self.env.cr)

        self.assertEqual((task_counts, category_counts), ({"given": 0, "cleared": 0}, self._category_counts([])))
        self.assertEqual(self._count_filled(), filled_counts)
        self.assertEqual(self._stored_values(messages), expected_values)
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [
                "Task messages given their task: 0, their task cleared: 0; task notes given a category: "
                + ", ".join("0 %s" % label for label in NOTE_CATEGORIES.values())
                + ", their category cleared: 0"
            ],
        )

    def test_the_fill_makes_the_columns_and_indexes_odoo_finds_so_an_update_computes_nothing(self):
        messages, expected_values = self._create_messages()
        self.env.cr.execute("ALTER TABLE mail_message DROP COLUMN task_id, DROP COLUMN note_category")

        fill_task_messages(self.env.cr)

        self.assertEqual(self._stored_values(messages), expected_values)
        self.env.cr.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'mail_message' AND indexname IN %s",
            (("mail_message_task_id_index", "mail_message_note_category_index"),),
        )
        self.assertEqual(
            dict(self.env.cr.fetchall()),
            {
                "mail_message_%s_index"
                % column: "CREATE INDEX mail_message_%s_index ON public.mail_message USING btree"
                " (%s) WHERE (%s IS NOT NULL)" % (column, column, column)
                for column in ("task_id", "note_category")
            },
        )
        self.env.cr.execute(
            "SELECT attname FROM pg_stats WHERE tablename = 'mail_message' AND attname IN ('task_id', 'note_category')"
        )
        self.assertEqual({attname for (attname,) in self.env.cr.fetchall()}, {"task_id", "note_category"}, "analyzed")
        # the foreign key of task_id is queued for the end of an update, which a test never reaches
        with patch.object(self.registry, "post_init"):
            self.Message._auto_init()
        for field_name in ("task_id", "note_category"):
            self.assertFalse(self.env.records_to_compute(self.Message._fields[field_name]), field_name)

    def test_migration_script_fills_on_update_only(self):
        script = _load_script(MIGRATION_SCRIPT)
        messages, expected_values = self._create_messages()
        self._set_columns(messages, None, None)

        script.migrate(self.env.cr, None)
        self.assertEqual(self._stored_values(messages), [(False, False)] * len(messages))

        script.migrate(self.env.cr, "16.0.2.7.0")
        self.assertEqual(self._stored_values(messages), expected_values)

    def test_pre_init_hook_fills_without_a_module_to_take_over_from(self):
        self.env.cr.execute(
            "UPDATE ir_module_module SET state = 'uninstalled' WHERE name IN %s", ((FORMER_MODULE, LEGACY_MODULE),)
        )
        messages, expected_values = self._create_messages()
        self._set_columns(messages, None, None)
        pre_init_hook(self.env.cr)
        self.assertEqual(self._stored_values(messages), expected_values)


@tagged("post_install", "-at_install")
class TestTaskNotes(NoteCategoryCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.action = cls.env.ref(ACTION)
        cls.hidden_project = cls.env["project.project"].create(
            {"name": "Followers Only", "privacy_visibility": "followers", "user_id": cls.user_lead.id},
        )
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.hidden_project.id})
        cls.employee = cls._create_user("Employee", "project_ai_driven_employee", "employee@example.com")

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
        self._flush_tracking()
        self.task.date_deadline = "2026-12-01"
        self._flush_tracking()
        self._log_note("<p><b>Progress</b> - on the project</p>", record=self.project)
        return notes

    def _arch(self, xmlid):
        return etree.fromstring(self.env.ref("%s.%s" % (MODULE, xmlid)).arch)

    def test_the_action_lists_the_notes_of_tasks_their_chatter_shows(self):
        notes = self._log_task_messages()
        self.assertFalse(notes[-1].body, "a note of files alone")
        self.assertEqual(literal_eval(self.action.domain), self.Message._get_task_note_domain())
        self.assertEqual(self._action_notes(), notes)

    def test_the_notes_button_counts_the_notes_of_its_task_and_lists_them(self):
        notes = self._log_task_messages()
        self._log_note("<p><b>Deploy</b></p>", record=self.hidden_task)
        self.assertEqual((self.task | self.hidden_task).mapped("note_count"), [3, 1])

        notes_action = self.task.action_open_notes()

        self.assertEqual(notes_action["res_model"], "mail.message")
        self.assertEqual(self.Message.search(notes_action["domain"]), notes)

    def test_the_notes_button_counts_a_note_posted_since_it_was_read(self):
        self.assertEqual(self.task.note_count, 0)
        self._log_note("<p>Posted after the form was read</p>")
        self.assertEqual(self.task.note_count, 1)

    def test_a_user_who_cannot_read_a_task_does_not_find_its_notes(self):
        visible_note = self._log_note("<p><b>Progress</b> - visible</p>")
        hidden_note = self._log_note("<p><b>Progress</b> - hidden</p>", record=self.hidden_task)
        with self.assertRaises(AccessError):
            self.hidden_task.with_user(self.user_dev).check_access_rule("read")
        self.assertEqual(self._action_notes(self.user_dev), visible_note)
        self.assertEqual(
            (visible_note | hidden_note).with_user(self.user_dev).mapped("is_task_readable"), [True, False]
        )

    def test_a_page_of_the_list_holds_the_notes_the_user_can_read(self):
        visible_note = self._log_note("<p><b>Progress</b> - visible</p>")
        self._log_note("<p><b>Progress</b> - hidden, and newer</p>", record=self.hidden_task)
        first_page = self.Message.with_user(self.user_dev).search(literal_eval(self.action.domain), limit=1)
        self.assertEqual(first_page, visible_note)

    def test_the_task_of_a_message_is_found_by_its_current_name(self):
        task_note = self._log_note("<p>On the task</p>")
        project_note = self._log_note("<p>On the project</p>", record=self.project)
        messages = task_note | project_note
        former_name = self.task.display_name
        self.task.name = "Renamed Task"
        self.assertEqual(task_note.record_name, former_name)
        self.assertEqual((task_note.task_id, project_note.task_id), (self.task, self.env["project.task"]))
        self.assertEqual(self.Message.search([("task_id", "in", self.task.ids)]), self.task.message_ids)
        self.assertEqual(self.Message.search([("task_id", "ilike", "Renamed Task")]), self.task.message_ids)
        self.assertFalse(self.Message.search([("task_id", "ilike", former_name), ("id", "in", messages.ids)]))
        self.assertEqual(self.Message.search([("task_id", "=", False), ("id", "in", messages.ids)]), project_note)
        self.assertEqual(self.Message.search([("task_id", "!=", False), ("id", "in", messages.ids)]), task_note)
        with self.assertRaises(UserError):
            self.Message.search([("is_task_readable", "in", [True])])

    def test_a_project_user_groups_the_notes_of_a_task_under_its_current_name(self):
        notes = self._log_note("<p><b>Progress</b> - visible</p>") | self._log_note("<p><b>Deploy</b> - visible</p>")
        self._log_note("<p><b>Progress</b> - hidden</p>", record=self.hidden_task)
        self.task.name = "Renamed Task"
        task_groups = self.Message.with_user(self.user_dev).read_group(self._action_domain(), ["task_id"], ["task_id"])
        self.assertNotEqual(set(notes.mapped("record_name")), {self.task.display_name})
        self.assertEqual(
            [(group["task_id"], group["task_id_count"]) for group in task_groups],
            [((self.task.id, self.task.display_name), 2)],
        )

    def test_the_domain_of_a_group_is_the_callers_with_the_group_condition(self):
        progress_note = self._log_note("<p><b>Progress</b> - visible</p>")
        deploy_note = self._log_note("<p><b>Deploy</b> - visible</p>")
        self._log_note("<p><b>Progress</b> - hidden</p>", record=self.hidden_task)
        domain = self._action_domain()
        user_messages = self.Message.with_user(self.user_dev)

        category_groups = user_messages.read_group(domain, ["note_category"], ["note_category"])

        self.assertEqual(
            [(group["note_category"], group["note_category_count"]) for group in category_groups],
            [("progress", 1), ("deploy", 1)],
        )
        for group, notes in zip(category_groups, (progress_note, deploy_note)):
            group_condition = [("note_category", "=", group["note_category"])]
            self.assertEqual(group["__domain"], expression.AND([group_condition, domain]))
            self.assertEqual(user_messages.search(json.loads(json.dumps(group["__domain"]))), notes)

    def test_grouping_other_messages_stays_refused_to_a_user_who_is_no_administrator(self):
        self._log_note("<p><b>Progress</b> - visible</p>")
        task_messages = [("model", "=", "project.task"), ("res_id", "=", self.task.id)]
        with self.assertRaises(AccessError):
            self.Message.with_user(self.user_dev).read_group(task_messages, ["note_category"], ["note_category"])
        with self.assertRaises(AccessError):
            self.Message.with_user(self.employee).read_group(
                self._action_domain(), ["note_category"], ["note_category"]
            )

    def test_a_grouped_read_counts_the_messages_of_readable_tasks_alone_whatever_the_domain(self):
        visible_note = self._log_note("<p><b>Progress</b> - visible</p>")
        messages = (
            visible_note
            | self._log_note("<p><b>Progress</b> - hidden</p>", record=self.hidden_task)
            | self._log_note("<p><b>Progress</b> - on the project</p>", record=self.project)
            | self.task.message_notify(partner_ids=self.user_lead.partner_id.ids, body=Markup("<p>To the lead</p>"))
        )
        domain_counted_messages = (
            (["|", ("is_task_readable", "=", True), ("id", "!=", 0)], visible_note),
            ([("is_task_readable", "=", False)], self.Message),
        )
        for domain, counted_messages in domain_counted_messages:
            with self.subTest(domain=domain):
                model_groups = self.Message.with_user(self.user_dev).read_group(
                    expression.AND([[("id", "in", messages.ids)], domain]), ["message_ids:array_agg(id)"], ["model"]
                )
                counted_ids = {message_id for group in model_groups for message_id in group["message_ids"]}
                self.assertEqual(counted_ids, set(counted_messages.ids))

    def test_a_project_user_groups_by_the_fields_of_the_notes_list_alone(self):
        notes = self._log_note("<p><b>Progress</b> - visible</p>") | self._log_note("<p>Plain</p>")
        user_messages = self.Message.with_user(self.user_dev)
        for groupby in ("note_category", "task_id", "author_id", "date:month", "model", "message_type", "subtype_id"):
            groups = user_messages.read_group(self._action_domain(), ["__count"], [groupby], lazy=False)
            self.assertEqual(sum(group["__count"] for group in groups), len(notes), groupby)
        for groupby in ("partner_ids", "notified_partner_ids", "starred_partner_ids", ["task_id", "partner_ids"]):
            with self.assertRaises(AccessError, msg=groupby):
                user_messages.read_group(self._action_domain(), ["__count"], groupby)

    def test_whether_a_task_is_readable_follows_the_allowed_companies(self):
        main_company = self.user_dev.company_id
        other_company = self.env["res.company"].create({"name": "Other Company"})
        self.user_dev.company_ids = [Command.link(other_company.id)]
        other_project = self.env["project.project"].create({"name": "Other Project", "company_id": other_company.id})
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": other_project.id})
        user_note = self._log_note("<p><b>Progress</b></p>", record=other_task).with_user(self.user_dev)
        company_contexts = (main_company.ids, (main_company | other_company).ids, main_company.ids)
        self.assertEqual(
            [
                user_note.with_context(allowed_company_ids=company_ids).is_task_readable
                for company_ids in company_contexts
            ],
            [False, True, False],
        )

    def test_whether_a_task_is_readable_is_searched_as_1_and_0_too(self):
        visible_note = self._log_note("<p><b>Progress</b> - visible</p>")
        project_note = self._log_note("<p><b>Progress</b> - on the project</p>", record=self.project)
        messages = visible_note | project_note | self._log_note("<p>Hidden</p>", record=self.hidden_task)
        user_messages = self.Message.with_user(self.user_dev)
        expected_messages = {
            ("=", 1): visible_note,
            ("!=", 0): visible_note,
            ("=", 0): project_note,
            ("!=", 1): project_note,
        }
        self.assertEqual(
            {
                condition: user_messages.search([("is_task_readable", *condition), ("id", "in", messages.ids)])
                for condition in expected_messages
            },
            expected_messages,
        )
        with self.assertRaises(UserError):
            user_messages.search([("is_task_readable", "=", 2)])

    def test_categories_group_in_the_order_of_the_selection_the_notes_without_one_last(self):
        notes = (
            self._log_note("<p><b>Deploy</b> - prod</p>")
            | self._log_note("<p>Plain</p>")
            | self._log_note("<p><b>Progress</b> - in review</p>")
        )
        category_groups = self.Message.read_group([("id", "in", notes.ids)], ["note_category"], ["note_category"])
        self.assertEqual([group["note_category"] for group in category_groups], ["progress", "deploy", False])
        self.assertEqual(self.Message._fields["note_category"].string, "Category")

    def test_the_menu_is_for_project_users(self):
        menu = self.env.ref("%s.mail_message_menu_task_note" % MODULE)
        self.assertEqual((menu.action, menu.groups_id), (self.action, self.env.ref("project.group_project_user")))

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

    def test_the_views_show_the_task_and_offer_no_change(self):
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

    def test_the_task_form_has_the_notes_button_for_project_users(self):
        arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        buttons = arch.xpath("//div[@name='button_box']/button[@name='action_open_notes']")
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get("groups"), "project.group_project_user")
        self.assertEqual(buttons[0].xpath(".//field/@name"), ["note_count"])
