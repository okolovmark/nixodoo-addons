import re
import unittest
from datetime import datetime

from lxml import etree, html

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import ProjectAiDrivenCommon
from .test_checklist import CHECKLIST_MODEL
from .test_estimate_rule import NO_ESTIMATE, NO_TYPE

DEADLINE = datetime(2026, 12, 1, 10, 0)
REFUSAL = "Only the assignee or the reviewer of a checklist item can convert it into a sub-task."
PRIVATE_TASK_REFUSAL = (
    "The checklist items of a private task cannot be converted into sub-tasks: set a project on the task first."
)
READ_ONLY_TASK_REFUSAL = "Only a user who may change the task can convert its checklist items into sub-tasks."
NO_PROJECT_REFUSAL = (
    "On a task without a project only the assignee of a checklist item or a project manager"
    " can convert it into a sub-task."
)
NO_PROJECT_ACCESS_REFUSAL = (
    "On a task of a project you have no access to, only the assignee of a checklist item"
    " can convert it into a sub-task."
)
CONVERT_CONFIRMATION = "Convert this item into a sub-task of the task? The item is deleted."


class TestChecklistItemToSubtask(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_backlog = cls._create_stage("Backlog", 1)
        cls.stage_progress = cls._create_stage("In Progress", 2)
        cls.task.write({"stage_id": cls.stage_progress.id, "date_deadline": datetime(2026, 12, 24, 18, 0)})
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

    def _create_project_manager(self):
        return self._create_user(
            "Project Manager", "project_ai_driven_manager", "manager@example.com", "project.group_project_manager"
        )

    def _create_template_without_project(self):
        return self.env["project.task"].create(
            {
                "name": "Template without Project",
                "is_template": True,
                "user_ids": [Command.set((self.user_dev | self.reviewer).ids)],
            },
        )

    def _create_task_of_team_project(self, privacy_visibility):
        """A task assigned to the developer and the reviewer, neither of them a
        team member of its project.
        """
        project = self.env["project.project"].create(
            {"name": "Team Project", "privacy_visibility": privacy_visibility, "user_id": self.user_lead.id},
        )
        return self.env["project.task"].create(
            {
                "name": "Task of the Team Project",
                "project_id": project.id,
                "user_ids": [Command.set((self.user_dev | self.reviewer).ids)],
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

    def _assignment_notifications(self, task):
        return self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", task.id), ("message_type", "=", "user_notification")],
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
        item = self._new_item(deadline=DEADLINE)
        self.assertNotIn(self.reviewer.partner_id, self.task.message_partner_ids)
        self.assertIs(item.with_user(self.user_dev).action_convert_to_subtask(), True)
        self.assertFalse(item.exists())
        subtask = self._subtasks(self.task)
        self.assertRecordValues(
            subtask,
            [
                {
                    "name": "Write the migration",
                    "project_id": self.project.id,
                    "user_ids": self.user_dev.ids,
                    "date_deadline": DEADLINE,
                    "stage_id": self.stage_backlog.id,
                },
            ],
        )
        self.assertIn(self.reviewer.partner_id, subtask.message_partner_ids)
        self.assertNoteLinks(self.task, "Write the migration", subtask, self.user_dev)

    def test_the_reviewer_converts_an_item_assigned_to_another(self):
        item = self._new_item()
        self.assertTrue(item.with_user(self.reviewer).can_convert)
        item.with_user(self.reviewer).action_convert_to_subtask()
        self.assertFalse(item.exists())
        subtask = self._subtasks(self.task)
        self.assertRecordValues(subtask, [{"name": "Write the migration", "user_ids": self.user_dev.ids}])
        self.assertNoteLinks(self.task, "Write the migration", subtask, self.reviewer)

    def test_the_reviewer_converting_an_item_notifies_its_assignee_of_the_subtask(self):
        item = self._new_item().with_context(mail_auto_subscribe_no_notify=False)
        item.with_user(self.reviewer).action_convert_to_subtask()
        notification = self._assignment_notifications(self._subtasks(self.task))
        self.assertEqual(len(notification), 1)
        self.assertEqual(notification.notification_ids.res_partner_id, self.user_dev.partner_id)
        self.assertEqual(notification.author_id, self.reviewer.partner_id)
        self.assertEqual(notification.subject, "You have been assigned to Write the migration")

    def test_the_assignee_converting_their_own_item_is_not_notified(self):
        item = self._new_item().with_context(mail_auto_subscribe_no_notify=False)
        item.with_user(self.user_dev).action_convert_to_subtask()
        subtask = self._subtasks(self.task)
        self.assertEqual(subtask.user_ids, self.user_dev)
        self.assertFalse(self._assignment_notifications(subtask))

    def test_another_project_user_is_refused_the_conversion_before_anything_is_created(self):
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

    def test_the_subtask_takes_the_first_unfolded_stage_of_the_project(self):
        self.stage_backlog.fold = True
        self._new_item().with_user(self.user_dev).action_convert_to_subtask()
        self.assertEqual(self._subtasks(self.task).stage_id, self.stage_progress)

    def test_a_subtask_refused_by_the_first_stage_of_the_project_leaves_the_item(self):
        self.project.estimate_required_states = self.stage_backlog
        item = self._new_item()
        with unittest.TestCase.assertRaises(self, ValidationError) as refusal:
            item.with_user(self.user_dev).action_convert_to_subtask()
        for message_part in (NO_TYPE, NO_ESTIMATE):
            self.assertIn(message_part, refusal.exception.args[0])
        # the rule refuses after core's INSERT: the sub-task row goes with the rollback of the request
        self.assertTrue(item.exists(), "the item goes only once its sub-task is accepted")
        self.assertFalse(self._conversion_notes(self.task), "the note follows the sub-task")

    def test_a_user_who_may_read_but_not_write_the_task_is_refused_before_anything_is_created(self):
        self.env["ir.access"].create(
            {
                "name": "Read-only task",
                "model_id": self.env["ir.model"]._get_id("project.task"),
                "operation": "u",
                "domain": "[('name', '!=', 'Read-only Task')]",
            },
        )
        read_only_task = self.env["project.task"].create({"name": "Read-only Task", "project_id": self.project.id})
        task_template = self.env["project.task"].create({"name": "Task Template", "is_template": True})
        for task in (read_only_task, task_template):
            with self.subTest(task=task.name):
                item = self._new_item(task_id=task.id)
                task.with_user(self.user_dev).check_access("read")
                self.assertFalse(item.with_user(self.user_dev).can_convert, "no button for its assignee")
                with unittest.TestCase.assertRaisesRegex(self, AccessError, "^%s$" % re.escape(READ_ONLY_TASK_REFUSAL)):
                    item.with_user(self.user_dev).action_convert_to_subtask()
                self.assertLeftInPlace(item, task)

    def test_an_item_of_a_private_task_is_refused_before_anything_is_created(self):
        private_task = self.env["project.task"].create(
            {"name": "Private Task", "user_ids": [Command.set(self.user_dev.ids)]},
        )
        item = self._new_item(task_id=private_task.id)
        private_task.with_user(self.user_dev).check_access("write")
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(PRIVATE_TASK_REFUSAL)):
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertLeftInPlace(item, private_task)

    def test_an_item_of_a_template_becomes_a_template_subtask(self):
        template_in_project = self.env["project.task"].create(
            {"name": "Template in Project", "project_id": self.project.id, "is_template": True},
        )
        template_without_project = self.env["project.task"].create(
            {"name": "Template without Project", "is_template": True, "user_ids": [Command.set(self.user_dev.ids)]},
        )
        no_stage = self.env["project.task.type"]
        for template, stage in ((template_in_project, self.stage_backlog), (template_without_project, no_stage)):
            with self.subTest(template=template.name):
                template.with_user(self.user_dev).check_access("write")
                item = self._new_item(task_id=template.id)
                item.with_user(self.user_dev).action_convert_to_subtask()
                self.assertFalse(item.exists())
                subtask = self._subtasks(template)
                self.assertRecordValues(
                    subtask,
                    [
                        {
                            "is_template": True,
                            "project_id": template.project_id.id,
                            "user_ids": self.user_dev.ids,
                            "stage_id": stage.id,
                        },
                    ],
                )
                self.assertNoteLinks(template, "Write the migration", subtask, self.user_dev)

    def test_who_may_convert_an_item(self):
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        manager = self._create_project_manager()
        assignee_and_reviewer = [Command.set((self.user_dev | self.reviewer).ids)]
        private_task = self.env["project.task"].create({"name": "Private Task", "user_ids": assignee_and_reviewer})
        template_in_project = self.env["project.task"].create(
            {"name": "Template in Project", "project_id": self.project.id, "is_template": True},
        )
        template_without_project = self._create_template_without_project()
        task_item = self._new_item()
        private_task_item = self._new_item(task_id=private_task.id)
        template_in_project_item = self._new_item(task_id=template_in_project.id)
        template_without_project_item = self._new_item(task_id=template_without_project.id)
        manager_item = self._new_item(task_id=template_without_project.id, reviewer_id=manager.id)
        cases = (
            ("task", task_item, self.user_dev, True),
            ("task", task_item, self.reviewer, True),
            ("task", task_item, outsider, False),
            ("private task", private_task_item, self.user_dev, False),
            ("private task", private_task_item, self.reviewer, False),
            ("template in project", template_in_project_item, self.reviewer, True),
            ("template without project", template_without_project_item, self.user_dev, True),
            ("template without project", template_without_project_item, self.reviewer, False),
            ("template without project", manager_item, manager, True),
        )
        for task_kind, item, user, can_convert in cases:
            with self.subTest(task=task_kind, user=user.name):
                self.assertEqual(item.with_user(user).can_convert, can_convert)

    def test_without_project_the_reviewer_is_refused_before_anything_is_created_but_the_assignee_converts(self):
        template = self._create_template_without_project()
        template.with_user(self.reviewer).check_access("write")
        item = self._new_item(task_id=template.id)
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(NO_PROJECT_REFUSAL)):
            item.with_user(self.reviewer).action_convert_to_subtask()
        self.assertLeftInPlace(item, template)
        item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertFalse(item.exists(), "its assignee converts it")
        self.assertRecordValues(self._subtasks(template), [{"user_ids": self.user_dev.ids, "project_id": False}])

    def test_a_project_manager_converts_an_item_of_a_task_without_project_they_review(self):
        manager = self._create_project_manager()
        template = self._create_template_without_project()
        item = self._new_item(task_id=template.id, reviewer_id=manager.id)
        item.with_user(manager).action_convert_to_subtask()
        self.assertFalse(item.exists())
        subtask = self._subtasks(template)
        self.assertRecordValues(subtask, [{"is_template": True, "project_id": False, "user_ids": self.user_dev.ids}])
        self.assertNoteLinks(template, "Write the migration", subtask, manager)

    def test_a_reviewer_assigned_to_a_task_of_a_project_they_cannot_read_is_refused_but_the_assignee_converts(self):
        for privacy_visibility in ("followers", "invited_users"):
            with self.subTest(privacy_visibility=privacy_visibility):
                task = self._create_task_of_team_project(privacy_visibility)
                task.with_user(self.reviewer).check_access("write")
                self.assertFalse(task.project_id.with_user(self.reviewer).has_access("read"))
                item = self._new_item(task_id=task.id)
                self.assertFalse(item.with_user(self.reviewer).can_convert, "no button for the reviewer")
                with unittest.TestCase.assertRaisesRegex(
                    self, UserError, "^%s$" % re.escape(NO_PROJECT_ACCESS_REFUSAL)
                ):
                    item.with_user(self.reviewer).action_convert_to_subtask()
                self.assertLeftInPlace(item, task)
                self.assertTrue(item.with_user(self.user_dev).can_convert)
                item.with_user(self.user_dev).action_convert_to_subtask()
                self.assertFalse(item.exists(), "its assignee converts it")
                self.assertRecordValues(
                    self._subtasks(task), [{"project_id": task.project_id.id, "user_ids": self.user_dev.ids}]
                )

    def test_a_reviewer_who_may_read_the_project_of_a_team_converts(self):
        task = self._create_task_of_team_project("followers")
        task.project_id.allowed_internal_user_ids = [Command.link(self.reviewer.id)]
        item = self._new_item(task_id=task.id)
        self.assertTrue(item.with_user(self.reviewer).can_convert)
        item.with_user(self.reviewer).action_convert_to_subtask()
        self.assertFalse(item.exists())
        subtask = self._subtasks(task)
        self.assertRecordValues(subtask, [{"project_id": task.project_id.id, "user_ids": self.user_dev.ids}])
        self.assertNoteLinks(task, "Write the migration", subtask, self.reviewer)

    def test_the_subtask_takes_no_default_of_the_calling_view(self):
        self.project.allow_milestones = True
        milestone = self.env["project.milestone"].create({"name": "Milestone", "project_id": self.project.id})
        tag = self.env["project.tags"].create({"name": "Calling view tag"})
        item = self._new_item()
        item.with_user(self.user_dev).with_context(
            default_is_template=True,
            default_tag_ids=tag.ids,
            default_milestone_id=milestone.id,
            default_name="Calling view name",
            default_stage_id=self.stage_progress.id,
        ).action_convert_to_subtask()
        self.assertRecordValues(
            self._subtasks(self.task),
            [
                {
                    "name": "Write the migration",
                    "is_template": False,
                    "tag_ids": [],
                    "milestone_id": False,
                    "stage_id": self.stage_backlog.id,
                },
            ],
        )

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

    def test_converting_from_the_item_form_opens_the_subtask_in_place_of_the_breadcrumbs(self):
        item = self._new_item()
        action = item.with_user(self.user_dev).action_convert_to_subtask_open()
        subtask = self._subtasks(self.task)
        self.assertEqual(len(subtask), 1)
        self.assertFalse(item.exists())
        self.assertEqual(
            action,
            {
                "type": "ir.actions.act_window",
                "res_model": "project.task",
                "res_id": subtask.id,
                "views": [(False, "form")],
                "target": "main",
            },
        )

    def test_each_item_row_of_the_task_form_converts_it_for_who_may_convert_it(self):
        arch = etree.fromstring(self.env["project.task"].get_view(view_type="form")["arch"])
        (item_list,) = arch.xpath("//page[@name='checklist']/field[@name='checklist_item_ids']/list")
        (button,) = item_list.xpath("button[@name='action_convert_to_subtask']")
        self.assertEqual(
            {name: button.get(name) for name in ("type", "icon", "title", "invisible", "confirm")},
            {
                "type": "object",
                "icon": "subdirectory_arrow_right",
                "title": "Convert to Sub-task",
                "invisible": "not can_convert",
                "confirm": CONVERT_CONFIRMATION,
            },
        )
        self.assertEqual(len(item_list.xpath("field[@name='can_convert']")), 1)

    def test_the_item_form_converts_it_from_its_header_for_who_may_convert_it(self):
        arch = etree.fromstring(self.env[CHECKLIST_MODEL].get_view(view_type="form")["arch"])
        (button,) = arch.xpath("/form/header/button[@name='action_convert_to_subtask_open']")
        self.assertEqual(
            {name: button.get(name) for name in ("type", "string", "invisible", "confirm")},
            {
                "type": "object",
                "string": "Convert to Sub-task",
                "invisible": "not can_convert",
                "confirm": CONVERT_CONFIRMATION,
            },
        )
        self.assertEqual(len(arch.xpath("//field[@name='can_convert']")), 1)
