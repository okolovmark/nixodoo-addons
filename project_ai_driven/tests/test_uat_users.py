from lxml import etree

from odoo import Command
from odoo.tests.common import Form, TransactionCase, tagged

from ..hooks import MODULE

OWN_UAT_SPEC = "//xpath[@expr=\"//field[@name='user_ids']\"][@position='after']/field[@name='uat_user_ids']"


@tagged("post_install", "-at_install")
class TestUatUsers(TransactionCase):
    def test_uat_users_keep_their_relation_table(self):
        field = self.env["project.task"]._fields["uat_user_ids"]
        self.assertEqual(
            (field.comodel_name, field.relation, field.column1, field.column2),
            ("res.users", "project_task__uat_user_rel", "task_id", "user_id"),
        )
        self.assertTrue(field.tracking)

    def test_the_module_declares_the_uat_users(self):
        field = self.env["ir.model.fields"]._get("project.task", "uat_user_ids")
        owners = self.env["ir.model.data"].search([("model", "=", "ir.model.fields"), ("res_id", "=", field.id)])
        self.assertIn(MODULE, owners.mapped("module"))

    def test_the_task_form_shows_the_uat_users_after_the_assignees(self):
        arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        assignees = arch.xpath("//sheet//field[@name='user_ids'][not(ancestor::field)]")[0]
        # other modules may put their own fields right after the assignees too
        uat_users = [node for node in assignees.itersiblings() if node.get("name") == "uat_user_ids"]
        self.assertTrue(uat_users)
        self.assertEqual(uat_users[0].get("widget"), "many2many_avatar_user")
        self.assertFalse(uat_users[0].get("attrs"), "shown on the tasks of every project")
        own_arch = etree.fromstring(self.env.ref("%s.view_task_form2_common" % MODULE).arch)
        self.assertTrue(own_arch.xpath(OWN_UAT_SPEC), "added by this module")

    def test_uat_users_are_saved_from_the_task_form(self):
        user = self.env["res.users"].create({"name": "Tester", "login": "project_ai_driven_uat_tester"})
        task = self.env["project.task"].create({"name": "Accepted task"})
        with Form(task) as task_form:
            task_form.uat_user_ids.add(user)
        self.assertEqual(task.uat_user_ids, user)

    def test_a_copy_takes_no_uat_users(self):
        tester = self.env["res.users"].create({"name": "Tester", "login": "project_ai_driven_uat_copy"})
        project = self.env["project.project"].create({"name": "With sub-tasks", "allow_subtasks": True})
        Task = self.env["project.task"]
        parent = Task.create({"name": "Parent", "project_id": project.id, "uat_user_ids": [Command.set(tester.ids)]})
        sub_task = Task.create({"name": "Sub-task", "parent_id": parent.id, "uat_user_ids": [Command.set(tester.ids)]})

        parent_copy = parent.copy()

        self.assertEqual(len(parent_copy.child_ids), 1, "the sub-task is copied with its parent")
        self.assertFalse((parent_copy | parent_copy.child_ids).uat_user_ids)
        self.assertFalse(sub_task.copy().uat_user_ids)
