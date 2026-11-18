import unittest
from datetime import datetime
from unittest.mock import patch

from freezegun import freeze_time
from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import Form
from odoo.tools import mute_logger

from ..models.project_task_question import DISPLAY_NAME_PLACEHOLDER, DISPLAY_NAME_WIDTH
from .common import ProjectAiDrivenCommon

EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
SESSION = "0f8e2c1a-5b7d-4e3a-9c61-2d4b8a7f1e90"
OTHER_SESSION = "7a1c9e4b-3d2f-4b8a-a6e5-1c0d9f8b2a47"
SESSION_LIST_COLUMNS = ["date", "user_id", "session_id", "outcome"]


class TestTaskSessions(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env["project.task.session"]
        cls.other_task = cls.env["project.task"].create({"name": "Other Task", "project_id": cls.project.id})

    def _new_session(self, **vals):
        return self.Session.create({"task_id": self.task.id, "session_id": SESSION, **vals})

    def _task_rows(self, task=None):
        return self.Session.search([("task_id", "=", (task or self.task).id)])

    def test_a_row_holds_the_session_who_worked_it_when_and_the_outcome(self):
        with freeze_time(EARLIER):
            row = self.Session.with_user(self.user_dev).create(
                {"task_id": self.task.id, "session_id": SESSION, "outcome": "Built the form"}
            )
        self.assertRecordValues(
            row,
            [
                {
                    "task_id": self.task.id,
                    "project_id": self.project.id,
                    "session_id": SESSION,
                    "date": EARLIER,
                    "user_id": self.user_dev.id,
                    "outcome": "Built the form",
                },
            ],
        )
        self.assertEqual(self.task.session_ids, row)

    def test_the_same_session_on_the_same_task_updates_its_row(self):
        with freeze_time(EARLIER):
            first_row = self._new_session(outcome="Started")
        with freeze_time(LATER):
            second_row = self._new_session(outcome="Done")
        self.assertEqual(second_row, first_row)
        self.assertEqual(self._task_rows(), first_row)
        self.assertRecordValues(first_row, [{"outcome": "Done", "date": LATER}])

    def test_the_same_session_on_another_task_gets_a_row_of_its_own(self):
        task_row = self._new_session()
        other_task_row = self._new_session(task_id=self.other_task.id)
        self.assertNotEqual(other_task_row, task_row)
        self.assertEqual(self._task_rows(), task_row)
        self.assertEqual(self._task_rows(self.other_task), other_task_row)

    def test_another_session_on_the_same_task_adds_a_row_listed_newest_first(self):
        with freeze_time(EARLIER):
            first_row = self._new_session()
        with freeze_time(LATER):
            second_row = self._new_session(session_id=OTHER_SESSION)
        self.assertEqual(self._task_rows().ids, [second_row.id, first_row.id])

    def test_a_batch_naming_a_pair_twice_writes_one_row_with_the_later_values(self):
        existing_row = self._new_session(session_id=OTHER_SESSION, outcome="Started")
        with freeze_time(LATER):
            rows = self.Session.create(
                [
                    {"task_id": self.task.id, "session_id": SESSION, "outcome": "First", "date": EARLIER},
                    {"task_id": self.task.id, "session_id": OTHER_SESSION, "outcome": "Reviewed"},
                    {"task_id": self.task.id, "session_id": SESSION, "outcome": "Second", "user_id": self.user_dev.id},
                ]
            )
        new_row = rows[0]
        self.assertEqual(rows.ids, [new_row.id, existing_row.id, new_row.id], "one record per vals, in their order")
        self.assertEqual(self._task_rows(), new_row | existing_row)
        self.assertRecordValues(
            new_row | existing_row,
            [
                {"outcome": "Second", "user_id": self.user_dev.id, "date": LATER},
                {"outcome": "Reviewed", "user_id": self.env.user.id, "date": LATER},
            ],
        )

    def test_a_session_added_through_the_task_updates_its_row(self):
        with freeze_time(EARLIER):
            row = self._new_session(outcome="Started")
        with freeze_time(LATER):
            self.task.write({"session_ids": [Command.create({"session_id": SESSION, "outcome": "Done"})]})
        self.assertEqual(self._task_rows(), row)
        self.assertRecordValues(row, [{"outcome": "Done", "date": LATER}])

    def test_the_task_form_saves_a_known_session_onto_its_row(self):
        with freeze_time(EARLIER):
            row = self._new_session(outcome="Started")
        with freeze_time(LATER), Form(self.task.with_user(self.user_dev)) as task_form:
            with task_form.session_ids.new() as session_form:
                session_form.session_id = SESSION
                session_form.outcome = "Done"
        self.assertEqual(self._task_rows(), row)
        self.assertRecordValues(row, [{"outcome": "Done", "date": LATER, "user_id": self.user_dev.id}])

    def test_a_date_given_with_an_update_is_kept(self):
        with freeze_time(LATER):
            self._new_session()
            row = self._new_session(date=EARLIER, outcome="Recorded later")
        self.assertRecordValues(row, [{"date": EARLIER, "outcome": "Recorded later"}])

    def test_a_default_task_names_the_row_to_update(self):
        row = self._new_session(outcome="Started")
        updated_row = self.Session.with_context(default_task_id=self.task.id).create(
            {"session_id": SESSION, "outcome": "Done"}
        )
        self.assertEqual(updated_row, row)
        self.assertEqual(self._task_rows(), row)
        self.assertEqual(row.outcome, "Done")

    def test_a_create_reads_the_defaults_once(self):
        self._new_session(outcome="Started")
        session_class = type(self.Session)
        with patch.object(
            session_class, "default_get", autospec=True, side_effect=session_class.default_get
        ) as default_get:
            self.Session.with_context(default_task_id=self.task.id).create({"session_id": SESSION, "outcome": "Done"})
        self.assertEqual(default_get.call_count, 1)

    def test_an_update_keeps_the_user_and_the_outcome_it_does_not_give(self):
        row = self.Session.with_user(self.user_dev).create(
            {"task_id": self.task.id, "session_id": SESSION, "outcome": "Started"}
        )
        self.Session.with_user(self.user_lead).create({"task_id": self.task.id, "session_id": SESSION})
        self.assertRecordValues(row, [{"user_id": self.user_dev.id, "outcome": "Started"}])

    def test_a_write_leaves_the_date_alone(self):
        with freeze_time(EARLIER):
            row = self._new_session(outcome="Built the form")
        with freeze_time(LATER):
            row.outcome = "Built the form and its list"
        self.assertEqual(row.date, EARLIER)

    def test_a_write_onto_a_pair_that_has_a_row_is_refused(self):
        self._new_session()
        other_row = self._new_session(session_id=OTHER_SESSION)
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            other_row.session_id = SESSION
            other_row.flush_recordset()
        self.assertEqual(other_row.session_id, OTHER_SESSION)

    def test_a_copy_of_the_task_starts_without_sessions(self):
        row = self._new_session()
        task_copy = self.task.copy()
        self.assertFalse(task_copy.session_ids)
        self.assertEqual(self.task.session_ids, row)

    def test_a_row_is_named_by_its_outcome_or_its_session(self):
        def repeat_word(count):
            return " ".join(["Fixed"] * count)

        fitting_word_count = max(
            count
            for count in range(DISPLAY_NAME_WIDTH)
            if len(repeat_word(count) + DISPLAY_NAME_PLACEHOLDER) <= DISPLAY_NAME_WIDTH
        )
        rows = (
            self._new_session(outcome="Built the form")
            | self._new_session(session_id=OTHER_SESSION)
            | self._new_session(session_id="third-session", outcome=repeat_word(DISPLAY_NAME_WIDTH))
        )
        self.assertEqual(
            rows.mapped("display_name"),
            ["Built the form", OTHER_SESSION, repeat_word(fitting_word_count) + DISPLAY_NAME_PLACEHOLDER],
        )

    def test_the_sessions_page_follows_the_pull_requests_page_on_ai_driven_tasks_only(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (pull_requests_page,) = form_arch.xpath("//page[@name='pull_requests']")
        sessions_page = pull_requests_page.getnext()
        self.assertEqual(
            {name: sessions_page.get(name) for name in ("name", "groups", "invisible")},
            {"name": "sessions", "groups": "project.group_project_user", "invisible": "not is_ai_driven"},
        )
        (session_list,) = sessions_page.xpath("./field[@name='session_ids']")
        self.assertEqual(session_list.xpath("./list/field/@name"), SESSION_LIST_COLUMNS)
        self.assertNotIn("task_id", session_list.xpath("./form//field/@name"))

    def test_a_phone_gets_cards_of_the_sessions(self):
        form_arch = etree.fromstring(
            self.env["project.task"].with_user(self.user_dev).get_view(view_type="form")["arch"]
        )
        (session_list,) = form_arch.xpath("//field[@name='session_ids']")
        self.assertEqual(session_list.get("mode"), "list,kanban")
        (card_template,) = session_list.xpath("./kanban//t[@t-name='card']")
        self.assertLessEqual(set(SESSION_LIST_COLUMNS), set(card_template.xpath(".//field/@name")))
        (outcome_node,) = card_template.xpath(".//field[@name='outcome']")
        self.assertEqual(outcome_node.get("t-if"), "record.outcome.raw_value", "no empty line for a row without one")

    def test_users_without_project_access_get_no_sessions_page(self):
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        form_arch = etree.fromstring(
            self.env["project.task"].with_user(internal_user).get_view(view_type="form")["arch"]
        )
        self.assertFalse(form_arch.xpath("//page[@name='sessions'] | //field[@name='session_ids']"))


class TestTaskSessionAccess(ProjectAiDrivenCommon):
    """Global restrictions hide one task from everyone and keep everyone from
    changing another, whichever modules' task access is installed.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._restrict_task_access("Hide the hidden task", "[('name', '!=', 'Hidden Task')]")
        cls._restrict_task_access("Keep the read-only task", "[('name', '!=', 'Read-only Task')]", operation="ud")
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.project.id})
        cls.read_only_task = cls.env["project.task"].create({"name": "Read-only Task", "project_id": cls.project.id})
        Session = cls.env["project.task.session"]
        cls.hidden_row = Session.create({"task_id": cls.hidden_task.id, "session_id": SESSION})
        cls.read_only_row = Session.create({"task_id": cls.read_only_task.id, "session_id": SESSION})
        cls.visible_row = Session.create({"task_id": cls.task.id, "session_id": SESSION})
        cls.as_developer = Session.with_user(cls.user_dev)

    def _assert_unchanged(self, rows):
        self.env.invalidate_all()
        self.assertEqual(
            self.env["project.task.session"].search([("task_id", "in", rows.task_id.ids)]),
            rows,
            "no row added",
        )
        self.assertEqual(rows.mapped("outcome"), [False] * len(rows))

    def test_rows_of_a_task_the_user_cannot_read_are_not_found(self):
        domain = [("task_id", "in", (self.hidden_task | self.task).ids), ("session_id", "=", SESSION)]
        self.assertEqual(self.as_developer.search(domain), self.visible_row)
        self.assertEqual(self.as_developer.search_count(domain), 1)
        self.assertEqual(self.as_developer.sudo().search(domain), self.hidden_row | self.visible_row)

    def test_rows_of_a_task_the_user_cannot_read_are_not_read_written_or_deleted(self):
        hidden_row = self.hidden_row.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_row.read(["outcome"])
        # in place: Odoo's assertRaises rolls back what a refused write or unlink would leave behind
        with unittest.TestCase.assertRaises(self, AccessError):
            hidden_row.write({"outcome": "Changed"})
        with unittest.TestCase.assertRaises(self, AccessError):
            hidden_row.unlink()
        self._assert_unchanged(self.hidden_row)

    def test_an_outsider_learns_nothing_of_the_sessions_of_a_task_they_cannot_read(self):
        """A create for a session the hidden task lists and for one it does not
        is refused alike, before anything is written.
        """
        errors = []
        for session_id in (SESSION, OTHER_SESSION):
            with self.subTest(session_id=session_id), unittest.TestCase.assertRaises(self, AccessError) as refusal:
                self.as_developer.create({"task_id": self.hidden_task.id, "session_id": session_id, "outcome": "X"})
            errors.append(str(refusal.exception))
        self.assertEqual(errors[0], errors[1])
        self._assert_unchanged(self.hidden_row)

    def test_a_default_task_the_user_cannot_read_is_refused_too(self):
        with unittest.TestCase.assertRaises(self, AccessError):
            self.as_developer.with_context(default_task_id=self.hidden_task.id).create(
                {"session_id": SESSION, "outcome": "Changed"}
            )
        self._assert_unchanged(self.hidden_row)

    def test_a_row_without_a_session_is_refused_on_a_task_the_user_cannot_read(self):
        with unittest.TestCase.assertRaises(self, AccessError):
            self.as_developer.create({"task_id": self.hidden_task.id, "session_id": ""})
        self._assert_unchanged(self.hidden_row)

    def test_rows_cannot_be_moved_onto_a_task_the_user_cannot_read(self):
        own_row = self.as_developer.create({"task_id": self.task.id, "session_id": OTHER_SESSION})
        for row in (own_row, self.visible_row.with_user(self.user_dev)):
            with self.subTest(session_id=row.session_id), unittest.TestCase.assertRaises(self, AccessError):
                row.write({"task_id": self.hidden_task.id})
        self.env.invalidate_all()
        self.assertEqual((own_row | self.visible_row).task_id, self.task)
        self._assert_unchanged(self.hidden_row)

    def test_a_row_moved_onto_a_task_the_user_may_write_but_not_read_is_refused_before_the_constraint(self):
        self._restrict_task_access("Hide the unread task", "[('name', '!=', 'Unread Task')]", operation="r")
        unread_task = self.env["project.task"].create({"name": "Unread Task", "project_id": self.project.id})
        unread_row = self.env["project.task.session"].create({"task_id": unread_task.id, "session_id": SESSION})
        unread_task.with_user(self.user_dev).check_access("write")
        with unittest.TestCase.assertRaises(self, AccessError):
            self.visible_row.with_user(self.user_dev).write({"task_id": unread_task.id})
        self.env.invalidate_all()
        self.assertEqual(self.visible_row.task_id, self.task)
        self._assert_unchanged(unread_row)

    def test_a_reader_of_a_task_finds_and_reads_its_rows_and_changes_none(self):
        read_only_row = self.read_only_row.with_user(self.user_dev)
        self.assertEqual(self.as_developer.search([("task_id", "=", self.read_only_task.id)]), read_only_row)
        self.assertEqual(read_only_row.read(["session_id"])[0]["session_id"], SESSION)
        for session_id in (SESSION, OTHER_SESSION):
            with self.subTest(session_id=session_id), self.assertRaises(AccessError):
                self.as_developer.create({"task_id": self.read_only_task.id, "session_id": session_id, "outcome": "X"})
        with unittest.TestCase.assertRaises(self, AccessError):
            read_only_row.write({"outcome": "Changed"})
        with unittest.TestCase.assertRaises(self, AccessError):
            read_only_row.unlink()
        with unittest.TestCase.assertRaises(self, AccessError):
            self.visible_row.with_user(self.user_dev).write({"task_id": self.read_only_task.id})
        self._assert_unchanged(self.read_only_row)
        self.assertEqual(self.visible_row.task_id, self.task)

    def test_a_writer_of_a_task_creates_updates_writes_moves_and_deletes_its_rows(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        row = self.as_developer.create({"task_id": self.task.id, "session_id": OTHER_SESSION, "outcome": "Started"})
        updated_row = self.as_developer.create(
            {"task_id": self.task.id, "session_id": OTHER_SESSION, "outcome": "Done"}
        )
        self.assertEqual(updated_row, row)
        self.assertEqual(self.as_developer.search([("task_id", "=", self.task.id)]), self.visible_row | row)
        self.assertEqual(row.read(["outcome"])[0]["outcome"], "Done")
        row.write({"outcome": "Reviewed"})
        self.assertEqual(row.outcome, "Reviewed")
        row.write({"task_id": other_task.id})
        self.assertEqual(row.task_id, other_task)
        row.unlink()
        self.assertFalse(row.exists())

    def test_sudo_updates_the_row_of_a_task_the_user_cannot_read(self):
        row = self.as_developer.sudo().create(
            {"task_id": self.hidden_task.id, "session_id": SESSION, "outcome": "Done"}
        )
        self.assertEqual(row, self.hidden_row)
        self.assertEqual(self.hidden_row.outcome, "Done")

    def test_rows_need_project_user(self):
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        with self.assertRaises(AccessError):
            self.env["project.task.session"].with_user(internal_user).search_count([])
