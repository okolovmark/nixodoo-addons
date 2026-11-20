from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import convert_file

from odoo.addons.project_support_request.hooks import READ_RULE, uninstall_hook
from odoo.addons.project_support_request.models.ir_rule import SHIPPED_TASK_COMPANY_DOMAIN, TASK_COMPANY_RULE

NARROWED_DOMAIN = "[('company_id', 'in', company_ids), ('project_id.privacy_visibility', '!=', 'portal')]"


@tagged("post_install", "-at_install")
class TestSupportRequestAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.own_company = cls.env.ref("base.main_company")
        cls.support_company = cls.env["res.company"].create({"name": "Company of the Support Project"})
        stage = cls.env["project.task.type"].create({"name": "Backlog"})
        cls.project, cls.other_project = cls.env["project.project"].create(
            [
                {
                    "name": name,
                    "is_ai_driven": True,
                    "privacy_visibility": "portal",
                    "company_id": cls.support_company.id,
                    "type_ids": [Command.set(stage.ids)],
                }
                for name in ("Project That Develops This Odoo", "Other Project of That Company")
            ]
        )
        cls.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        cls.project.develops_this_odoo = True

        user_values = {"company_id": cls.own_company.id, "company_ids": [Command.set(cls.own_company.ids)]}
        cls.filer = new_test_user(
            cls.env, login="support_access_filer", groups="base.group_user,project.group_project_user", **user_values
        )
        cls.colleague = new_test_user(
            cls.env, login="support_access_colleague", groups="base.group_user", **user_values
        )
        cls.portal_user = new_test_user(
            cls.env, login="support_access_portal", groups="base.group_portal", **user_values
        )
        cls.own_request, cls.colleague_request, cls.other_project_task, cls.portal_request = cls.env[
            "project.task"
        ].create(
            [
                {
                    "name": name,
                    "project_id": project.id,
                    "uat_user_ids": [Command.set(uat_user.ids)],
                    "user_ids": [Command.clear()],
                }
                for name, project, uat_user in (
                    ("Request of the filer", cls.project, cls.filer),
                    ("Request of a colleague", cls.project, cls.colleague),
                    ("Task of another project", cls.other_project, cls.filer),
                    ("Request of a portal user", cls.project, cls.portal_user),
                )
            ]
        )
        # A follower of a task of a portal project: core's portal rule lets them read it.
        cls.portal_request.message_subscribe(partner_ids=cls.portal_user.partner_id.ids)

    def _as(self, user, records):
        return records.with_user(user).with_context(allowed_company_ids=self.own_company.ids)

    def _create_own_company_task(self):
        own_company_project = self.env["project.project"].create(
            {
                "name": "Project of the Filer's Company",
                "privacy_visibility": "portal",
                "company_id": self.own_company.id,
            }
        )
        return self.env["project.task"].create(
            {"name": "Task of the filer's company", "project_id": own_company_project.id}
        )

    def _assert_the_filer_reads_by_the_shipped_company_domain(self, own_company_task):
        tasks = self._as(
            self.filer, own_company_task | self.own_request | self.colleague_request | self.other_project_task
        )

        self.assertEqual(tasks.search([("id", "in", tasks.ids)]), own_company_task | self.own_request)
        for task in tasks - own_company_task - self.own_request:
            with self.assertRaises(AccessError):
                task.read(["name"])

    def _update_security_data(self):
        convert_file(self.env.cr, "project_support_request", "security/project_task_security.xml", {}, mode="update")

    def test_a_filer_reads_their_request_in_a_company_they_have_no_access_to(self):
        self.assertNotIn(self.support_company, self.filer.company_ids)
        own_request = self._as(self.filer, self.own_request)

        self.assertEqual(
            own_request.read(["name", "project_id", "stage_id", "company_id"])[0]["name"], "Request of the filer"
        )
        self.assertEqual(own_request.search([("project_id", "=", self.project.id)]), own_request)

    def test_the_request_stays_read_only_outside_the_companies_the_filer_selected(self):
        own_request = self._as(self.filer, self.own_request)

        with self.assertRaises(AccessError):
            own_request.write({"name": "Renamed by the filer"})
        with self.assertRaises(AccessError):
            own_request.unlink()
        self.filer.company_ids = [Command.link(self.support_company.id)]
        own_request.with_context(allowed_company_ids=self.support_company.ids).write({"name": "Renamed by the filer"})

    def test_the_company_rule_of_tasks_still_refuses_a_task_created_in_a_company_the_filer_did_not_select(self):
        # No project: a project of a company not selected would refuse the create before the task rule.
        task_values = {"name": "Created by the filer", "company_id": self.support_company.id}

        with self.assertRaises(AccessError):
            self._as(self.filer, self.env["project.task"]).create(task_values)
        self.filer.company_ids = [Command.link(self.support_company.id)]
        self.env["project.task"].with_user(self.filer).with_context(
            allowed_company_ids=self.support_company.ids
        ).create(task_values)

    def test_the_exception_reaches_no_other_request_and_no_task_of_another_project(self):
        tasks = self._as(self.filer, self.own_request | self.colleague_request | self.other_project_task)

        self.assertEqual(tasks.search([("id", "in", tasks.ids)]), self.own_request)
        for task in tasks - self.own_request:
            with self.assertRaises(AccessError):
                task.read(["name"])
        self.filer.company_ids = [Command.link(self.support_company.id)]
        tasks_in_both_companies = tasks.with_context(allowed_company_ids=(self.own_company | self.support_company).ids)
        self.assertEqual(tasks_in_both_companies.search([("id", "in", tasks.ids)]), tasks)
        self.assertEqual(len(tasks_in_both_companies.read(["name"])), 3)

    def test_a_portal_user_does_not_read_their_request_outside_their_companies(self):
        portal_request = self.portal_request.with_user(self.portal_user)

        with self.assertRaises(AccessError):
            portal_request.read(["name"])
        self.portal_user.company_ids = [Command.link(self.support_company.id)]
        portal_request.with_context(allowed_company_ids=self.support_company.ids).read(["name"])

    def test_reading_follows_the_companies_selected_not_all_the_companies_of_the_filer(self):
        self.filer.company_ids = [Command.link(self.support_company.id)]
        tasks = self._as(self.filer, self.own_request | self.colleague_request)

        self.assertEqual(tasks.search([("id", "in", tasks.ids)]), self.own_request)
        with self.assertRaises(AccessError):
            self._as(self.filer, self.colleague_request).read(["name"])

    def test_reading_follows_an_edit_of_core_s_company_rule_at_once_and_keeps_the_exception(self):
        own_company_task = self._create_own_company_task()
        tasks = self._as(self.filer, own_company_task | self.own_request | self.colleague_request)
        self.assertEqual(tasks.search([("id", "in", tasks.ids)]), own_company_task | self.own_request)

        self.env.ref(TASK_COMPANY_RULE).domain_force = NARROWED_DOMAIN

        self.assertEqual(tasks.search([("id", "in", tasks.ids)]), self.own_request)
        with self.assertRaises(AccessError):
            self._as(self.filer, own_company_task).read(["name"])
        self.assertEqual(self._as(self.filer, self.own_request).read(["name"])[0]["name"], "Request of the filer")

    def test_with_core_s_company_rule_archived_reading_keeps_the_domain_project_ships_it_with(self):
        own_company_task = self._create_own_company_task()
        core_rule = self.env.ref(TASK_COMPANY_RULE)
        core_rule.domain_force = NARROWED_DOMAIN

        core_rule.active = False

        self._assert_the_filer_reads_by_the_shipped_company_domain(own_company_task)

    def test_with_core_s_company_rule_deleted_reading_keeps_the_domain_project_ships_it_with(self):
        own_company_task = self._create_own_company_task()

        self.env.ref(TASK_COMPANY_RULE).unlink()

        self._assert_the_filer_reads_by_the_shipped_company_domain(own_company_task)

    def test_a_copy_of_core_s_company_rule_with_the_original_archived_does_not_open_reading(self):
        own_company_task = self._create_own_company_task()
        core_rule = self.env.ref(TASK_COMPANY_RULE)

        edited_copy = core_rule.copy({"domain_force": NARROWED_DOMAIN})
        core_rule.active = False

        self.assertFalse(edited_copy.perm_read)
        self._assert_the_filer_reads_by_the_shipped_company_domain(own_company_task)

    def test_core_s_company_rule_given_groups_restricts_reading_for_their_members_only(self):
        self.env.ref(TASK_COMPANY_RULE).groups = self.env.ref("project.group_project_user")
        tasks = self.own_request | self.colleague_request | self.other_project_task
        self.assertNotIn(self.env.ref("project.group_project_user"), self.colleague.groups_id)

        self.assertEqual(self._as(self.filer, tasks).search([("id", "in", tasks.ids)]), self.own_request)
        with self.assertRaises(AccessError):
            self._as(self.filer, self.colleague_request).read(["name"])
        colleague_tasks = self._as(self.colleague, tasks)
        self.assertEqual(colleague_tasks.search([("id", "in", tasks.ids)]), tasks)
        self.assertEqual(len(colleague_tasks.read(["name"])), 3)

    def test_the_uninstall_gives_core_s_company_rule_its_read_back(self):
        core_rule = self.env.ref(TASK_COMPANY_RULE)
        self.assertFalse(core_rule.perm_read)

        uninstall_hook(self.env.cr, self.env.registry)

        self.assertTrue(core_rule.perm_read)

    def test_the_uninstall_archives_the_copies_of_the_module_s_read_rule(self):
        read_rule = self.env.ref(READ_RULE)
        renamed_copy = read_rule.copy({"name": "Project/Task: read, edited"})
        visibility_rule = self.env.ref("project.task_visibility_rule")

        uninstall_hook(self.env.cr, self.env.registry)

        self.assertRecordValues(
            read_rule | renamed_copy | visibility_rule, [{"active": True}, {"active": False}, {"active": True}]
        )

    def test_after_the_uninstall_a_copy_of_core_s_company_rule_restricts_reading_with_the_original_archived(self):
        own_company_task = self._create_own_company_task()
        core_rule = self.env.ref(TASK_COMPANY_RULE)
        duplicate = core_rule.copy()
        core_rule.active = False

        uninstall_hook(self.env.cr, self.env.registry)
        self.env.ref(READ_RULE).unlink()

        tasks = self._as(self.filer, own_company_task | self.own_request | self.colleague_request)
        self.assertEqual(tasks.search([("id", "in", tasks.ids)]), own_company_task)
        for task in tasks - own_company_task:
            with self.assertRaises(AccessError):
                task.read(["name"])
        self.assertTrue(duplicate.perm_read)

    def test_the_uninstall_takes_a_copy_of_core_s_company_rule_by_its_name_or_its_domain(self):
        core_rule = self.env.ref(TASK_COMPANY_RULE)
        core_rule.domain_force = NARROWED_DOMAIN
        copies = core_rule.copy({"name": "Tasks: selected companies"})
        copies |= core_rule.copy({"name": "Tasks: shipped domain", "domain_force": SHIPPED_TASK_COMPANY_DOMAIN})
        copies |= core_rule.copy({"domain_force": "[('company_id', 'child_of', company_ids)]"})

        uninstall_hook(self.env.cr, self.env.registry)

        self.assertRecordValues(copies, [{"perm_read": True}] * 3)

    def test_the_uninstall_leaves_read_off_on_a_rule_that_is_not_an_active_global_copy_of_core_s_company_rule(self):
        core_rule = self.env.ref(TASK_COMPANY_RULE)
        unrelated_rule, other_model_rule = self.env["ir.rule"].create(
            [
                {
                    "name": name,
                    "model_id": self.env.ref(model_xmlid).id,
                    "domain_force": domain,
                    "perm_read": False,
                }
                for name, model_xmlid, domain in (
                    ("Tasks: assignees write", "project.model_project_task", "[('user_ids', 'in', user.id)]"),
                    ("Projects: multi-company", "project.model_project_project", SHIPPED_TASK_COMPANY_DOMAIN),
                )
            ]
        )
        group_copy = core_rule.copy({"groups": [Command.set(self.env.ref("project.group_project_user").ids)]})
        archived_copy = core_rule.copy({"active": False})
        read_rule_copy = self.env.ref(READ_RULE).copy({"perm_read": False, "perm_write": True})

        uninstall_hook(self.env.cr, self.env.registry)

        self.assertRecordValues(
            unrelated_rule | other_model_rule | group_copy | archived_copy | read_rule_copy, [{"perm_read": False}] * 5
        )

    def test_an_update_leaves_a_deleted_company_rule_of_tasks_alone(self):
        own_company_task = self._create_own_company_task()
        self.env.ref(TASK_COMPANY_RULE).unlink()

        self._update_security_data()

        self.assertFalse(self.env.ref(TASK_COMPANY_RULE, raise_if_not_found=False))
        self._assert_the_filer_reads_by_the_shipped_company_domain(own_company_task)

    def test_an_update_splits_core_s_company_rule_again_once_an_update_of_project_recreated_it(self):
        self.env.ref(TASK_COMPANY_RULE).unlink()
        convert_file(self.env.cr, "project", "security/project_security.xml", {}, mode="update")
        recreated_rule = self.env.ref(TASK_COMPANY_RULE)
        self.assertTrue(recreated_rule.perm_read)

        self._update_security_data()

        self.assertRecordValues(
            recreated_rule, [{"perm_read": False, "perm_write": True, "perm_create": True, "perm_unlink": True}]
        )
        self.assertEqual(self._as(self.filer, self.own_request).read(["name"])[0]["name"], "Request of the filer")

    def test_an_update_leaves_read_on_a_company_rule_of_tasks_with_no_other_permission(self):
        core_rule = self.env.ref(TASK_COMPANY_RULE)
        core_rule.write({"perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False})

        with self.assertLogs("odoo.addons.project_support_request.models.ir_rule", "WARNING"):
            self._update_security_data()

        self.assertTrue(core_rule.perm_read)
        with self.assertRaises(AccessError):
            self._as(self.filer, self.own_request).read(["name"])

    def test_an_update_gives_core_s_company_rule_its_write_only_scope_again_and_adds_no_rule(self):
        core_rule, read_rule = self.env.ref(TASK_COMPANY_RULE), self.env.ref(READ_RULE)
        core_rule.perm_read = True

        for _run in range(2):
            self._update_security_data()

        self.assertEqual(
            self.env["ir.rule"].search(
                [("model_id.model", "=", "project.task"), ("name", "=like", "Project/Task: multi-company%")]
            ),
            core_rule | read_rule,
        )
        self.assertRecordValues(
            core_rule | read_rule,
            [
                {"perm_read": False, "perm_write": True, "perm_create": True, "perm_unlink": True},
                {"perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False},
            ],
        )
