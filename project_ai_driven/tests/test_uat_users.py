from lxml import etree

from odoo import Command
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools.safe_eval import safe_eval
from odoo.tools.view_validation import get_expression_field_names

from .common import MODULE

ASSIGNEES = "//sheet//field[@name='user_ids'][not(ancestor::field)]"
OWN_UAT_SPEC = "//xpath[@expr=\"%s\"][@position='after']/field[@name='uat_user_ids']" % ASSIGNEES


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
        assignees = arch.xpath(ASSIGNEES)[0]
        # other modules may put their own fields right after the assignees too
        uat_users = [node for node in assignees.itersiblings() if node.get("name") == "uat_user_ids"]
        self.assertTrue(uat_users)
        self.assertEqual(uat_users[0].get("widget"), "many2many_avatar_user")
        self.assertFalse(uat_users[0].get("invisible"), "shown on the tasks of every project")
        own_arch = etree.fromstring(self.env.ref("%s.view_task_form2_common" % MODULE).arch)
        self.assertTrue(own_arch.xpath(OWN_UAT_SPEC), "added by this module")

    def test_uat_users_are_saved_from_the_task_form(self):
        user = new_test_user(self.env, "project_ai_driven_uat_tester", groups="base.group_user", name="Tester")
        task = self.env["project.task"].create({"name": "Accepted task"})
        with Form(task) as task_form:
            task_form.uat_user_ids.add(user)
        self.assertEqual(task.uat_user_ids, user)

    def test_no_copy_takes_the_uat_users(self):
        user = new_test_user(self.env, "project_ai_driven_uat_copied", groups="base.group_user", name="Tester")
        project = self.env["project.project"].create({"name": "Accepted project"})
        task, template = self.env["project.task"].create(
            [
                {"name": "Accepted task", "project_id": project.id, "uat_user_ids": [Command.set(user.ids)]},
                {"name": "Template", "project_id": project.id, "uat_user_ids": [Command.set(user.ids)]},
            ]
        )
        template.is_template = True
        copies = {
            "duplicate": task.copy(),
            "from a template": self.env["project.task"].browse(template.action_create_from_template()),
            "of a duplicated project": project.copy().tasks.filtered(lambda task_copy: task_copy.name == task.name),
        }
        for kind, task_copy in copies.items():
            with self.subTest(kind):
                self.assertEqual(len(task_copy), 1)
                self.assertFalse(task_copy.uat_user_ids)
        self.assertEqual((task | template).uat_user_ids, user)

    def test_uat_users_are_picked_as_the_assignees_are(self):
        """Active internal users of the task's company; an archived one already set stays shown."""
        company = self.env.company
        other_company = self.env["res.company"].create({"name": "Other company"})

        def create_user(login, groups="base.group_user", companies=company):
            return new_test_user(
                self.env,
                "project_ai_driven_uat_%s" % login,
                groups=groups,
                company_id=companies[0].id,
                company_ids=[Command.set(companies.ids)],
            )

        users = {
            "picked": create_user("picked"),
            "of both companies": create_user("both", companies=other_company | company),
            "of another company": create_user("other", companies=other_company),
            "archived": create_user("archived"),
            "portal": create_user("portal", groups="base.group_portal"),
        }
        users["archived"].active = False
        task = self.env["project.task"].create({"name": "Accepted task", "company_id": company.id})
        arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        domain_text = arch.xpath(ASSIGNEES + "/following-sibling::field[@name='uat_user_ids']")[0].get("domain")
        # evaluated as the web client does, on the task's values of the fields it names
        values = task.web_read({fname: {} for fname in get_expression_field_names(domain_text)})[0]
        domain = safe_eval(domain_text, values)
        candidates = self.env["res.users"].with_context(active_test=False).search(domain)
        self.assertEqual({name for name, user in users.items() if user in candidates}, {"picked", "of both companies"})
        task.uat_user_ids = users["picked"] | users["archived"]
        shown = task.web_read({"uat_user_ids": {"fields": {"display_name": {}}}})[0]["uat_user_ids"]
        self.assertEqual({row["id"] for row in shown}, set((users["picked"] | users["archived"]).ids))
