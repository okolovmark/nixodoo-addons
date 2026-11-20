import csv
import os
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged
from odoo.tools import file_open, file_open_temporary_directory
from odoo.tools.convert import convert_file

from .. import hooks
from ..hooks import CORE_TASK_ACCESS_FILE, uninstall_hook


@tagged("post_install", "-at_install")
class TestSupportRequestAccess(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.main_company = cls.env.ref("base.main_company")
        cls.support_company = cls.env["res.company"].create({"name": "Support Company"})
        cls.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        # Each filer follows the tasks below and reads them as a follower: the internal ones, as the
        # project of another company is closed to them, and the portal one, in a Portal project.
        cls.support_project, cls.other_project = cls.env["project.project"].create(
            [
                {
                    "name": name,
                    "is_ai_driven": True,
                    "company_id": cls.support_company.id,
                    "privacy_visibility": "portal",
                }
                for name in ("Project That Develops This Odoo", "Other Project of the Support Company")
            ]
        )
        cls.support_project.develops_this_odoo = True
        cls.filer, cls.other_filer = (
            new_test_user(
                cls.env,
                login=login,
                groups="base.group_user,project.group_project_user",
                company_id=company.id,
                company_ids=[Command.set(company.ids)],
            )
            for login, company in (
                ("support_access_filer", cls.main_company),
                ("support_access_other_filer", cls.support_company),
            )
        )
        cls.portal_filer = new_test_user(
            cls.env,
            login="support_access_portal_filer",
            groups="base.group_portal",
            company_id=cls.main_company.id,
            company_ids=[Command.set(cls.main_company.ids)],
        )
        cls.request, cls.other_filer_request, cls.other_project_task, cls.portal_request = cls.env[
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
                    ("Request of the filer", cls.support_project, cls.filer),
                    ("Request of another filer", cls.support_project, cls.other_filer),
                    ("Task of another project", cls.other_project, cls.filer),
                    ("Request of a portal user", cls.support_project, cls.portal_filer),
                )
            ]
        )
        (cls.request | cls.other_filer_request | cls.other_project_task).message_subscribe(cls.filer.partner_id.ids)
        cls.portal_request.message_subscribe(cls.portal_filer.partner_id.ids)
        cls.read_restriction = cls.env.ref("project_support_request.task_comp_rule_read")
        cls.core_restriction = cls.env.ref("project.task_comp_rule")

    def _failed_accesses(self, user, task, operation):
        return self.env["ir.access"].with_user(user)._get_failed_accesses(task, operation)

    def _tasks_read_by(self, user, tasks, companies=None):
        task_model = self.env["project.task"].with_user(user)
        if companies:
            task_model = task_model.with_context(allowed_company_ids=companies.ids)
        return task_model.search([("id", "in", tasks.ids)])

    def _task_of_main_company(self, follower=None, privacy_visibility="portal"):
        project = self.env["project.project"].create(
            {
                "name": "Project of the main company",
                "company_id": self.main_company.id,
                "privacy_visibility": privacy_visibility,
            }
        )
        task = self.env["project.task"].create({"name": "Task of the main company", "project_id": project.id})
        if follower:
            task.message_subscribe(follower.partner_id.ids)
        return task

    def _customize_core_restriction(self):
        return self.env["ir.access"].browse(self.core_restriction.customize()["res_id"])

    def _update_module_data(self):
        convert_file(self.env, "project_support_request", "security/project_task_security.xml", {})

    def test_a_filer_reads_their_request_in_a_company_they_are_not_allowed_in(self):
        tasks = self.request | self.other_filer_request | self.other_project_task

        self.assertEqual(self.env["project.task"].with_user(self.filer).search([("id", "in", tasks.ids)]), self.request)
        self.assertEqual(
            self.request.with_user(self.filer).read(["name", "project_id", "stage_id", "company_id"])[0]["company_id"],
            (self.support_company.id, self.support_company.display_name),
        )

    def test_a_filer_allowed_in_the_company_of_their_request_reads_it_without_selecting_that_company(self):
        self.filer.company_ids = self.main_company | self.support_company

        request = self.request.with_user(self.filer).with_context(allowed_company_ids=self.main_company.ids)

        self.assertTrue(request.has_access("read"))
        self.assertFalse(request.has_access("write"), "the company stays unselected for anything but a read")

    def test_the_exception_is_read_only(self):
        request = self.request.with_user(self.filer)

        for operation in ("write", "unlink"):
            with self.subTest(operation=operation):
                self.assertFalse(request.has_access(operation))
                self.assertEqual(self._failed_accesses(self.filer, self.request, operation), self.core_restriction)
        with self.assertRaises(AccessError):
            request.write({"name": "Renamed by the filer"})
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(self.filer).create(
                {"name": "Created by the filer", "project_id": self.support_project.id}
            )

    def test_a_filer_comments_on_their_request_in_a_company_they_are_not_allowed_in(self):
        self.request.with_user(self.filer).message_post(body="Still happens", message_type="comment")

        self.assertEqual(self.request.message_ids[:1].author_id, self.filer.partner_id)

    def test_the_exception_does_not_reach_the_request_of_another_filer(self):
        self.assertFalse(self.other_filer_request.with_user(self.filer).has_access("read"))
        self.assertEqual(
            self._failed_accesses(self.filer, self.other_filer_request, "read"),
            self.read_restriction,
            "the filer follows the request: the company restriction alone refuses it",
        )

    def test_the_exception_does_not_reach_a_task_of_another_project(self):
        self.assertFalse(self.other_project_task.with_user(self.filer).has_access("read"))
        self.assertEqual(self._failed_accesses(self.filer, self.other_project_task, "read"), self.read_restriction)

    def test_a_portal_user_does_not_read_their_request_in_another_company(self):
        self.assertFalse(self.portal_request.with_user(self.portal_filer).has_access("read"))
        self.assertEqual(self._failed_accesses(self.portal_filer, self.portal_request, "read"), self.read_restriction)

    def test_the_core_restriction_keeps_create_write_and_delete_after_an_update_of_project(self):
        convert_file(self.env, "project", "security/ir.access.csv", {})
        self.assertEqual(self.core_restriction.operation, "crud", "an update of project gives core's restriction back")

        self._update_module_data()

        self.assertEqual((self.core_restriction.operation, self.read_restriction.operation), ("cud", "r"))
        self.assertEqual(
            self.env["project.task"].with_user(self.filer).search([("id", "=", self.request.id)]), self.request
        )

    def test_updating_the_module_once_core_restriction_is_deleted_narrows_its_customized_copy(self):
        customized_restriction = self._customize_core_restriction()
        customized_restriction.operation = "crud"
        self.core_restriction.unlink()

        with self.assertLogs("odoo.addons.project_support_request.models.ir_access", "INFO") as logs:
            self._update_module_data()

        self.assertEqual(customized_restriction.operation, "cud")
        self.assertIn(str(customized_restriction.ids), "\n".join(logs.output))
        self.assertEqual(self._tasks_read_by(self.filer, self.request | self.other_filer_request), self.request)

    def test_reads_follow_a_customize_that_edits_the_domain_of_core_restriction(self):
        customized_restriction = self._customize_core_restriction()
        customized_restriction.domain = (
            f"['|', ('company_id', 'in', company_ids + [False]), ('project_id', '=', {self.other_project.id})]"
        )

        self.assertEqual(
            self._tasks_read_by(self.filer, self.request | self.other_filer_request | self.other_project_task),
            self.request | self.other_project_task,
        )

    def test_reads_follow_a_customized_copy_once_core_restriction_is_deleted(self):
        main_task = self._task_of_main_company(follower=self.filer)
        customized_restriction = self._customize_core_restriction()
        customized_restriction.domain = (
            f"[('company_id', 'in', company_ids + [False]), ('project_id', '!=', {main_task.project_id.id})]"
        )

        self.core_restriction.unlink()

        self.assertEqual(self._tasks_read_by(self.filer, self.request | main_task), self.request)

    def test_reads_follow_a_renamed_copy_of_core_restriction_edited_in_place(self):
        self.core_restriction.domain = (
            f"['|', ('company_id', 'in', company_ids + [False]), ('project_id', '=', {self.other_project.id})]"
        )
        self._customize_core_restriction().name = "Tasks: in my companies"

        self.assertEqual(
            self._tasks_read_by(self.filer, self.request | self.other_filer_request | self.other_project_task),
            self.request | self.other_project_task,
        )

    def test_a_restriction_taken_for_core_one_restricts_reads_only_if_it_covers_write(self):
        main_task = self._task_of_main_company(follower=self.filer)
        customized_restriction = self._customize_core_restriction()
        customized_restriction.domain = (
            f"['|', ('company_id', 'in', company_ids + [False]), ('project_id', '=', {self.other_project.id})]"
        )
        tasks = self.request | self.other_project_task | main_task
        create_only = {"model_id": self.core_restriction.model_id.id, "operation": "c"}
        for look_alike, read_tasks in (
            (
                {
                    "name": f"{self.core_restriction.name}: not in the main project",
                    "domain": f"[('project_id', '!=', {main_task.project_id.id})]",
                },
                self.request | self.other_project_task,
            ),
            (
                {"name": "Tasks: created in my companies", "domain": self.core_restriction.domain},
                self.request | main_task,
            ),
        ):
            with self.subTest(look_alike["name"]):
                restriction = self.env["ir.access"].create(dict(create_only, **look_alike))
                self.assertEqual(self._tasks_read_by(self.filer, tasks), tasks, "create only")
                restriction.operation = "cu"
                self.assertEqual(self._tasks_read_by(self.filer, tasks), read_tasks, "create and update")
                restriction.unlink()

    def test_a_group_on_core_restriction_makes_it_a_permission_for_reads_too(self):
        company_task_readers = self.env["res.groups"].create({"name": "Readers of the tasks of their companies"})
        customized_restriction = self._customize_core_restriction()
        customized_restriction.write({"group_id": company_task_readers.id, "operation": "crud"})
        self._update_module_data()
        self.assertEqual(customized_restriction.operation, "crud", "a permission is not narrowed")
        member = new_test_user(
            self.env,
            login="support_access_company_task_reader",
            groups="base.group_user,project.group_project_user",
            company_id=self.main_company.id,
            company_ids=[Command.set(self.main_company.ids)],
        )
        member.group_ids = [Command.link(company_task_readers.id)]
        unfollowed_task = self._task_of_main_company(privacy_visibility="followers")
        tasks = self.request | self.other_filer_request | self.other_project_task | unfollowed_task

        for user, read_tasks in (
            (self.filer, self.request | self.other_filer_request | self.other_project_task),
            (member, unfollowed_task),
        ):
            with self.subTest(user=user.login):
                self.assertEqual(self._tasks_read_by(user, tasks), read_tasks)

    def test_reads_follow_the_companies_the_user_selects(self):
        self.filer.company_ids = self.main_company | self.support_company
        tasks = self.request | self.other_project_task

        for companies, read_tasks in (
            (self.main_company, self.request),
            (self.main_company | self.support_company, tasks),
        ):
            with self.subTest(companies=companies.mapped("name")):
                self.assertEqual(self._tasks_read_by(self.filer, tasks, companies), read_tasks)

    def test_without_an_active_core_restriction_reads_keep_the_domain_of_project_file(self):
        tasks = self.request | self.other_project_task

        self.core_restriction.active = False
        self.assertEqual(self._tasks_read_by(self.filer, tasks), self.request, "core's restriction archived")
        self.core_restriction.unlink()
        self.assertEqual(self._tasks_read_by(self.filer, tasks), self.request, "core's restriction deleted")

    def test_without_an_active_core_restriction_or_its_row_reads_keep_the_stock_domain(self):
        main_task = self._task_of_main_company(follower=self.filer)
        tasks = self.request | main_task | self.other_project_task

        # each change clears the access cache, so the read below evaluates the domain again
        for access_file, remove_core_restriction in (
            ("mail/security/ir.access.csv", lambda: self.core_restriction.write({"active": False})),
            ("project/security/no_such_file.csv", self.core_restriction.unlink),
        ):
            with self.subTest(access_file), patch.object(hooks, "CORE_TASK_ACCESS_FILE", access_file):
                remove_core_restriction()
                with self.assertLogs(hooks.__name__, "WARNING") as logs:
                    self.assertEqual(self._tasks_read_by(self.filer, tasks), self.request | main_task)
                self.assertIn("taken as Odoo ships it", "\n".join(logs.output))

    def test_updating_the_module_leaves_a_restriction_not_covering_update_and_uninstalling_gives_it_read(self):
        create_and_read, create_and_delete = self.env["ir.access"].create(
            [
                {
                    "name": f"{self.core_restriction.name}: created in my companies",
                    "model_id": self.core_restriction.model_id.id,
                    "operation": operation,
                    "domain": self.core_restriction.domain,
                }
                for operation in ("cr", "cd")
            ]
        )

        self._update_module_data()
        self.assertEqual((create_and_read | create_and_delete).mapped("operation"), ["cr", "cd"])
        self._uninstall()
        self.assertEqual((create_and_read | create_and_delete).mapped("operation"), ["cr", "crd"])

    def test_updating_the_module_takes_read_alone_off_a_copy_customized_before_the_install(self):
        customized_restriction = self._customize_core_restriction()
        # as a Customize before the install leaves it, Delete unticked
        customized_restriction.operation = "cru"

        self._update_module_data()

        self.assertEqual(customized_restriction.operation, "cu")
        self.assertEqual(self._tasks_read_by(self.filer, self.request | self.other_filer_request), self.request)
        self._uninstall()
        self.assertEqual(customized_restriction.operation, "cru")

    def test_a_customize_of_the_read_restriction_keeps_the_exception(self):
        customized_read_restriction = self.env["ir.access"].browse(self.read_restriction.customize()["res_id"])
        self.assertEqual(
            (self.read_restriction.active, customized_read_restriction.domain), (False, self.read_restriction.domain)
        )

        self.assertEqual(
            self._tasks_read_by(self.filer, self.request | self.other_filer_request | self.other_project_task),
            self.request,
        )

    def _uninstall(self):
        """The hook, then the removal of the module's own records."""
        uninstall_hook(self.env)
        self.read_restriction.unlink()

    def test_uninstalling_gives_core_its_own_restriction_back(self):
        with file_open(CORE_TASK_ACCESS_FILE) as access_file:
            core_row = next(row for row in csv.DictReader(access_file) if row["id"] == "task_comp_rule")

        uninstall_hook(self.env)

        self.assertEqual(self.core_restriction.operation, core_row["operation"])

    def test_uninstalling_after_a_customize_of_core_restriction_keeps_reads_restricted(self):
        customized_restriction = self._customize_core_restriction()
        self.assertEqual((customized_restriction.operation, self.core_restriction.active), ("cud", False))

        self._uninstall()

        self.assertFalse(self.request.with_user(self.filer).has_access("read"))
        self.assertEqual(customized_restriction.operation, "crud")

    def test_uninstalling_gives_read_alone_back_to_a_copy_customized_after_the_install(self):
        customized_restriction = self._customize_core_restriction()
        customized_restriction.operation = "cu"

        self._uninstall()

        self.assertEqual(customized_restriction.operation, "cru")
        self.assertFalse(self._tasks_read_by(self.filer, self.request | self.other_filer_request))

    def test_uninstalling_after_a_customize_that_unticks_update_keeps_reads_restricted(self):
        customized_restriction = self._customize_core_restriction()
        customized_restriction.operation = "cd"

        self._uninstall()

        self.assertEqual((customized_restriction.operation, self.core_restriction.operation), ("crd", "crud"))
        self.assertFalse(self._tasks_read_by(self.filer, self.request | self.other_filer_request))

    def test_uninstalling_gives_read_back_to_core_restriction_whatever_it_covers(self):
        self.core_restriction.operation = "cd"

        self._uninstall()

        self.assertEqual(self.core_restriction.operation, "crd")
        self.assertFalse(self._tasks_read_by(self.filer, self.request | self.other_filer_request))

    def test_uninstalling_leaves_a_group_on_core_restriction_a_permission(self):
        self.core_restriction.group_id = self.env["res.groups"].create({"name": "Writers of their companies' tasks"})

        self._uninstall()

        self.assertEqual(self.core_restriction.operation, "cud", "a permission is not widened to reads")

    def test_uninstalling_gives_back_every_operation_to_the_copies_of_core_restriction_only(self):
        other_name, other_domain = "Tasks: in my companies", "[('company_id', 'in', company_ids)]"
        renamed_copy, edited_copy, other_restriction = (
            self.core_restriction.copy(vals)
            for vals in ({"name": other_name}, {"domain": other_domain}, {"name": other_name, "domain": other_domain})
        )

        self._uninstall()

        self.assertEqual((renamed_copy | edited_copy | other_restriction).mapped("operation"), ["crud", "crud", "cud"])

    def test_uninstalling_without_core_row_gives_core_restriction_read_back(self):
        for access_file in ("mail/security/ir.access.csv", "project/security/no_such_file.csv"):
            with self.subTest(access_file), patch.object(hooks, "CORE_TASK_ACCESS_FILE", access_file):
                self.core_restriction.operation = "cud"
                with self.assertLogs(hooks.__name__, "WARNING") as logs:
                    uninstall_hook(self.env)
                self.assertEqual(self.core_restriction.operation, "crud")
                self.assertIn("taken as Odoo ships it", "\n".join(logs.output))

    def test_uninstalling_archives_the_copies_of_the_read_restriction(self):
        customized_copy = self.env["ir.access"].browse(self.read_restriction.customize()["res_id"])
        renamed_copy = customized_copy.copy({"name": "Tasks: own support requests"})
        edited_copy = customized_copy.copy({"domain": "[('company_id', 'in', company_ids + [False])]"})
        other_restriction = edited_copy.copy({"name": "Tasks: read in my companies"})

        self._uninstall()

        self.assertEqual(
            (customized_copy | renamed_copy | edited_copy | other_restriction).mapped("active"),
            [False, False, False, True],
        )

    def test_uninstalling_archives_every_access_whose_domain_calls_the_read_method(self):
        grouped_copy = self.read_restriction.copy(
            {"group_id": self.env["res.groups"].create({"name": "Readers of their own support requests"}).id}
        )
        project_restriction = self.env["ir.access"].create(
            {
                "name": "Projects: of my own support requests",
                "model_id": self.env["ir.model"]._get_id("project.project"),
                "operation": "r",
                "domain": "[('task_ids', 'any', user._support_request_task_read_domain())]",
            }
        )

        self._uninstall()

        self.assertEqual((grouped_copy | project_restriction).mapped("active"), [False, False])

    def test_uninstalling_reads_a_line_break_in_a_field_of_project_file_as_written(self):
        domain = "[\r\n('company_id', 'in', company_ids + [False])\r\n]"
        renamed_copy = self.core_restriction.copy({"name": "Tasks: in my companies", "domain": domain})
        with file_open_temporary_directory(self.env) as access_dir:
            access_file_path = os.path.join(access_dir, "ir.access.csv")
            with open(access_file_path, "w", newline="", encoding="utf-8") as access_file:
                csv.writer(access_file).writerows(
                    [
                        ("id", "name", "operation", "domain"),
                        ("task_comp_rule", "Project/Task: multi-company", "crud", domain),
                    ]
                )
            with patch.object(hooks, "CORE_TASK_ACCESS_FILE", access_file_path):
                uninstall_hook(self.env)

        self.assertEqual(renamed_copy.operation, "crud")
