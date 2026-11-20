from datetime import datetime

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import new_test_user, tagged

from ..models.project_task import SILENT_ASSIGNMENT_KEY, SilentAssignmentTaskIds
from .common import ProjectAiDrivenCommon

EARLIER = datetime(2026, 1, 5, 9, 0)
TEST_REFERENCE = "/report_module:TestWeeklyReport.test_opens"
EDITOR_HISTORY = '<p data-last-history-steps="%s">Steps</p>'


@tagged("post_install", "-at_install")
class TestNextOccurrenceLines(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project.allow_recurring_tasks = True
        Task = cls.env["project.task"]
        cls.recurring_task = Task.create(
            {
                "name": "Weekly check",
                "project_id": cls.project.id,
                "recurring_task": True,
                "repeat_unit": "week",
                "mon": True,
            }
        )
        cls.subtask = Task.create(
            {"name": "Weekly sub-check", "project_id": cls.project.id, "parent_id": cls.recurring_task.id}
        )
        cls.source_tasks = cls.recurring_task | cls.subtask
        cls.env["project.task.checklist.item"].with_context(mail_auto_subscribe_no_notify=True).create(
            [
                {
                    "name": "Item of %s" % task.name,
                    "task_id": task.id,
                    "state": "done",
                    "deadline": EARLIER,
                    "user_id": cls.user_dev.id,
                    "reviewer_id": cls.user_lead.id,
                }
                for task in cls.source_tasks
            ]
        )
        cls.env["project.task.testcase"].create(
            [
                {
                    "name": "Case of %s" % task.name,
                    "task_id": task.id,
                    "state": "passed",
                    "verification_kind": "manual",
                    "steps_to_execute": "<p>Open the report</p>",
                    "expected_results": "<p>It opens</p>",
                    "comments": "<p>Opened at once</p>",
                    "test_reference": TEST_REFERENCE,
                }
                for task in cls.source_tasks
            ]
        )
        cls.env["project.task.question"].create(
            [
                {"task_id": task.id, "question": "<p>Which report?</p>", "answer": "<p>The weekly one</p>"}
                for task in cls.source_tasks
            ]
        )

    def _create_next_occurrences(self):
        """Return the next task of the recurrence and its sub-task."""
        self.recurring_task.recurrence_id._create_next_task()
        next_task = self.recurring_task.recurrence_id.task_ids - self.recurring_task
        return next_task | next_task.child_ids

    def test_the_next_occurrence_and_its_sub_task_take_items_and_cases_from_zero_and_no_question_row(self):
        occurrences = self._create_next_occurrences()

        self.assertEqual(occurrences.mapped("name"), self.source_tasks.mapped("name"))
        for source_task, occurrence in zip(self.source_tasks, occurrences):
            with self.subTest(task=source_task.name):
                self.assertRecordValues(
                    occurrence.checklist_item_ids,
                    [
                        {
                            "name": "Item of %s" % source_task.name,
                            "state": "todo",
                            "deadline": False,
                            "user_id": self.user_dev.id,
                            "reviewer_id": self.user_lead.id,
                        }
                    ],
                )
                self.assertRecordValues(
                    occurrence.testcase_ids,
                    [
                        {
                            "name": "Case of %s" % source_task.name,
                            "state": "not_run",
                            "verified_by_id": False,
                            "verified_on": False,
                            "verification_kind": False,
                            "steps_to_execute": "<p>Open the report</p>",
                            "expected_results": "<p>It opens</p>",
                            "comments": False,
                            "test_reference": TEST_REFERENCE,
                        }
                    ],
                )
                self.assertFalse(occurrence.question_ids)
                self.assertRecordValues(source_task.checklist_item_ids, [{"state": "done", "deadline": EARLIER}])
                self.assertRecordValues(
                    source_task.testcase_ids,
                    [{"state": "passed", "verified_by_id": self.env.uid, "comments": "<p>Opened at once</p>"}],
                )
                self.assertEqual(len(source_task.question_ids), 1)

    def test_the_items_of_the_next_occurrence_are_posted_to_their_assignee_and_reviewer(self):
        occurrences = self._create_next_occurrences()

        item_subtype = self.env.ref("project_ai_driven.checklist_subtype")
        for occurrence in occurrences:
            item_posts = occurrence.message_ids.filtered(lambda message: message.subtype_id == item_subtype)
            self.assertEqual(len(item_posts), 1)
            self.assertEqual(item_posts.partner_ids, (self.user_dev | self.user_lead).partner_id)


@tagged("post_install", "-at_install")
class TestRecurrenceWrite(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project.allow_recurring_tasks = True
        first_task = cls.env["project.task"].create(
            {
                "name": "Every Monday",
                "project_id": cls.project.id,
                "recurring_task": True,
                "repeat_unit": "week",
                "mon": True,
            }
        )
        cls.recurrence = first_task.recurrence_id
        cls.recurrence._create_next_task()
        cls.recurrence._create_next_task()
        cls.recurring_tasks = cls.recurrence.task_ids.sorted("id")
        other_project = cls.env["project.project"].create({"name": "Other Project"})
        cls.later_task = cls.env["project.task"].create({"name": "Later task", "project_id": other_project.id})
        # Tasks of one transaction share their creation date. These are far ahead of the
        # tasks of the database, which a search on the date written from core would reach.
        cls.env.flush_all()
        dated_tasks = (cls.task, *cls.recurring_tasks, cls.later_task)
        for day, task in enumerate(dated_tasks, start=1):
            cls.env.cr.execute(
                "UPDATE project_task SET create_date = %s WHERE id = %s", (datetime(2099, 1, day), task.id)
            )
        cls.env["project.task"].invalidate_model(["create_date"])

    def _colors(self):
        return (self.task | self.recurring_tasks | self.later_task).mapped("color")

    def _count_assignment_notifications(self, user):
        """The assignment notifications user got on each task of the recurrence."""
        return [
            self.env["mail.message"].search_count(
                [
                    ("model", "=", "project.task"),
                    ("res_id", "=", task.id),
                    ("message_type", "=", "user_notification"),
                    ("partner_ids", "in", user.partner_id.ids),
                ]
            )
            for task in self.recurring_tasks
        ]

    def test_subsequent_tasks_written_with_recurrent_unticked_are_those_of_the_recurrence(self):
        self.recurring_tasks[1].write({"color": 5, "recurring_task": False, "recurrence_update": "subsequent"})

        self.assertEqual(self._colors(), [0, 0, 5, 5, 0])
        self.assertFalse(self.recurrence.exists())
        self.assertRecordValues(self.recurring_tasks, [{"recurring_task": False, "recurrence_id": False}] * 3)

    def test_all_tasks_written_with_recurrent_unticked_are_those_of_the_recurrence(self):
        self.recurring_tasks[1].write({"color": 5, "recurring_task": False, "recurrence_update": "all"})

        self.assertEqual(self._colors(), [0, 5, 5, 5, 0])
        self.assertFalse(self.recurrence.exists())
        self.assertRecordValues(self.recurring_tasks, [{"recurring_task": False, "recurrence_id": False}] * 3)

    def test_subsequent_tasks_of_a_task_without_recurrence_are_none(self):
        self.task.write({"color": 5, "recurrence_update": "subsequent"})

        self.assertEqual(self._colors(), [5, 0, 0, 0, 0])

    def test_this_task_is_written_alone(self):
        self.recurring_tasks[1].write({"color": 5, "recurrence_update": "this"})

        self.assertEqual(self._colors(), [0, 0, 5, 0, 0])
        self.assertEqual(self.recurring_tasks.recurrence_id, self.recurrence)

    def test_subsequent_tasks_of_a_recurring_and_a_non_recurring_task_are_those_of_the_recurrence(self):
        (self.task | self.recurring_tasks[1]).write({"color": 5, "recurrence_update": "subsequent"})

        self.assertEqual(self._colors(), [5, 0, 5, 5, 0])

    def test_an_archived_task_of_the_recurrence_is_not_written(self):
        """A guard: core's search leaves archived tasks out too."""
        # core's write of active=False removes the recurrence; archived in SQL, the task keeps it
        self.env.cr.execute("UPDATE project_task SET active = false WHERE id = %s", (self.recurring_tasks[2].id,))
        self.env["project.task"].invalidate_model(["active"])

        for color, recurrence_update in ((5, "subsequent"), (6, "all")):
            self.recurring_tasks[1].write({"color": color, "recurrence_update": recurrence_update})

        self.assertEqual(self._colors(), [0, 6, 6, 0, 0])

    def test_new_assignees_of_all_tasks_are_notified_on_the_task_saved_only(self):
        self.recurring_tasks.user_ids = False
        self.recurring_tasks[1].write({"user_ids": [Command.link(self.user_dev.id)], "recurrence_update": "all"})

        self.assertEqual([task.user_ids for task in self.recurring_tasks], [self.user_dev] * 3)
        self.assertEqual(self._count_assignment_notifications(self.user_dev), [0, 1, 0])
        self.assertTrue(all(self.recurring_tasks.mapped("date_assign")), "each first assignee is dated")

    def test_new_assignees_of_several_tasks_written_are_notified_on_each(self):
        """A guard: a write of several tasks notifies on each, whatever value a caller sends
        under the key that silences the other tasks of a recurrence.
        """
        written_tasks = self.recurring_tasks[:2]
        written_tasks.with_context(**{SILENT_ASSIGNMENT_KEY: written_tasks.ids}).write(
            {"user_ids": [Command.link(self.user_dev.id)]}
        )

        self.assertEqual(self._count_assignment_notifications(self.user_dev), [1, 1, 0])

    def test_a_list_under_the_silent_assignment_key_silences_no_task(self):
        task = self.recurring_tasks[0]
        Task = self.env["project.task"].with_context(**{SILENT_ASSIGNMENT_KEY: task.ids})

        Task._task_message_auto_subscribe_notify({task: self.user_dev})

        self.assertEqual(self._count_assignment_notifications(self.user_dev), [1, 0, 0])

    def test_a_write_notifies_its_own_tasks_under_the_silent_tasks_of_an_outer_write(self):
        written_tasks = self.recurring_tasks[:2]
        outer_silent_task_ids = SilentAssignmentTaskIds(written_tasks.ids)

        written_tasks.with_context(**{SILENT_ASSIGNMENT_KEY: outer_silent_task_ids}).write(
            {"user_ids": [Command.link(self.user_dev.id)]}
        )

        self.assertEqual(self._count_assignment_notifications(self.user_dev), [1, 1, 0])

    def test_a_description_saved_for_all_tasks_from_a_stale_editor_history_is_refused(self):
        saved_task = self.recurring_tasks[1]
        saved_task.description = EDITOR_HISTORY % "11,12"

        for recurrence_update in ("this", "all"):
            with (
                self.subTest(recurrence_update=recurrence_update),
                self.assertLogs("odoo.addons.web_editor.controllers.main", "WARNING"),
                self.assertRaises(ValidationError),
            ):
                saved_task.write({"description": EDITOR_HISTORY % "13,14", "recurrence_update": recurrence_update})
        self.assertIn('data-last-history-steps="12"', saved_task.description)

        saved_task.write({"description": EDITOR_HISTORY % "12,15", "recurrence_update": "all"})
        for task in self.recurring_tasks:
            self.assertIn('data-last-history-steps="15"', task.description, "only the last step is saved")

    def test_a_portal_user_is_refused_a_recurrence_update(self):
        portal_user = new_test_user(self.env, "project_ai_driven_portal", groups="base.group_portal")
        self.project.write(
            {
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": portal_user.partner_id.id})],
            }
        )
        self.project.message_subscribe(partner_ids=portal_user.partner_id.ids)
        self.recurring_tasks.with_user(portal_user).check_access_rule("write")

        for recurrence_update in ("this", "subsequent", "all"):
            with self.subTest(recurrence_update=recurrence_update), self.assertRaises(AccessError):
                self.recurring_tasks[1].with_user(portal_user).write(
                    {"name": "Renamed", "recurrence_update": recurrence_update}
                )
        self.assertEqual(self.recurring_tasks.mapped("name"), ["Every Monday"] * 3)
        self.recurring_tasks[1].with_user(portal_user).write({"name": "Renamed"})
        self.assertEqual(self.recurring_tasks.mapped("name"), ["Every Monday", "Renamed", "Every Monday"])
