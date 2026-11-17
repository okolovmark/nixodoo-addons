import json
from datetime import datetime

from freezegun import freeze_time
from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import Form, tagged
from odoo.tools import mute_logger

from ..models.project_task_question import DISPLAY_NAME_PLACEHOLDER, DISPLAY_NAME_WIDTH
from .common import ProjectAiDrivenCommon

EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
SESSION = "0f8e2c1a-5b7d-4e3a-9c61-2d4b8a7f1e90"
OTHER_SESSION = "7a1c9e4b-3d2f-4b8a-a6e5-1c0d9f8b2a47"
SESSION_LIST_COLUMNS = ["date", "user_id", "session_id", "outcome"]


@tagged("post_install", "-at_install")
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
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"), self.env.cr.savepoint():
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

    def test_the_sessions_page_follows_the_pull_requests_page(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (pull_requests_page,) = form_arch.xpath("//page[@name='pull_requests']")
        sessions_page = pull_requests_page.getnext()
        self.assertEqual(
            (sessions_page.get("name"), sessions_page.get("groups")), ("sessions", "project.group_project_user")
        )
        (session_list,) = sessions_page.xpath("./field[@name='session_ids']")
        self.assertEqual(session_list.get("mode"), "tree,kanban", "a phone gets cards")
        self.assertEqual(session_list.xpath("./tree/field/@name"), SESSION_LIST_COLUMNS)
        self.assertNotIn("task_id", session_list.xpath("./form//field/@name"))

    def test_the_sessions_page_shows_on_tasks_of_ai_driven_projects_only(self):
        plain_project = self.env["project.project"].create({"name": "Plain Project"})
        plain_task = self.env["project.task"].create({"name": "Plain Task", "project_id": plain_project.id})
        form_view = self.env.ref("project.view_task_form2")
        views = self.env["project.task"].with_user(self.user_dev).get_views([(form_view.id, "form")])
        (sessions_page,) = etree.fromstring(views["views"]["form"]["arch"]).xpath("//page[@name='sessions']")
        hiding_domain = json.loads(sessions_page.get("modifiers"))["invisible"]
        tasks = self.task | plain_task
        self.assertEqual(self.env["project.task"].search([("id", "in", tasks.ids)] + hiding_domain), plain_task)


@tagged("post_install", "-at_install")
class TestTaskSessionAccess(ProjectAiDrivenCommon):
    """A global rule hides one task from everyone, whichever modules' task rules are installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.rule"].create(
            {
                "name": "Hide the hidden task",
                "model_id": cls.env["ir.model"]._get_id("project.task"),
                "domain_force": "[('name', '!=', 'Hidden Task')]",
            },
        )
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.project.id})
        Session = cls.env["project.task.session"]
        cls.hidden_row = Session.create({"task_id": cls.hidden_task.id, "session_id": SESSION})
        cls.visible_row = Session.create({"task_id": cls.task.id, "session_id": SESSION})
        cls.as_developer = Session.with_user(cls.user_dev)

    def test_rows_of_a_task_the_user_cannot_read_are_not_found(self):
        domain = [("task_id", "in", (self.hidden_task | self.task).ids), ("session_id", "=", SESSION)]
        self.assertEqual(self.as_developer.search(domain), self.visible_row)
        self.assertEqual(self.as_developer.search_count(domain), len(self.visible_row))
        self.assertEqual(self.as_developer.sudo().search(domain), self.hidden_row | self.visible_row)

    def test_rows_of_a_task_the_user_cannot_read_are_not_read_written_or_deleted(self):
        hidden_row = self.hidden_row.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_row.read(["outcome"])
        with self.assertRaises(AccessError):
            hidden_row.write({"outcome": "Changed"})
        with self.assertRaises(AccessError):
            hidden_row.unlink()
        self.assertRecordValues(self.hidden_row, [{"outcome": False}])

    def _assert_hidden_task_keeps_its_row(self):
        self.assertEqual(
            self.env["project.task.session"].search([("task_id", "=", self.hidden_task.id)]), self.hidden_row
        )
        self.assertRecordValues(self.hidden_row, [{"outcome": False}])

    def test_rows_cannot_be_added_to_a_task_the_user_cannot_read_whether_it_lists_the_session_or_not(self):
        for session_id in (OTHER_SESSION, SESSION):
            with self.subTest(session_id=session_id), self.assertRaises(AccessError), self.env.cr.savepoint():
                self.as_developer.create(
                    {"task_id": self.hidden_task.id, "session_id": session_id, "outcome": "Changed"}
                )
        self._assert_hidden_task_keeps_its_row()

    def test_a_default_task_the_user_cannot_read_is_refused_too(self):
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.as_developer.with_context(default_task_id=self.hidden_task.id).create(
                {"session_id": SESSION, "outcome": "Changed"}
            )
        self._assert_hidden_task_keeps_its_row()

    def test_rows_cannot_be_moved_onto_a_task_the_user_cannot_read(self):
        own_row = self.as_developer.create({"task_id": self.task.id, "session_id": OTHER_SESSION})
        for row in (own_row, self.visible_row.with_user(self.user_dev)):
            with self.subTest(session_id=row.session_id), self.assertRaises(AccessError), self.env.cr.savepoint():
                row.write({"task_id": self.hidden_task.id})
        self.assertRecordValues(own_row | self.visible_row, [{"task_id": self.task.id}, {"task_id": self.task.id}])
        self._assert_hidden_task_keeps_its_row()

    def test_a_row_moves_onto_another_task_the_user_reads(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        own_row = self.as_developer.create({"task_id": self.task.id, "session_id": OTHER_SESSION})
        own_row.write({"task_id": other_task.id})
        self.assertEqual(own_row.task_id, other_task)

    def test_a_row_without_a_session_is_refused_on_a_task_the_user_cannot_read(self):
        self.env["project.task.session"].create({"task_id": self.hidden_task.id, "session_id": ""})
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.as_developer.create({"task_id": self.hidden_task.id, "session_id": ""})

    def test_sudo_updates_the_row_of_a_task_the_user_cannot_read(self):
        row = self.as_developer.sudo().create(
            {"task_id": self.hidden_task.id, "session_id": SESSION, "outcome": "Done"}
        )
        self.assertEqual(row, self.hidden_row)
        self.assertEqual(self.hidden_row.outcome, "Done")

    def test_a_project_user_who_reads_the_task_finds_reads_writes_and_deletes_its_rows(self):
        row = self.as_developer.create({"task_id": self.task.id, "session_id": OTHER_SESSION, "outcome": "Started"})
        updated_row = self.as_developer.create(
            {"task_id": self.task.id, "session_id": OTHER_SESSION, "outcome": "Done"}
        )
        self.assertEqual(updated_row, row)
        self.assertEqual(self.as_developer.search([("task_id", "=", self.task.id)]), self.visible_row | row)
        self.assertEqual(row.read(["outcome"])[0]["outcome"], "Done")
        row.write({"outcome": "Reviewed"})
        self.assertEqual(row.outcome, "Reviewed")
        row.unlink()
        self.assertFalse(row.exists())

    def test_rows_need_project_user(self):
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        with self.assertRaises(AccessError):
            self.env["project.task.session"].with_user(internal_user).search_count([])
