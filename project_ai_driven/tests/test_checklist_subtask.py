import re
import unittest
from ast import literal_eval
from datetime import date, datetime

from lxml import html
from markupsafe import escape

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged

from ..hooks import CHECKLIST_MODEL, MODULE
from .common import ProjectAiDrivenCommon
from .test_checklist import MARKUP_NAME
from .test_estimate_rule import NO_ESTIMATE, NO_TYPE

REFUSAL = "Only the assignee or the reviewer of a checklist item can convert it into a sub-task."
NO_TASK_CREATION = (
    "You may not create tasks in the project %s, so you cannot convert its checklist items into sub-tasks."
)
NO_TASK_CREATION_IN_PROJECTS = (
    "You may not create tasks in the projects %s, so you cannot convert their checklist items into sub-tasks."
)
CONVERT_INVISIBLE = {"invisible": [("can_convert", "=", False)]}


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

    def _new_item_of_invited_users_project(self, project_name="Invited Users Project", **vals):
        """An item of a task its assignee and its reviewer follow, in a project
        of invited internal users neither of them follows.
        """
        project = self.env["project.project"].create(
            {
                "name": project_name,
                "privacy_visibility": "followers",
                "allow_subtasks": True,
                "type_ids": [Command.clear()],
            },
        )
        self._create_stage("Invited Users Backlog", 1, project=project)
        task = self.env["project.task"].create({"name": "Invited Users Task", "project_id": project.id})
        task.message_subscribe(partner_ids=(self.user_dev | self.reviewer).partner_id.ids)
        return self._new_item(task_id=task.id, **vals)

    def _create_task_rule(self, domain_force, perm_create=True, perm_read=True):
        """A rule another module adds to project.task for project users."""
        return self.env["ir.rule"].create(
            {
                "name": "Tasks a module opens to project users",
                "model_id": self.env["ir.model"]._get_id("project.task"),
                "groups": [Command.link(self.env.ref("project.group_project_user").id)],
                "domain_force": domain_force,
                "perm_read": perm_read,
                "perm_write": False,
                "perm_create": perm_create,
                "perm_unlink": False,
            },
        )

    def _in_a_new_request(self, records, user):
        # a request starts with an empty cache: values the setup cached as superuser would skip the user's access checks
        self.env.invalidate_all()
        return records.with_user(user)

    def _subtasks(self, task):
        return self.env["project.task"].search([("parent_id", "=", task.id)])

    def _assignment_notifications(self, task):
        return self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", task.id), ("message_type", "=", "user_notification")],
        )

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
        self.assertFalse(self._assignment_notifications(subtask), "mail_auto_subscribe_no_notify silences the notice")
        self.assertNoteLinks(self.task, "Write the migration", subtask, self.reviewer)

    def test_the_assignee_is_notified_of_a_subtask_converted_by_the_reviewer(self):
        item = self._new_item()
        item.with_user(self.reviewer).with_context(mail_auto_subscribe_no_notify=False).action_convert_to_subtask()
        subtask = self._subtasks(self.task)
        notification = self._assignment_notifications(subtask)
        self.assertEqual(len(notification), 1)
        self.assertEqual(notification.partner_ids, self.user_dev.partner_id)
        self.assertEqual(notification.subject, "You have been assigned to %s" % subtask.display_name)

    def test_the_assignee_converting_their_own_item_is_not_notified(self):
        item = self._new_item()
        item.with_user(self.user_dev).with_context(mail_auto_subscribe_no_notify=False).action_convert_to_subtask()
        subtask = self._subtasks(self.task)
        self.assertEqual(subtask.user_ids, self.user_dev)
        self.assertFalse(self._assignment_notifications(subtask))

    def test_another_project_user_is_refused_the_conversion_before_anything_is_created(self):
        item = self._new_item()
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        as_outsider = self._in_a_new_request(item, outsider)
        self.assertFalse(as_outsider.can_convert, "no button")
        # in place: Odoo's assertRaises rolls back what a late refusal would leave behind
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(REFUSAL)):
            as_outsider.action_convert_to_subtask()
        self.assertLeftInPlace(item, self.task)

    def test_an_item_without_deadline_gives_a_subtask_without_deadline(self):
        item = self._new_item()
        item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertFalse(self._subtasks(self.task).date_deadline, "not the deadline of the task")

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

    def test_a_subtask_refused_by_the_closing_rule_of_the_first_stage_leaves_the_item(self):
        self._skip_next_to_pull_request_module()
        self.project.pr_required_states = self.stage_backlog
        item = self._new_item()
        with unittest.TestCase.assertRaises(self, ValidationError) as refusal:
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertIn("Choose a Resolution", refusal.exception.args[0])
        self.assertTrue(item.exists(), "the item goes only once its sub-task is accepted")
        self.assertFalse(self._conversion_notes(self.task), "the note follows the sub-task")

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
        self.assertFalse(item.with_user(self.user_dev).can_convert, "no button")
        with unittest.TestCase.assertRaises(self, AccessError):
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertLeftInPlace(item, read_only_task)

    def test_the_subtask_takes_no_default_of_the_calling_view(self):
        milestone = self.env["project.milestone"].create({"name": "Milestone", "project_id": self.project.id})
        customer = self.env["res.partner"].create({"name": "Calling view customer"})
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        item = self._new_item()
        item.with_user(self.user_dev).with_context(
            default_partner_id=customer.id,
            default_milestone_id=milestone.id,
            default_user_ids=self.user_lead.ids,
            default_parent_id=other_task.id,
            default_stage_id=self.stage_progress.id,
        ).action_convert_to_subtask()
        self.assertRecordValues(
            self._subtasks(self.task),
            [
                {
                    "partner_id": False,
                    "milestone_id": False,
                    "user_ids": self.user_dev.ids,
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

    def test_markup_in_the_name_of_an_item_is_text_in_the_note(self):
        item = self._new_item(name=MARKUP_NAME)
        item.with_user(self.user_dev).action_convert_to_subtask()
        subtask = self._subtasks(self.task)
        self.assertEqual(subtask.name, MARKUP_NAME)
        self.assertIn(str(escape(MARKUP_NAME)), self._conversion_notes(self.task).body)
        self.assertNoteLinks(self.task, MARKUP_NAME, subtask, self.user_dev)

    def test_each_item_row_of_the_task_form_converts_it_for_a_user_who_may_convert(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (item_list,) = form_arch.xpath("//field[@name='checklist_item_ids']/tree")
        (button,) = item_list.xpath("button[@name='action_convert_to_subtask']")
        self.assertEqual(
            {name: button.get(name) for name in ("type", "icon", "title")},
            {"type": "object", "icon": "fa-level-down", "title": "Convert to Sub-task"},
        )
        self.assertEqual(literal_eval(button.get("attrs")), CONVERT_INVISIBLE)
        self.assertIn("The item is deleted.", button.get("confirm"))
        self.assertEqual(len(item_list.xpath("field[@name='can_convert']")), 1)

    def test_the_item_form_converts_it_from_its_header(self):
        form_arch = self.env.ref("%s.project_task_checklist_item_view_form" % MODULE)._get_combined_arch()
        (button,) = form_arch.xpath("/form/header/button[@name='action_convert_to_subtask_open']")
        self.assertEqual(
            {name: button.get(name) for name in ("type", "string")},
            {"type": "object", "string": "Convert to Sub-task"},
        )
        self.assertEqual(literal_eval(button.get("attrs")), CONVERT_INVISIBLE)
        self.assertIn("The item is deleted.", button.get("confirm"))
        self.assertEqual(len(form_arch.xpath("//field[@name='can_convert']")), 1)

    def test_computing_the_conversion_in_an_onchange_adds_no_subtask_to_the_task(self):
        item = self._new_item()
        as_assignee = self._in_a_new_request(self.task, self.user_dev)
        task_in_onchange = as_assignee.new(
            {"checklist_item_ids": [Command.update(item.id, {"name": "Renamed in the form"})]}, origin=as_assignee
        )
        self.assertFalse(task_in_onchange.checklist_item_ids.can_convert, "a line of an onchange")
        self.assertFalse(task_in_onchange.child_ids)

    def test_a_project_without_subtasks_refuses_the_conversion(self):
        item = self._new_item()
        self.assertTrue(item.with_user(self.user_dev).can_convert)
        self.project.allow_subtasks = False
        self.assertFalse(item.with_user(self.user_dev).can_convert, "the buttons are hidden")
        with unittest.TestCase.assertRaisesRegex(self, UserError, "only in a project with Sub-tasks turned on"):
            item.with_user(self.user_dev).action_convert_to_subtask()
        self.assertLeftInPlace(item, self.task)

    def test_an_item_of_a_task_without_project_is_refused_by_the_subtasks_setting(self):
        task = self.env["project.task"].create({"name": "Private Task", "user_ids": [Command.set(self.user_dev.ids)]})
        item = self._new_item(task_id=task.id)
        as_assignee = self._in_a_new_request(item, self.user_dev)
        self.assertFalse(as_assignee.can_convert, "no button")
        with unittest.TestCase.assertRaisesRegex(self, UserError, "only in a project with Sub-tasks turned on"):
            as_assignee.action_convert_to_subtask()
        self.assertLeftInPlace(item, task)

    def test_a_reviewer_outside_an_invited_users_project_is_refused_before_anything_is_created(self):
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertFalse(as_reviewer.can_convert, "no button")
        refusal = NO_TASK_CREATION % "Invited Users Project"
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(refusal)):
            as_reviewer.action_convert_to_subtask()
        self.assertLeftInPlace(item, task)

    def test_the_assignee_outside_an_invited_users_project_is_refused_as_well(self):
        # core reads the new sub-task before writing its assignees, and refuses them that read
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        as_assignee = self._in_a_new_request(item, self.user_dev)
        self.assertFalse(as_assignee.can_convert, "no button")
        refusal = NO_TASK_CREATION % "Invited Users Project"
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(refusal)):
            as_assignee.action_convert_to_subtask()
        self.assertLeftInPlace(item, task)

    def test_items_of_several_refused_projects_are_refused_naming_each_project(self):
        first_item = self._new_item_of_invited_users_project()
        second_item = self._new_item_of_invited_users_project(project_name="Second Invited Users Project")
        items = self._in_a_new_request(first_item | second_item, self.reviewer)
        refusal = NO_TASK_CREATION_IN_PROJECTS % "Invited Users Project, Second Invited Users Project"
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(refusal)):
            items.action_convert_to_subtask()
        self.assertLeftInPlace(first_item, first_item.task_id)
        self.assertLeftInPlace(second_item, second_item.task_id)

    def test_a_task_rule_another_module_adds_lets_a_reviewer_outside_an_invited_users_project_convert(self):
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        self._create_task_rule("[('project_id', '=', %d)]" % task.project_id.id)
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertTrue(as_reviewer.can_convert)
        as_reviewer.action_convert_to_subtask()
        self.assertFalse(item.exists())
        self.assertRecordValues(
            self._subtasks(task), [{"project_id": task.project_id.id, "user_ids": self.user_dev.ids}]
        )

    def test_a_task_rule_opening_the_tasks_to_read_only_leaves_the_reviewer_refused(self):
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        self._create_task_rule("[('project_id', '=', %d)]" % task.project_id.id, perm_create=False)
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertFalse(as_reviewer.can_convert, "no button")
        refusal = NO_TASK_CREATION % "Invited Users Project"
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(refusal)):
            as_reviewer.action_convert_to_subtask()
        self.assertLeftInPlace(item, task)

    def test_a_task_rule_opening_task_creation_only_leaves_the_reviewer_refused(self):
        # core reads the new row before it checks the create rule
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        self._create_task_rule("[('project_id', '=', %d)]" % task.project_id.id, perm_read=False)
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertFalse(as_reviewer.can_convert, "no button")
        refusal = NO_TASK_CREATION % "Invited Users Project"
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(refusal)):
            as_reviewer.action_convert_to_subtask()
        self.assertLeftInPlace(item, task)

    def test_the_right_to_convert_is_computed_for_each_company_selection(self):
        other_company = self.env["res.company"].create({"name": "Other Company"})
        self.reviewer.company_ids = [Command.link(other_company.id)]
        item = self._new_item_of_invited_users_project()
        main_company = item.task_id.company_id
        self._create_task_rule(
            "[('project_id', '=', %d), ('company_id', '=', company_id)]" % item.task_id.project_id.id
        )
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertTrue(as_reviewer.with_context(allowed_company_ids=(main_company | other_company).ids).can_convert)
        self.assertFalse(
            as_reviewer.with_context(allowed_company_ids=(other_company | main_company).ids).can_convert,
            "the rules are those of the current company",
        )

    def test_a_task_rule_searching_a_hierarchy_is_followed(self):
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        other_company = self.env["res.company"].create({"name": "Other Company"})
        rule = self._create_task_rule("[('company_id', 'child_of', [%d])]" % other_company.id)
        self.assertFalse(self._in_a_new_request(item, self.reviewer).can_convert, "not a task of that company")
        rule.domain_force = "[('company_id', 'child_of', company_ids)]"
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertTrue(as_reviewer.can_convert)
        as_reviewer.action_convert_to_subtask()
        self.assertEqual(self._subtasks(task).user_ids, self.user_dev)

    def test_a_user_without_the_right_to_create_tasks_is_refused_the_conversion(self):
        item = self._new_item()
        self.env["ir.model.access"].search([("model_id.model", "=", "project.task")]).perm_create = False
        as_assignee = self._in_a_new_request(item, self.user_dev)
        self.assertFalse(as_assignee.can_convert, "no button")
        refusal = NO_TASK_CREATION % "AI-driven Project"
        with unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(refusal)):
            as_assignee.action_convert_to_subtask()
        self.assertLeftInPlace(item, self.task)

    def test_a_task_rule_on_the_creator_lets_the_reviewer_create_the_subtask(self):
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        self._create_task_rule("[('create_uid', '=', user.id)]")
        as_reviewer = self._in_a_new_request(item, self.reviewer)
        self.assertTrue(as_reviewer.can_convert)
        as_reviewer.action_convert_to_subtask()
        self.assertEqual(self._subtasks(task).create_uid, self.reviewer)

    def test_a_reviewer_who_follows_an_invited_users_project_converts_its_items(self):
        item = self._new_item_of_invited_users_project()
        task = item.task_id
        task.project_id.message_subscribe(partner_ids=self.reviewer.partner_id.ids)
        self.assertTrue(self._in_a_new_request(item, self.reviewer).can_convert)
        self.assertFalse(item.with_user(self.user_dev).can_convert, "computed for each user")
        self._in_a_new_request(item, self.reviewer).action_convert_to_subtask()
        self.assertFalse(item.exists())
        self.assertRecordValues(
            self._subtasks(task), [{"project_id": task.project_id.id, "user_ids": self.user_dev.ids}]
        )

    def test_a_project_manager_converts_the_items_they_review_in_any_project(self):
        manager = self._create_user(
            "Project Manager", "project_ai_driven_manager", "manager@example.com", "project.group_project_manager"
        )
        item = self._new_item_of_invited_users_project(reviewer_id=manager.id)
        task = item.task_id
        as_manager = self._in_a_new_request(item, manager)
        self.assertTrue(as_manager.can_convert)
        as_manager.action_convert_to_subtask()
        self.assertFalse(item.exists())
        self.assertEqual(self._subtasks(task).user_ids, self.user_dev)

    def test_an_item_of_another_allowed_company_converts_into_that_company(self):
        other_company = self.env["res.company"].create({"name": "Other Company"})
        self.reviewer.company_ids = [Command.link(other_company.id)]
        project = self.env["project.project"].create(
            {
                "name": "Other Company Project",
                "company_id": other_company.id,
                "privacy_visibility": "employees",
                "allow_subtasks": True,
                "type_ids": [Command.clear()],
            },
        )
        self._create_stage("Other Company Backlog", 1, project=project)
        task = self.env["project.task"].create({"name": "Other Company Task", "project_id": project.id})
        item = self._new_item(task_id=task.id)
        as_reviewer = self._in_a_new_request(item, self.reviewer).with_context(
            allowed_company_ids=(self.env.company | other_company).ids
        )
        self.assertTrue(as_reviewer.can_convert)
        as_reviewer.action_convert_to_subtask()
        self.assertEqual(self._subtasks(task).company_id, other_company)
