from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import Form, tagged
from odoo.tools.safe_eval import safe_eval

from .common import MemberAccessCommon

PEOPLE_NODE = "//sheet//field[@name='%s'][not(ancestor::field)]"
READONLY_BY_FIELD = {
    "user_ids": "not is_assignee_change_allowed",
    "uat_user_ids": "not is_uat_user_change_allowed",
}


@tagged("post_install", "-at_install")
class TestTaskFormPeople(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.form_arch = cls.env.ref("project.view_task_form2")._get_combined_arch()

    def _people_nodes(self, fname):
        nodes = self.form_arch.xpath(PEOPLE_NODE % fname)
        self.assertEqual(len(nodes), 1, fname)
        return nodes[0]

    def test_the_people_are_read_only_for_who_may_not_change_them(self):
        for fname, readonly in READONLY_BY_FIELD.items():
            self.assertEqual(self._people_nodes(fname).get("readonly"), readonly)
        fields = self.env["project.task"].with_user(self.reader).get_views([(False, "form")])["models"]
        self.assertTrue(
            {"is_assignee_change_allowed", "is_uat_user_change_allowed", "project_member_user_ids"}
            <= set(fields["project.task"]["fields"])
        )

    def test_who_may_change_the_people_of_a_task(self):
        # (assignees, UAT users) per task and user
        expected_by_task = {
            self.public_task: {
                self.reader: (False, False),
                self.contributor: (False, False),
                self.editor: (False, True),
                self.admin: (True, True),
                self.sysadmin: (True, True),
            },
            self.private_reader_task: {
                self.reader: (False, True),
                self.contributor: (False, False),
                self.editor: (False, True),
                self.admin: (True, True),
            },
            self.standard_task: {self.outsider: (True, True), self.reader: (True, True)},
        }
        for task, expected_by_user in expected_by_task.items():
            for user, expected in expected_by_user.items():
                as_user = task.with_user(user)
                with self.subTest(task=task.name, user=user.login):
                    self.assertEqual((as_user.is_assignee_change_allowed, as_user.is_uat_user_change_allowed), expected)

    def test_a_task_being_created_keeps_its_people_editable(self):
        """A screen may fill in the creator as assignee: the form lets them clear it, the create checks."""
        for user in self.contributor | self.editor:
            new_task = self.env["project.task"].with_user(user).new({"project_id": self.public_project.id})
            with self.subTest(user=user.login):
                self.assertEqual(
                    (new_task.is_assignee_change_allowed, new_task.is_uat_user_change_allowed), (True, True)
                )

    def test_the_people_follow_the_saved_project_until_the_save(self):
        """The web client leaves a read-only field out of the save: a value typed before a change of
        project reaches the save, which judges it by the project the task lands in.
        """
        for task, other_project, expected in (
            (self._create_task(self.public_project, self.contributor), self.standard_project, (False, False)),
            (self.standard_task, self.public_project, (True, True)),
        ):
            task_form = Form(task.with_user(self.contributor))
            task_form.project_id = other_project
            with self.subTest(task=task.name):
                self.assertEqual((task_form.is_assignee_change_allowed, task_form.is_uat_user_change_allowed), expected)
        for fname, message in (
            ("user_ids", "Only an Admin of Public moves a task with assignees into it"),
            ("uat_user_ids", "Only an Editor or Admin of Public moves a task with UAT users into it"),
        ):
            task_form = Form(self._create_task(self.standard_project).with_user(self.contributor))
            getattr(task_form, fname).add(self.reader)
            task_form.project_id = self.public_project
            with self.subTest(fname=fname), self.assertRefused(AccessError, message):
                task_form.save()

    def test_the_pickers_extend_the_domains_they_replace(self):
        """The view repeats the domain of core's Assignees and of project_ai_driven's UAT users."""
        own_uat_arch = etree.fromstring(self.env.ref("project_ai_driven.view_task_form2_common").arch)
        replaced_domains = {
            "user_ids": self.env["project.task"]._fields["user_ids"].domain,
            "uat_user_ids": own_uat_arch.xpath("//field[@name='uat_user_ids']")[0].get("domain"),
        }
        for fname, replaced_domain in replaced_domains.items():
            with self.subTest(fname=fname):
                self.assertTrue(self._people_nodes(fname).get("domain").startswith(replaced_domain + " + "))

    def test_the_pickers_offer_only_members_inside_a_private_project(self):
        members = self.private_project.member_user_ids
        for fname in READONLY_BY_FIELD:
            domain_text = self._people_nodes(fname).get("domain")
            for task in self.private_task | self.public_task | self.standard_task:
                values = {
                    "project_access_mode": task.project_access_mode,
                    "project_member_user_ids": task.project_member_user_ids.ids,
                    "project_sharing_portal_user_ids": [],
                    "company_id": task.company_id.id,
                }
                offered_users = self.env["res.users"].search(safe_eval(domain_text, values))
                with self.subTest(fname=fname, task=task.name):
                    self.assertIn(self.reader, offered_users)
                    self.assertNotIn(self.portal, offered_users)
                    if task == self.private_task:
                        self.assertEqual(offered_users, members)
                    else:
                        self.assertIn(self.outsider, offered_users)
