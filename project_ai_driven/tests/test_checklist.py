from odoo import Command
from odoo.exceptions import AccessError, UserError

from .common import ProjectAiDrivenCommon


class TestChecklist(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Subtask = cls.env["project.task.subtask"]
        cls.QuietSubtask = cls.Subtask.with_context(mail_auto_subscribe_no_notify=True)

    def _new_item(self, **vals):
        return self.QuietSubtask.create(
            {"name": "Checklist item", "task_id": self.task.id, "user_id": self.user_dev.id, **vals},
        )

    def test_recolor_marks_the_readers_own_todo_item(self):
        item = self._new_item()
        as_assignee = item.with_user(self.user_dev)
        self.assertTrue(as_assignee.recolor)
        item.write({"state": "done"})
        self.assertFalse(as_assignee.recolor)
        item.write({"state": "todo", "user_id": self.user_lead.id})
        self.assertFalse(as_assignee.recolor)

    def test_hide_button_follows_the_assignee(self):
        item = self._new_item()
        as_lead = item.with_user(self.user_lead)
        self.assertTrue(as_lead.hide_button)
        item.write({"user_id": self.user_lead.id})
        self.assertFalse(as_lead.hide_button)

    def test_default_user_follows_the_task_assignee(self):
        as_dev = self.task.with_user(self.user_dev)
        self.assertEqual(as_dev.default_user, self.task.user_ids)
        self.task.write({"user_ids": [Command.set(self.user_lead.ids)]})
        self.assertEqual(as_dev.default_user, self.user_lead)

    def test_completion_follows_the_readers_items(self):
        item = self._new_item()
        as_dev = self.task.with_user(self.user_dev)
        self.assertEqual(as_dev.completion, 0)
        self.assertIn("0.0% Done", as_dev.completion_xml)
        self.assertEqual(self.task.with_user(self.user_lead).task_completion(), 100, "no item of their own")
        item.write({"state": "done"})
        self.assertEqual(as_dev.completion, 100)
        self.assertIn("100.0% Done", as_dev.completion_xml)

    def test_kanban_summary_counts_todo_and_waiting(self):
        item = self._new_item()
        as_dev = self.task.with_user(self.user_dev)
        self.assertIn("TODO: 1", as_dev.kanban_subtasks)
        self.assertNotIn("Waiting", as_dev.kanban_subtasks)
        item.write({"state": "waiting"})
        self.assertIn("Waiting: 1", as_dev.kanban_subtasks)
        self.assertNotIn("TODO", as_dev.kanban_subtasks)

    def test_only_reviewer_and_assignee_change_an_item(self):
        item = self.QuietSubtask.with_user(self.user_lead).create(
            {"name": "Checklist item", "task_id": self.task.id, "user_id": self.user_dev.id},
        )
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        with self.assertRaises(UserError):
            item.with_user(outsider).write({"state": "done"})
        item.with_user(self.user_dev).change_state_done()
        self.assertEqual(item.state, "done")

    def test_copying_a_task_copies_its_items_with_their_state(self):
        self._new_item(name="Done item", state="done")
        self._new_item(name="Open item")
        task_copy = self.task.with_context(mail_auto_subscribe_no_notify=True).copy()
        self.assertEqual(
            sorted(task_copy.subtask_ids.mapped(lambda item: (item.name, item.state))),
            [("Done item", "done"), ("Open item", "todo")],
        )
        self.assertEqual(len(self.task.subtask_ids), 2, "the original keeps its own items")

    def test_copying_several_tasks_copies_each_ones_items_and_cases(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        self._new_item(name="First task item", state="waiting")
        self._new_item(name="Other task item", task_id=other_task.id)
        self.env["project.task.testcase"].create({"name": "Other task case", "task_id": other_task.id})
        tasks = self.task | other_task
        task_copy, other_copy = tasks.with_context(mail_auto_subscribe_no_notify=True).copy()
        self.assertEqual(
            task_copy.subtask_ids.mapped(lambda item: (item.name, item.state)), [("First task item", "waiting")]
        )
        self.assertFalse(task_copy.testcase_ids)
        self.assertEqual(other_copy.subtask_ids.mapped("name"), ["Other task item"])
        self.assertEqual(other_copy.testcase_ids.mapped("name"), ["Other task case"])

    def test_checklist_needs_project_user(self):
        self._new_item()
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        self.assertTrue(self.Subtask.with_user(self.user_dev).search_count([("task_id", "=", self.task.id)]))
        with self.assertRaises(AccessError):
            self.Subtask.with_user(internal_user).search_count([])


class TestChecklistNotification(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.subtype = cls.env.ref("project_ai_driven.subtasks_subtype")
        cls.project_subtype = cls.env.ref("project_ai_driven.all_subtask_subtype")
        cls.user_no_email = cls._create_project_user("Service Account", "project_ai_driven_service", False)
        cls.follower = cls._create_project_user("Follower", "project_ai_driven_follower", "follower@example.com")
        cls.project.message_subscribe(partner_ids=cls.follower.partner_id.ids)
        cls.task = cls.env["project.task"].create({"name": "Followed Task", "project_id": cls.project.id})

    def _checklist_comments(self, body_text):
        return self.env["mail.message"].search(
            [
                ("model", "=", "project.task"),
                ("res_id", "=", self.task.id),
                ("message_type", "=", "comment"),
                ("body", "ilike", body_text),
            ],
        )

    def _item_vals(self, name):
        return {"name": name, "task_id": self.task.id, "user_id": self.user_dev.id}

    def test_subtypes_are_internal_and_opt_in(self):
        self.assertEqual(self.project_subtype.parent_id, self.subtype)
        for subtype in self.subtype | self.project_subtype:
            self.assertTrue(subtype.internal, subtype.res_model)
            self.assertFalse(subtype.default, subtype.res_model)

    def test_update_is_posted_to_the_assignee_under_the_checklist_subtype(self):
        self.assertIn(self.follower.partner_id, self.task.message_partner_ids)
        item = self.env["project.task.subtask"].with_user(self.user_lead).create(self._item_vals("Check the BOM"))
        item.with_user(self.user_lead).write({"name": "Check the BOM revision"})

        message = self._checklist_comments("Check the BOM revision")
        self.assertEqual(message.subtype_id, self.subtype)
        notified_partners = message.notification_ids.res_partner_id
        self.assertIn(self.user_dev.partner_id, notified_partners)
        self.assertNotIn(
            self.follower.partner_id,
            notified_partners,
            "a follower who did not opt into the checklist subtype is not notified",
        )

    def test_own_item_is_logged_as_a_plain_note(self):
        self.env["project.task.subtask"].with_user(self.user_dev).create(self._item_vals("Own item"))
        message = self._checklist_comments("Own item")
        self.assertEqual(message.subtype_id, self.env.ref("mail.mt_note"))
        self.assertFalse(message.notification_ids)

    def test_creating_without_email_posts_without_the_flag(self):
        item = (
            self.env["project.task.subtask"].with_user(self.user_no_email).create(self._item_vals("Review schematics"))
        )
        self.assertEqual(item.task_id, self.task)
        message = self._checklist_comments("Review schematics")
        self.assertEqual(message.subtype_id, self.subtype)
        self.assertEqual(message.notification_ids.res_partner_id, self.user_dev.partner_id)
        self.assertFalse(message.email_from, "the account has no address to send from")

    def test_creating_without_email_works_with_the_flag(self):
        item = (
            self.env["project.task.subtask"]
            .with_user(self.user_no_email)
            .with_context(mail_auto_subscribe_no_notify=True)
            .create(self._item_vals("Review schematics"))
        )
        self.assertEqual(item.task_id, self.task)
        self.assertFalse(self._checklist_comments("Review schematics"))

    def test_changing_state_without_email_works_with_the_flag(self):
        Subtask = self.env["project.task.subtask"].with_user(self.user_no_email)
        item = Subtask.with_context(mail_auto_subscribe_no_notify=True).create(self._item_vals("Run tests"))
        item.with_context(mail_auto_subscribe_no_notify=True).write({"state": "done"})
        self.assertEqual(item.state, "done")
        self.assertFalse(self._checklist_comments("Run tests"))
