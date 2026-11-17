import re
import unittest
from unittest.mock import patch

from lxml import etree, html
from markupsafe import escape

from odoo import Command
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tests import Form
from odoo.tools.translate import code_translations

from .common import MODULE, ProjectAiDrivenCommon

CHECKLIST_MODEL = "project.task.checklist.item"
CHECKLIST_RECORDS = (
    "view_task_form2",
    "view_task_kanban_checklist",
    "project_task_checklist_item_view_form",
    "project_task_checklist_item_view_search",
    "project_task_checklist_item_view_tree",
    "action_view_checklist_item",
    "menu_action_view_checklist_item",
)
REFUSAL = "Only the assignee or the reviewer of a checklist item can change its state, name or assignee."
MARKUP_NAME = "<b>x</b> & y"


class TestChecklist(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env[CHECKLIST_MODEL]
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

    def test_two_users_read_their_own_values_of_an_item_and_its_task_in_one_transaction(self):
        item = self._new_item()
        for user, item_values, task_values in (
            (self.user_dev, (False, True), (0, True, True)),
            (self.user_lead, (True, False), (100, False, False)),
        ):
            with self.subTest(user=user.name):
                user_item, user_task = item.with_user(user), self.task.with_user(user)
                self.assertEqual((user_item.hide_button, user_item.recolor), item_values)
                self.assertEqual(
                    (
                        user_task.completion,
                        "% Done" in user_task.completion_xml,
                        "TODO: 1" in user_task.kanban_checklist,
                    ),
                    task_values,
                )

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

    def test_copying_several_tasks_copies_each_ones_items_and_cases(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        self._new_item(name="First task item", state="waiting")
        self._new_item(name="Other task item", task_id=other_task.id)
        self.env["project.task.testcase"].create({"name": "Other task case", "task_id": other_task.id})
        tasks = self.task | other_task
        task_copy, other_copy = tasks.with_context(mail_auto_subscribe_no_notify=True).copy()
        self.assertEqual(
            task_copy.checklist_item_ids.mapped(lambda item: (item.name, item.state)), [("First task item", "waiting")]
        )
        self.assertFalse(task_copy.testcase_ids)
        self.assertEqual(other_copy.checklist_item_ids.mapped("name"), ["Other task item"])
        self.assertEqual(other_copy.testcase_ids.mapped("name"), ["Other task case"])

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
            # the conversion buttons name the sub-task an item becomes, not the item
            text = " ".join(texts).lower().replace("action_convert_to_subtask", "")
            self.assertNotIn("subtask", text, name)
        form_arch = self.env.ref("%s.project_task_checklist_item_view_form" % MODULE).arch_db
        self.assertIn('string="Checklist Item"', form_arch)
        item = self._new_item()
        outsider = self._create_project_user("Outsider", "project_ai_driven_outsider", "outsider@example.com")
        with self.assertRaisesRegex(UserError, "^%s$" % re.escape(REFUSAL)):
            item.with_user(outsider).write({"state": "done"})

    def test_the_checklist_is_grouped_by_status(self):
        search_arch = self.env.ref("%s.project_task_checklist_item_view_search" % MODULE).arch_db
        self.assertEqual(etree.fromstring(search_arch).xpath("//filter[@name='state']/@string"), ["Status"])

    def test_an_item_has_no_activities(self):
        self.assertNotIn("activity_ids", self.Item._fields)
        self.assertFalse(self.env["ir.model"]._get(CHECKLIST_MODEL).is_mail_activity)
        self.env.cr.execute(
            """
            SELECT name FROM ir_model_fields WHERE model = %(model)s AND name ~ 'activity'
             UNION ALL
            SELECT module || '.' || name FROM ir_model_data WHERE name ~ (%(table)s || '__(my_)?activity_')
            """,
            {"model": CHECKLIST_MODEL, "table": self.Item._table},
        )
        self.assertFalse(self.env.cr.fetchall(), "the activity fields and every external id naming them")


class TestChecklistNotification(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Item = cls.env[CHECKLIST_MODEL]
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

    def assertRefusedInPlace(self):
        """The assertRaisesRegex of unittest: Odoo's rolls back to a savepoint, hiding what a refused write did."""
        return unittest.TestCase.assertRaisesRegex(self, UserError, "^%s$" % re.escape(REFUSAL))

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

    def test_a_change_is_posted_in_the_language_of_the_user_making_it(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        for state, label in (("done", "Fait"), ("todo", "A faire")):
            selection = self.env.ref("%s.selection__project_task_checklist_item__state__%s" % (MODULE, state))
            selection.with_context(lang="fr_FR").name = label
        sentences = (
            "I updated checklist item assigned to me:",
            "I updated checklist item, now it is assigned to %s:",
            "Updated from",
        )
        item = self._create_reviewed_item("Wire the lamp")
        as_developer_in_french = item.with_user(self.user_dev).with_context(lang="fr_FR")

        with patch.dict(
            code_translations.python_translations, {(MODULE, "fr_FR"): {text: "FR " + text for text in sentences}}
        ):
            as_developer_in_french.write({"name": "Wire the desk lamp", "state": "done"})
            as_developer_in_french.write({"user_id": self.follower.id})

        own_change, handover = self._checklist_comments("Wire the desk lamp")
        for text in (
            "FR I updated checklist item assigned to me:",
            "Fait</span></strong>: Wire the desk lamp",
            "FR Updated from",
            "A faire</span></strong>: Wire the lamp",
        ):
            self.assertIn(text, own_change.body)
        self.assertIn("FR I updated checklist item, now it is assigned to %s:" % self.follower.name, handover.body)

    def test_the_assignee_changing_the_state_posts_to_the_reviewer(self):
        item = self._create_reviewed_item("Solder the board")

        item.with_user(self.user_dev).write({"state": "done"})

        message = self._checklist_comments("Solder the board")[-1]
        self.assertIn("Done", message.body)
        self.assertPostedTo(message, self.reviewer)

    def test_a_change_of_state_by_someone_else_is_refused_before_anything_is_posted_or_written(self):
        item = self._create_reviewed_item("Flash the firmware")
        posts_before = self._checklist_comments("Flash the firmware")

        with self.assertRefusedInPlace():
            item.with_user(self.user_lead).write({"state": "done"})

        self.assertEqual(item.state, "todo")
        self.assertEqual(self._checklist_comments("Flash the firmware"), posts_before)
        self.env.flush_all()
        self.env.cr.execute("SELECT state FROM project_task_checklist_item WHERE id = %s", (item.id,))
        self.assertEqual(self.env.cr.fetchone()[0], "todo")

    def test_someone_else_cannot_take_the_item_alone_or_with_its_state_or_name(self):
        item = self._create_reviewed_item("Seal the box")
        posts_before = self._checklist_comments("Seal the")

        for vals in ({}, {"state": "done"}, {"name": "Seal the lid"}):
            with self.subTest(vals=vals), self.assertRefusedInPlace():
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

        with self.assertRefusedInPlace():
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

    def test_the_reviewer_reassigning_posts_to_the_new_and_the_former_assignee(self):
        item = self._create_reviewed_item("Pack the samples")

        item.with_user(self.reviewer).write({"user_id": self.follower.id})

        message = self._checklist_comments("Pack the samples")[-1]
        self.assertPostedTo(message, self.follower | self.user_dev)

    def test_items_reassigned_together_are_posted_each_to_its_own_former_assignee(self):
        items = self._create_reviewed_item("Tag the crates") | self.Item.with_user(self.user_lead).create(
            self._item_vals("Tag the pallets", user_id=self.user_lead.id, reviewer_id=self.reviewer.id)
        )

        items.with_user(self.reviewer).write({"user_id": self.follower.id})

        self.assertPostedTo(self._checklist_comments("Tag the crates")[-1], self.follower | self.user_dev)
        self.assertPostedTo(self._checklist_comments("Tag the pallets")[-1], self.follower | self.user_lead)

    def test_the_assignee_handing_the_item_over_posts_to_the_new_assignee_and_the_reviewer(self):
        item = self._create_reviewed_item("Ship the parts")

        item.with_user(self.user_dev).write({"user_id": self.follower.id})

        message = self._checklist_comments("Ship the parts")[-1]
        self.assertPostedTo(message, self.follower | self.reviewer)
        self.assertIn("now it is assigned to %s:" % self.follower.name, message.body)

    def test_the_assignee_may_hand_the_item_over_and_change_its_state_in_one_write(self):
        item = self._create_reviewed_item("Close the valve")

        item.with_user(self.user_dev).write({"user_id": self.follower.id, "state": "done"})

        self.assertRecordValues(item, [{"user_id": self.follower.id, "state": "done"}])
        self.assertPostedTo(self._checklist_comments("Close the valve")[-1], self.follower | self.reviewer)

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
        self.assertNotIn(self.subscriber.partner_id, own_message.notification_ids.res_partner_id)

    def test_creating_without_email_posts_without_the_flag(self):
        item = self.Item.with_user(self.user_no_email).create(self._item_vals("Review schematics"))
        self.assertEqual(item.task_id, self.task)
        message = self._checklist_comments("Review schematics")
        self.assertPostedTo(message, self.user_dev)
        self.assertEqual(
            message.notification_ids.res_partner_id,
            (self.user_dev | self.subscriber).partner_id,
            "the assignee and the follower who opted in",
        )
        self.assertFalse(message.email_from, "the account has no address to send from")

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


class TestChecklistAccess(ProjectAiDrivenCommon):
    """A global restriction hides one task from everyone, whichever modules'
    task access is installed; the developer is the assignee and the reviewer of
    every item.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._restrict_task_access("Hide the hidden task", "[('name', '!=', 'Hidden Task')]")
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.project.id})
        cls.both_tasks = cls.task | cls.hidden_task
        cls.Item = cls.env[CHECKLIST_MODEL].with_context(mail_auto_subscribe_no_notify=True)
        parties = {"user_id": cls.user_dev.id, "reviewer_id": cls.user_dev.id}
        cls.hidden_item = cls.Item.create({"name": "Hidden item", "task_id": cls.hidden_task.id, **parties})
        cls.visible_item = cls.Item.create({"name": "Visible item", "task_id": cls.task.id, **parties})
        cls.as_developer = cls.Item.with_user(cls.user_dev)

    def _task_access_consulted(self, items):
        """Whether the search and the unlink check of items, in their env, consult the access of the task."""
        task_model = type(self.env["project.task"])
        with patch.object(
            task_model, "_access_domain", autospec=True, side_effect=task_model._access_domain
        ) as task_access:
            items.search([])
            searched = task_access.called
            task_access.reset_mock()
            items._filtered_access("unlink")
            return searched, task_access.called

    def test_items_of_a_task_the_user_cannot_read_are_not_found(self):
        domain = [("task_id", "in", self.both_tasks.ids)]
        self.assertEqual(self.as_developer.search(domain), self.visible_item)
        self.assertEqual(self.as_developer.search_count(domain), 1)
        self.assertEqual(self.as_developer.read_group(domain, ["task_id"]), [(self.task.id,)])
        self.assertEqual([name for _item_id, name in self.as_developer.name_search("item", domain)], ["Visible item"])
        listed = self.as_developer.web_search_read(domain, {"name": {}})
        self.assertEqual((listed["length"], [record["name"] for record in listed["records"]]), (1, ["Visible item"]))
        self.env.invalidate_all()
        task_form = self.task.with_user(self.user_dev).read(["checklist_item_ids"])[0]
        self.assertEqual(task_form["checklist_item_ids"], self.visible_item.ids, "the items of a task it can read")

    def test_items_of_an_archived_task_the_user_can_read_are_found_and_changed(self):
        self.task.active = False
        self.assertEqual(self.as_developer.search([("task_id", "in", self.both_tasks.ids)]), self.visible_item)
        visible_item = self.visible_item.with_user(self.user_dev)
        visible_item.write({"state": "done"})
        self.assertEqual(visible_item.read(["state"]), [{"id": self.visible_item.id, "state": "done"}])

    def test_items_of_a_task_the_user_cannot_read_are_not_read(self):
        hidden_item = self.hidden_item.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_item.read(["name"])
        with self.assertRaises(AccessError):
            hidden_item.fetch(["name"])
        self.assertEqual(
            self.visible_item.with_user(self.user_dev).read(["name"]),
            [{"id": self.visible_item.id, "name": "Visible item"}],
        )

    def test_items_of_a_task_the_user_cannot_read_are_not_written_or_deleted(self):
        hidden_item = self.hidden_item.with_user(self.user_dev)
        for vals in ({"deadline": "2026-12-01 10:00:00"}, {"state": "done"}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                hidden_item.write(vals)
        with self.assertRaises(AccessError):
            hidden_item.unlink()
        self.assertRecordValues(self.hidden_item, [{"state": "todo", "deadline": False}])
        visible_item = self.visible_item.with_user(self.user_dev)
        visible_item.write({"state": "done"})
        visible_item.unlink()
        self.assertFalse(self.visible_item.exists())

    def test_items_cannot_be_added_to_a_task_the_user_cannot_read(self):
        with self.assertRaises(AccessError):
            self.as_developer.create({"name": "Sneaked item", "task_id": self.hidden_task.id})
        self.assertEqual(self.as_developer.create({"name": "Added item", "task_id": self.task.id}).task_id, self.task)

    def test_a_user_who_can_read_no_task_is_refused_an_item_before_it_is_inserted(self):
        self.assertTrue(self.as_developer.browse().has_access("create"))
        self._restrict_task_access("Hide every task", "[(0, '=', 1)]")
        self.assertFalse(self.as_developer.browse().has_access("create"))
        item_model = type(self.Item)
        with (
            patch.object(item_model, "_create", autospec=True, side_effect=item_model._create) as insert,
            self.assertRaises(AccessError),
        ):
            self.as_developer.create({"name": "Sneaked item", "task_id": self.task.id})
        self.assertFalse(insert.called)

    def test_an_item_deleted_meanwhile_fails_the_access_check_as_missing(self):
        """Two users with the task form open both delete the same line: the
        second save fails, as core evaluates the access domain on a deleted record.
        """
        self.visible_item.unlink()
        self.env.invalidate_all()
        with self.assertRaises(MissingError):
            self.task.with_user(self.user_dev).write({"checklist_item_ids": [Command.delete(self.visible_item.id)]})
        deleted_item = self.visible_item.with_user(self.user_dev)
        for operation in ("read", "write", "unlink"):
            self.env.invalidate_all()
            with self.subTest(operation=operation), self.assertRaises(MissingError):
                deleted_item.check_access(operation)
        self.env.invalidate_all()
        with self.assertRaises((AccessError, MissingError)):
            (deleted_item | self.hidden_item.with_user(self.user_dev)).check_access("unlink")

    def test_items_of_a_task_in_a_company_the_user_has_not_selected_are_not_found_or_read(self):
        home_company, other_company = self.env["res.company"].create(
            [{"name": "Checklist Home Company"}, {"name": "Checklist Other Company"}],
        )
        self.user_dev.write(
            {
                "company_ids": [Command.link(home_company.id), Command.link(other_company.id)],
                "company_id": home_company.id,
            },
        )
        other_project = self.env["project.project"].create({"name": "Other Project", "company_id": other_company.id})
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": other_project.id})
        other_item = self.Item.create(
            {
                "name": "Other item",
                "task_id": other_task.id,
                "user_id": self.user_dev.id,
                "reviewer_id": self.user_dev.id,
            },
        )
        domain = [("task_id", "=", other_task.id)]
        home_only = self.as_developer.with_context(allowed_company_ids=home_company.ids)
        self.assertFalse(home_only.search(domain))
        with self.assertRaises(AccessError):
            home_only.browse(other_item.id).read(["name"])
        with self.assertRaises(AccessError):
            home_only.browse(other_item.id).write({"deadline": "2026-12-01 10:00:00"})
        both_companies = self.as_developer.with_context(allowed_company_ids=(home_company | other_company).ids)
        self.assertEqual(both_companies.search(domain), other_item)
        both_companies.browse(other_item.id).write({"deadline": "2026-12-01 10:00:00"})
        self.assertEqual(both_companies.browse(other_item.id).read(["name"])[0]["name"], "Other item")

    def test_the_superuser_and_sudo_reach_every_item_without_the_task_access(self):
        domain = [("task_id", "in", self.both_tasks.ids)]
        every_item = self.hidden_item | self.visible_item
        self.assertEqual(self._task_access_consulted(every_item.with_user(self.user_dev)), (True, True), "as the user")
        for items in (every_item, every_item.with_user(self.user_dev).sudo()):
            with self.subTest(uid=items.env.uid):
                self.assertEqual(self._task_access_consulted(items), (False, False))
                self.assertEqual(items.search(domain), every_item)
                hidden_item = items & self.hidden_item
                hidden_item.write({"deadline": "2026-12-01 10:00:00"})
                self.assertEqual(hidden_item.read(["name"])[0]["name"], "Hidden item")
