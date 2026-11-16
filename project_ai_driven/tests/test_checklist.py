import re

from lxml import html
from markupsafe import escape

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import Form, tagged

from ..hooks import MODULE
from .common import ProjectAiDrivenCommon

CHECKLIST_RECORDS = (
    "view_task_form2",
    "view_task_kanban_checklist",
    "project_task_checklist_item_view_form",
    "project_task_checklist_item_view_search",
    "project_task_checklist_item_view_tree",
    "action_view_checklist_item",
    "menu_action_view_checklist_item",
)
REFUSAL = "Only the assignee or the reviewer of a checklist item can change its state or name."
MARKUP_NAME = "<b>x</b> & y"


@tagged("post_install", "-at_install")
class TestChecklist(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env["project.task.checklist.item"]
        cls.QuietItem = cls.Item.with_context(mail_auto_subscribe_no_notify=True)

    def _new_item(self, **vals):
        return self.QuietItem.create(
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

    def test_a_line_added_in_the_task_form_is_assigned_to_its_author(self):
        with Form(self.task.with_user(self.user_lead)) as task_form:
            with task_form.checklist_item_ids.new() as line:
                line.name = "Check the wiring"
        self.assertRecordValues(
            self.task.checklist_item_ids,
            [{"name": "Check the wiring", "user_id": self.user_lead.id, "reviewer_id": self.user_lead.id}],
        )

    def test_an_item_created_without_assignee_is_assigned_to_its_author(self):
        item = self.QuietItem.with_user(self.user_lead).create({"name": "Order parts", "task_id": self.task.id})
        self.assertEqual(item.user_id, self.user_lead)

    def test_an_item_created_with_an_assignee_keeps_it(self):
        item = self.QuietItem.with_user(self.user_lead).create(
            {"name": "Order parts", "task_id": self.task.id, "user_id": self.user_dev.id},
        )
        self.assertEqual(item.user_id, self.user_dev)

    def test_the_task_has_no_default_user_field(self):
        self.assertNotIn("default_user", self.env["project.task"]._fields)

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
        self.assertIn("TODO: 1", as_dev.kanban_checklist)
        self.assertNotIn("Waiting", as_dev.kanban_checklist)
        item.write({"state": "waiting"})
        self.assertIn("Waiting: 1", as_dev.kanban_checklist)
        self.assertNotIn("TODO", as_dev.kanban_checklist)

    def test_only_reviewer_and_assignee_change_the_state_or_the_name(self):
        item = self.QuietItem.with_user(self.user_lead).create(
            {"name": "Checklist item", "task_id": self.task.id, "user_id": self.user_dev.id},
        )
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        for vals in ({"state": "done"}, {"name": "Renamed item"}):
            with self.assertRaises(UserError):
                item.with_user(outsider).write(vals)
        item.with_user(outsider).write({"deadline": "2026-12-01 10:00:00"})
        item.with_user(self.user_dev).change_state_done()
        self.assertEqual(item.state, "done")

    def test_copying_a_task_copies_its_items_with_their_state(self):
        self._new_item(name="Done item", state="done")
        self._new_item(name="Open item")
        task_copy = self.task.with_context(mail_auto_subscribe_no_notify=True).copy()
        self.assertEqual(
            sorted(task_copy.checklist_item_ids.mapped(lambda item: (item.name, item.state))),
            [("Done item", "done"), ("Open item", "todo")],
        )
        self.assertEqual(len(self.task.checklist_item_ids), 2, "the original keeps its own items")

    def test_checklist_needs_project_user(self):
        self._new_item()
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        self.assertTrue(self.Item.with_user(self.user_dev).search_count([("task_id", "=", self.task.id)]))
        with self.assertRaises(AccessError):
            self.Item.with_user(internal_user).search_count([])

    def test_the_checklist_says_checklist_item(self):
        self.assertEqual(self.Item._description, "Checklist Item")
        self.assertEqual(self.env["project.task"]._fields["checklist_item_ids"].string, "Checklist")
        for name in CHECKLIST_RECORDS:
            record = self.env.ref("%s.%s" % (MODULE, name))
            texts = [record.name, record.arch_db if record._name == "ir.ui.view" else ""]
            self.assertNotIn("subtask", " ".join(texts).lower(), name)
        form_arch = self.env.ref("%s.project_task_checklist_item_view_form" % MODULE).arch_db
        self.assertIn('string="Checklist Item"', form_arch)
        item = self._new_item()
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        with self.assertRaisesRegex(UserError, "^%s$" % re.escape(REFUSAL)):
            item.with_user(outsider).write({"state": "done"})


@tagged("post_install", "-at_install")
class TestChecklistNotification(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env["project.task.checklist.item"]
        cls.subtype = cls.env.ref("project_ai_driven.checklist_subtype")
        cls.project_subtype = cls.env.ref("project_ai_driven.project_checklist_subtype")
        cls.user_no_email = cls._create_project_user("Service Account", "project_ai_driven_service", False)
        cls.reviewer = cls._create_project_user("Reviewer", "project_ai_driven_reviewer", "reviewer@example.com")
        cls.follower = cls._create_project_user("Follower", "project_ai_driven_follower", "follower@example.com")
        cls.subscriber = cls._create_project_user(
            "Checklist Subscriber", "project_ai_driven_subscriber", "subscriber@example.com"
        )
        cls.project.message_subscribe(partner_ids=cls.follower.partner_id.ids)
        cls.task = cls.env["project.task"].create({"name": "Followed Task", "project_id": cls.project.id})
        cls.task.message_subscribe(partner_ids=cls.subscriber.partner_id.ids, subtype_ids=cls.subtype.ids)

    def _checklist_comments(self, body_text):
        return self.env["mail.message"].search(
            [
                ("model", "=", "project.task"),
                ("res_id", "=", self.task.id),
                ("message_type", "=", "comment"),
                ("body", "ilike", body_text),
            ],
            order="id",
        )

    def _item_vals(self, name, **vals):
        return {"name": name, "task_id": self.task.id, "user_id": self.user_dev.id, **vals}

    def _create_reviewed_item(self, name):
        """An item the lead creates over the API for the developer, with another reviewer."""
        return self.Item.with_user(self.user_lead).create(self._item_vals(name, reviewer_id=self.reviewer.id))

    def assertPostedTo(self, message, users):
        self.assertEqual(message.subtype_id, self.subtype)
        self.assertEqual(message.partner_ids, users.partner_id)
        self.assertLessEqual(users.partner_id, message.notification_ids.res_partner_id)

    def test_subtypes_are_internal_and_opt_in(self):
        self.assertEqual(self.project_subtype.parent_id, self.subtype)
        for subtype in self.subtype | self.project_subtype:
            self.assertTrue(subtype.internal, subtype.res_model)
            self.assertFalse(subtype.default, subtype.res_model)

    def test_update_is_posted_to_the_assignee_under_the_checklist_subtype(self):
        self.assertIn(self.follower.partner_id, self.task.message_partner_ids)
        item = self.Item.with_user(self.user_lead).create(self._item_vals("Check the BOM"))
        item.with_user(self.user_lead).write({"name": "Check the BOM revision"})

        message = self._checklist_comments("Check the BOM revision")
        self.assertPostedTo(message, self.user_dev)
        self.assertNotIn(
            self.follower.partner_id,
            message.notification_ids.res_partner_id,
            "a follower who did not opt into the checklist subtype is not notified",
        )

    def test_own_item_is_a_note_to_no_one(self):
        item = self.Item.with_user(self.user_dev).create(self._item_vals("Own item"))
        item.with_user(self.user_dev).write({"state": "done"})

        messages = self._checklist_comments("Own item")
        self.assertEqual(len(messages), 2, "the creation and the change of state")
        self.assertEqual(messages.subtype_id, self.env.ref("mail.mt_note"))
        self.assertFalse(messages.partner_ids)
        self.assertFalse(messages.notification_ids)

    def test_an_item_created_for_others_is_posted_to_its_assignee_and_reviewer(self):
        self._create_reviewed_item("Review the drawings")

        message = self._checklist_comments("Review the drawings")
        self.assertPostedTo(message, self.user_dev | self.reviewer)

    def test_an_assignee_who_is_also_the_reviewer_is_named_and_notified_once(self):
        self.Item.with_user(self.user_lead).create(
            self._item_vals("Test the relay", user_id=self.reviewer.id, reviewer_id=self.reviewer.id)
        )

        message = self._checklist_comments("Test the relay")
        self.assertEqual(len(message), 1, "one post, not one per role")
        self.assertPostedTo(message, self.reviewer)
        addressees = message.body.split("<em")[0]
        self.assertEqual(addressees.count(self.reviewer.name), 1, message.body)
        notified = message.notification_ids.filtered(
            lambda notification: notification.res_partner_id == self.reviewer.partner_id
        )
        self.assertEqual(len(notified), 1)

    def test_names_holding_markup_are_escaped_in_the_post(self):
        assignee = self._create_project_user(MARKUP_NAME, "project_ai_driven_markup", "markup@example.com")

        self.Item.with_user(self.user_lead).create(
            self._item_vals(MARKUP_NAME, user_id=assignee.id, reviewer_id=self.reviewer.id)
        )

        message = self._checklist_comments(str(escape(MARKUP_NAME)))
        self.assertEqual(len(message), 1)
        self.assertEqual(
            message.body.count(escape(MARKUP_NAME)), 3, "the assignee as addressee and in the intro, and the item"
        )
        self.assertFalse(html.fromstring(message.body).xpath("//b"), message.body)

    def test_the_assignee_changing_the_state_posts_to_the_reviewer(self):
        item = self._create_reviewed_item("Solder the board")

        item.with_user(self.user_dev).write({"state": "done"})

        message = self._checklist_comments("Solder the board")[-1]
        self.assertIn("Done", message.body)
        self.assertPostedTo(message, self.reviewer)

    def test_a_change_of_state_by_someone_else_is_refused_before_anything_is_posted_or_written(self):
        item = self._create_reviewed_item("Flash the firmware")
        posts_before = self._checklist_comments("Flash the firmware")

        # assertRaises rolls back what the refused write did; assertRaisesRegex leaves it to be seen
        with self.assertRaisesRegex(UserError, re.escape(REFUSAL)):
            item.with_user(self.user_lead).write({"state": "done"})

        self.assertEqual(item.state, "todo")
        self.assertEqual(self._checklist_comments("Flash the firmware"), posts_before)
        self.env.flush_all()
        self.env.cr.execute("SELECT state FROM project_task_checklist_item WHERE id = %s", (item.id,))
        self.assertEqual(self.env.cr.fetchone()[0], "todo")

    def test_someone_else_taking_the_item_cannot_change_its_state_or_name_in_the_same_write(self):
        item = self._create_reviewed_item("Seal the box")
        posts_before = self._checklist_comments("Seal the")

        for vals in ({"state": "done"}, {"name": "Seal the lid"}):
            with self.subTest(vals=vals), self.assertRaisesRegex(UserError, re.escape(REFUSAL)):
                item.with_user(self.user_lead).write({"user_id": self.user_lead.id, **vals})

        self.assertRecordValues(item, [{"state": "todo", "name": "Seal the box", "user_id": self.user_dev.id}])
        self.assertEqual(self._checklist_comments("Seal the"), posts_before)
        self.env.flush_all()
        self.env.cr.execute("SELECT state, name, user_id FROM project_task_checklist_item WHERE id = %s", (item.id,))
        self.assertEqual(self.env.cr.fetchone(), ("todo", "Seal the box", self.user_dev.id))

    def test_a_batch_holding_an_item_the_user_may_not_change_is_refused_as_a_whole(self):
        items = self._create_reviewed_item("Mount the fan") | self.Item.with_user(self.user_lead).create(
            self._item_vals("Mount the supply", user_id=self.user_lead.id)
        )
        posts_before = self._checklist_comments("Mount the")

        with self.assertRaisesRegex(UserError, re.escape(REFUSAL)):
            items.with_user(self.user_dev).write({"state": "done"})

        self.assertEqual(items.mapped("state"), ["todo", "todo"])
        self.assertEqual(self._checklist_comments("Mount the"), posts_before)
        self.env.flush_all()
        self.env.cr.execute("SELECT state FROM project_task_checklist_item WHERE id IN %s", (tuple(items.ids),))
        self.assertEqual(self.env.cr.fetchall(), [("todo",), ("todo",)])

    def test_items_renamed_together_are_posted_each_with_its_former_name(self):
        former_names = ("Fit the lid", "Fit the hinge")
        items = self._create_reviewed_item(former_names[0]) | self._create_reviewed_item(former_names[1])

        items.with_user(self.reviewer).write({"name": "Fit the case"})

        messages = self._checklist_comments("Fit the case")
        self.assertEqual(
            [[name for name in former_names if name in message.body.split("Updated from")[1]] for message in messages],
            [[former_names[0]], [former_names[1]]],
        )

    def test_the_reviewer_renaming_posts_the_former_name_to_the_assignee(self):
        item = self._create_reviewed_item("Check the fuse")

        item.with_user(self.reviewer).write({"name": "Check the main fuse"})

        message = self._checklist_comments("Check the main fuse")
        self.assertIn("Check the fuse", message.body.split("Updated from")[1])
        self.assertPostedTo(message, self.user_dev)

    def test_the_reviewer_reassigning_posts_to_the_new_assignee(self):
        item = self._create_reviewed_item("Pack the samples")

        item.with_user(self.reviewer).write({"user_id": self.follower.id})

        message = self._checklist_comments("Pack the samples")[-1]
        self.assertPostedTo(message, self.follower)
        self.assertNotIn(self.user_dev.partner_id, message.notification_ids.res_partner_id)

    def test_a_change_of_state_and_name_together_is_posted_once(self):
        item = self._create_reviewed_item("Label the cables")

        item.with_user(self.reviewer).write({"name": "Label the power cables", "state": "waiting"})

        message = self._checklist_comments("Label the power cables")
        self.assertEqual(len(message), 1)
        self.assertIn("Waiting", message.body)
        self.assertPostedTo(message, self.user_dev)

    def test_a_change_of_state_and_name_together_shows_each_name_under_its_own_state(self):
        item = self._create_reviewed_item("Glue the base")

        item.with_user(self.reviewer).write({"name": "Glue the base plate", "state": "waiting"})

        new_part, former_part = self._checklist_comments("Glue the base plate").body.split("Updated from")
        self.assertIn("Waiting", new_part)
        self.assertIn("TODO", former_part)
        self.assertNotIn("Waiting", former_part)

    def test_a_change_of_another_field_is_not_posted(self):
        item = self._create_reviewed_item("Book the lab")

        item.with_user(self.reviewer).write({"deadline": "2026-12-01 10:00:00"})

        self.assertEqual(len(self._checklist_comments("Book the lab")), 1, "the creation only")

    def test_a_follower_who_opted_in_gets_the_posts_of_others_but_not_their_own(self):
        self._create_reviewed_item("Order the enclosure")
        self.Item.with_user(self.subscriber).create(self._item_vals("Order the screws"))

        notified_of_others = self._checklist_comments("Order the enclosure").notification_ids.res_partner_id
        self.assertIn(self.subscriber.partner_id, notified_of_others)
        own_message = self._checklist_comments("Order the screws")
        self.assertPostedTo(own_message, self.user_dev)

    def test_creating_without_email_fails_without_the_flag(self):
        with self.assertRaises(UserError):
            self.Item.with_user(self.user_no_email).create(self._item_vals("Review schematics"))

    def test_creating_without_email_works_with_the_flag(self):
        item = (
            self.Item.with_user(self.user_no_email)
            .with_context(mail_auto_subscribe_no_notify=True)
            .create(self._item_vals("Review schematics"))
        )
        self.assertEqual(item.task_id, self.task)
        self.assertFalse(self._checklist_comments("Review schematics"))

    def test_changing_state_without_email_works_with_the_flag(self):
        Item = self.Item.with_user(self.user_no_email)
        item = Item.with_context(mail_auto_subscribe_no_notify=True).create(self._item_vals("Run tests"))
        item.with_context(mail_auto_subscribe_no_notify=True).write({"state": "done"})
        self.assertEqual(item.state, "done")
        self.assertFalse(self._checklist_comments("Run tests"))
