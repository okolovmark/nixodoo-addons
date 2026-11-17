import re
import unittest
from ast import literal_eval
from datetime import date, datetime

from lxml import html
from markupsafe import escape

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from ..hooks import CHECKLIST_MODEL, MODULE
from .common import ProjectAiDrivenCommon
from .test_checklist import MARKUP_NAME, REFUSAL
from .test_estimate_rule import NO_ESTIMATE, NO_TYPE

CONVERT_INVISIBLE = {"invisible": ["|", ("hide_button", "=", True), ("allow_subtasks", "=", False)]}


@tagged("post_install", "-at_install")
class TestChecklistItemToSubtask(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project.allow_subtasks = True
        cls.stage_backlog = cls._create_stage("Backlog", 1)
        cls.stage_progress = cls._create_stage("In Progress", 2)
        cls.task.write({"stage_id": cls.stage_progress.id, "date_deadline": date(2026, 12, 24)})
        cls.reviewer = cls._create_project_user("Reviewer", "project_ai_driven_reviewer", "reviewer@example.com")
        cls.Item = cls.env[CHECKLIST_MODEL].with_context(mail_auto_subscribe_no_notify=True)

    def _new_item(self, **vals):
        return self.Item.create(
            {
                "name": "Write the migration",
                "task_id": self.task.id,
                "user_id": self.user_dev.id,
                "reviewer_id": self.reviewer.id,
                **vals,
            },
        )

    def _subtasks(self, task):
        return self.env["project.task"].search([("parent_id", "=", task.id)])

    def _conversion_notes(self, task):
        return self.env["mail.message"].search(
            [
                ("model", "=", "project.task"),
                ("res_id", "=", task.id),
                ("message_type", "=", "comment"),
                ("subtype_id", "=", self.env.ref("mail.mt_note").id),
            ],
        )

    def assertLeftInPlace(self, item, task):
        self.assertTrue(item.exists())
        self.assertFalse(self._subtasks(task))
        self.assertFalse(self._conversion_notes(task))

    def assertNoteLinks(self, task, item_name, subtask, author):
        note = self._conversion_notes(task)
        self.assertEqual(len(note), 1)
        self.assertEqual(note.author_id, author.partner_id)
        body = html.fromstring(note.body)
        self.assertIn(item_name, body.text_content())
        self.assertEqual(body.xpath("//a[@data-oe-model='project.task']/@data-oe-id"), [str(subtask.id)])
        self.assertFalse(body.xpath("//b | //strong"), "no category opening the note")
        self.assertFalse(note.note_category)

    def test_the_assignee_converts_an_item_into_a_subtask_in_the_first_stage(self):
        self.user_dev.tz = "Asia/Manila"
        item = self._new_item(deadline=datetime(2026, 12, 1, 20, 0))
        self.assertNotIn(self.reviewer.partner_id, self.task.message_partner_ids)
        self.assertIsNone(item.with_user(self.user_dev).action_convert_to_subtask())
        self.assertFalse(item.exists())
        subtask = self._subtasks(self.task)
        self.assertRecordValues(
            subtask,
            [
                {
                    "name": "Write the migration",
                    "project_id": self.project.id,
                    "user_ids": self.user_dev.ids,
                    "date_deadline": date(2026, 12, 2),
                    "stage_id": self.stage_backlog.id,
                },
            ],
        )
        self.assertIn(self.reviewer.partner_id, subtask.message_partner_ids)
        self.assertNoteLinks(self.task, "Write the migration", subtask, self.user_dev)

    def test_the_reviewer_converts_an_item_assigned_to_another(self):
        item = self._new_item()
        item.with_user(self.reviewer).action_convert_to_subtask()
        self.assertFalse(item.exists())
        subtask = self._subtasks(self.task)
        self.assertRecordValues(subtask, [{"name": "Write the migration", "user_ids": self.user_dev.ids}])
        self.assertIn(self.user_dev.partner_id, subtask.message_partner_ids)
        self.assertNoteLinks(self.task, "Write the migration", subtask, self.reviewer)

    def test_another_project_user_is_refused_before_anything_is_created(self):
        item = self._new_item()
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        # in place: Odoo's assertRaises rolls back what a late refusal would leave behind
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(REFUSAL)):
            item.with_user(outsider).action_convert_to_subtask()
        self.assertLeftInPlace(item, self.task)

    def test_an_item_without_deadline_gives_a_subtask_without_deadline(self):
        item = self._new_item()
        item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertFalse(self._subtasks(self.task).date_deadline, "not the deadline of the task")

    def test_a_subtask_refused_by_the_first_stage_of_the_project_leaves_the_item(self):
        self.project.estimate_required_states = self.stage_backlog
        item = self._new_item()
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertLeftInPlace(item, self.task)

    def test_a_user_who_may_read_but_not_change_the_items_is_refused_before_anything_is_created(self):
        self.env["ir.rule"].create(
            {
                "name": "Items of a read-only task",
                "model_id": self.env["ir.model"]._get_id(CHECKLIST_MODEL),
                "domain_force": "[('task_id.name', '!=', 'Read-only Task')]",
                "perm_read": False,
                "perm_write": True,
                "perm_create": False,
                "perm_unlink": True,
            },
        )
        read_only_task = self.env["project.task"].create({"name": "Read-only Task", "project_id": self.project.id})
        item = self._new_item(task_id=read_only_task.id)
        self.assertEqual(item.with_user(self.user_dev).read(["name"])[0]["name"], "Write the migration")
        with unittest.TestCase.assertRaises(self, AccessError):
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertLeftInPlace(item, read_only_task)

    def test_items_converted_together_become_each_a_subtask_of_its_own_task(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        first_item = self._new_item(name="First item")
        second_item = self._new_item(
            name="Second item", task_id=other_task.id, user_id=self.user_lead.id, reviewer_id=self.user_dev.id
        )
        (first_item | second_item).with_user(self.user_dev).action_convert_to_subtask()
        self.assertFalse((first_item | second_item).exists())
        first_subtask, second_subtask = self._subtasks(self.task), self._subtasks(other_task)
        self.assertRecordValues(
            first_subtask | second_subtask,
            [
                {"name": "First item", "user_ids": self.user_dev.ids},
                {"name": "Second item", "user_ids": self.user_lead.ids},
            ],
        )
        self.assertNoteLinks(self.task, "First item", first_subtask, self.user_dev)
        self.assertNoteLinks(other_task, "Second item", second_subtask, self.user_dev)

    def test_converting_from_the_item_form_opens_the_subtask(self):
        item = self._new_item()
        action = item.with_user(self.user_dev).action_convert_to_subtask_open()
        subtask = self._subtasks(self.task)
        self.assertEqual(len(subtask), 1)
        self.assertFalse(item.exists())
        self.assertEqual(
            (action["type"], action["res_model"], action["res_id"], action["views"]),
            ("ir.actions.act_window", "project.task", subtask.id, [(False, "form")]),
        )

    def test_markup_in_the_name_of_an_item_is_text_in_the_note(self):
        item = self._new_item(name=MARKUP_NAME)
        item.with_user(self.user_dev).action_convert_to_subtask()
        subtask = self._subtasks(self.task)
        self.assertEqual(subtask.name, MARKUP_NAME)
        self.assertIn(str(escape(MARKUP_NAME)), self._conversion_notes(self.task).body)
        self.assertNoteLinks(self.task, MARKUP_NAME, subtask, self.user_dev)

    def test_each_item_row_of_the_task_form_converts_it_for_its_assignee_and_reviewer(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (item_list,) = form_arch.xpath("//field[@name='checklist_item_ids']/tree")
        (button,) = item_list.xpath("button[@name='action_convert_to_subtask']")
        self.assertEqual(
            {name: button.get(name) for name in ("type", "icon", "title")},
            {"type": "object", "icon": "fa-level-down", "title": "Convert to sub-task"},
        )
        self.assertEqual(literal_eval(button.get("attrs")), CONVERT_INVISIBLE)
        self.assertIn("The item is deleted.", button.get("confirm"))
        for field_name in ("hide_button", "allow_subtasks"):
            self.assertEqual(len(item_list.xpath("field[@name='%s']" % field_name)), 1, field_name)

    def test_the_item_form_converts_it_from_its_header(self):
        form_arch = self.env.ref("%s.project_task_checklist_item_view_form" % MODULE)._get_combined_arch()
        (button,) = form_arch.xpath("/form/header/button[@name='action_convert_to_subtask_open']")
        self.assertEqual(
            {name: button.get(name) for name in ("type", "string")},
            {"type": "object", "string": "Convert to Sub-task"},
        )
        self.assertEqual(literal_eval(button.get("attrs")), CONVERT_INVISIBLE)
        self.assertIn("The item is deleted.", button.get("confirm"))
        for field_name in ("hide_button", "allow_subtasks"):
            self.assertEqual(len(form_arch.xpath("//field[@name='%s']" % field_name)), 1, field_name)

    def test_a_project_without_subtasks_refuses_the_conversion(self):
        item = self._new_item()
        self.assertTrue(item.with_user(self.user_dev).allow_subtasks)
        self.project.allow_subtasks = False
        self.assertFalse(item.with_user(self.user_dev).allow_subtasks, "the buttons are hidden")
        with unittest.TestCase.assertRaisesRegex(self, UserError, "only in a project with Sub-tasks turned on"):
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertLeftInPlace(item, self.task)
