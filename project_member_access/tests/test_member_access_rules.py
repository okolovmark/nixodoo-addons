import unittest

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import MODULE, MemberAccessCommon

ALL = (True, True, True, True)
NOTHING = (False, False, False, False)
READ_ONLY = (True, False, False, False)
SETTING_MODELS = ("project.milestone", "project.update")
BURNDOWN = "project.task.burndown.chart.report"


@tagged("post_install", "-at_install")
class TestMemberAccessRules(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = (
            cls.reader
            | cls.contributor
            | cls.editor
            | cls.admin
            | cls.manager
            | cls.sysadmin
            | cls.outsider
            | cls.employee
            | cls.portal
        )

    def _visible(self, model_name, user, records):
        return self.env[model_name].with_user(user).search([("id", "in", records.ids)])

    def test_tasks_of_a_private_project_by_role(self):
        self.assertEqual(
            self._allowed_matrix(self.private_task, self.users),
            {
                "member_access_reader": READ_ONLY,
                "member_access_contributor": (True, False, True, False),
                "member_access_editor": (True, True, True, False),
                "member_access_admin": ALL,
                "member_access_manager": READ_ONLY,
                "member_access_sysadmin": ALL,
                "member_access_outsider": NOTHING,
                "member_access_employee": NOTHING,
                "member_access_portal": NOTHING,
            },
        )

    def test_tasks_of_a_public_project_by_role(self):
        """The project is for its Team Members only, and none of them is one."""
        self.assertEqual(
            self._allowed_matrix(self.public_task, self.users),
            {
                "member_access_reader": READ_ONLY,
                "member_access_contributor": (True, False, True, False),
                "member_access_editor": (True, True, True, False),
                "member_access_admin": ALL,
                "member_access_manager": READ_ONLY,
                "member_access_sysadmin": ALL,
                "member_access_outsider": READ_ONLY,
                "member_access_employee": READ_ONLY,
                "member_access_portal": NOTHING,
            },
        )

    def test_own_tasks_are_written_whatever_the_role(self):
        self.assertEqual(
            self._allowed_matrix(self.private_reader_task, self.reader),
            {"member_access_reader": (True, True, False, False)},
        )
        created_task = self._create_task(self.private_project, self.contributor)
        self.assertTrue(self._allowed(created_task, self.contributor, "write"), "its creator")
        self.private_task.uat_user_ids = self.reader
        self.assertTrue(self._allowed(self.private_task, self.reader, "write"), "a UAT user")

    def test_projects_by_role(self):
        """Core lets Project Administrators alone change a project: a role adds no right
        to it, and an Admin member without the group changes nothing.
        """
        operations = ("read", "write", "unlink")
        expected_by_login = {
            "member_access_reader": (True, False, False),
            "member_access_contributor": (True, False, False),
            "member_access_editor": (True, False, False),
            "member_access_admin": (True, False, False),
            "member_access_manager": (True, False, False),
            "member_access_sysadmin": (True, False, False),
            "member_access_portal": (False, False, False),
        }
        for project, reads_without_role in ((self.private_project, False), (self.public_project, True)):
            outsiders = {
                "member_access_outsider": (reads_without_role, False, False),
                "member_access_employee": (reads_without_role, False, False),
            }
            self.assertEqual(
                self._allowed_matrix(project, self.users, operations),
                {**expected_by_login, **outsiders},
                project.name,
            )

    def test_a_project_is_changed_by_project_administrators_who_are_its_admins(self):
        admin_manager = self._create_user("admin_manager", "project.group_project_manager")
        self.env["project.member"].create(
            {"project_id": self.private_project.id, "user_id": admin_manager.id, "role": "admin"}
        )
        self.assertEqual(
            self._allowed_matrix(self.private_project, admin_manager | self.manager, ("read", "write", "unlink")),
            {"member_access_admin_manager": (True, True, True), "member_access_manager": (True, False, False)},
        )
        self.private_project.with_user(admin_manager).write({"name": "Renamed"})
        self.assertEqual(self.private_project.name, "Renamed")

    def test_members_by_role(self):
        """A Project Administrator passes the create row: create checks who adds the member."""
        reader_row = self.private_project.member_ids.filtered(lambda member: member.user_id == self.reader)
        self.assertEqual(
            self._allowed_matrix(reader_row, self.users),
            {
                "member_access_reader": READ_ONLY,
                "member_access_contributor": READ_ONLY,
                "member_access_editor": READ_ONLY,
                "member_access_admin": ALL,
                "member_access_manager": (True, False, True, False),
                "member_access_sysadmin": ALL,
                "member_access_outsider": NOTHING,
                "member_access_employee": NOTHING,
                "member_access_portal": NOTHING,
            },
        )

    def test_members_of_a_standard_project_are_kept_by_project_administrators(self):
        standard_row = self.env["project.member"].create(
            {"project_id": self.standard_project.id, "user_id": self.outsider.id, "role": "admin"}
        )
        self.assertEqual(
            self._allowed_matrix(standard_row, self.manager | self.outsider | self.editor),
            {"member_access_manager": ALL, "member_access_outsider": ALL, "member_access_editor": READ_ONLY},
        )

    def _create_lines(self, task):
        return (
            self.env["project.task.checklist.item"].create({"name": "Check", "task_id": task.id}),
            self.env["project.task.question"].create({"question": "Which?", "task_id": task.id}),
            self.env["project.task.testcase"].create({"name": "Case", "task_id": task.id}),
            self.env["project.task.session"].create({"session_id": "session-%s" % task.id, "task_id": task.id}),
        )

    def test_lines_of_a_task_follow_its_access(self):
        users = self.reader | self.contributor | self.editor | self.admin | self.manager | self.sysadmin | self.outsider
        for line in self._create_lines(self.private_task):
            self.assertEqual(
                self._allowed_matrix(line, users),
                {
                    "member_access_reader": READ_ONLY,
                    "member_access_contributor": READ_ONLY,
                    "member_access_editor": ALL,
                    "member_access_admin": ALL,
                    "member_access_manager": READ_ONLY,
                    "member_access_sysadmin": ALL,
                    "member_access_outsider": NOTHING,
                },
                line._name,
            )

    def test_lines_of_an_own_task_are_written_whatever_the_role(self):
        for line in self._create_lines(self.private_reader_task):
            self.assertEqual(
                self._allowed_matrix(line, self.reader | self.contributor),
                {"member_access_reader": ALL, "member_access_contributor": READ_ONLY},
                line._name,
            )

    def test_a_line_moves_only_to_a_task_one_may_write(self):
        own_standard_task = self._create_task(self.standard_project, user_ids=[Command.set(self.reader.ids)])
        for source_task in self.private_reader_task | own_standard_task:
            for line in self._create_lines(source_task):
                move_case = "%s from %s" % (line._name, source_task.project_id.name)
                with self.assertRaises(AccessError, msg=move_case):
                    line.with_user(self.reader).write({"task_id": self.private_task.id})
        for line in self._create_lines(self.private_reader_task):
            line.with_user(self.editor).write({"task_id": self.private_task.id})
            found_lines = self.env[line._name].with_user(self.editor).search([("task_id", "=", self.private_task.id)])
            self.assertIn(line, found_lines)

    def test_lines_follow_their_task_into_a_private_project(self):
        parent = self._create_task(self.standard_project)
        sub_task = self._create_task(self.standard_project, parent_id=parent.id)
        lines = self._create_lines(parent) + self._create_lines(sub_task)
        for line in lines:
            self.assertTrue(self._allowed(line, self.outsider, "read"), line._name)
        parent.project_id = self.private_project
        self.assertEqual(sub_task.project_id, self.private_project)
        for line in lines:
            self.assertFalse(self._allowed(line, self.outsider, "read"), line._name)

    def test_project_settings_by_role(self):
        records = (
            self.env["project.milestone"].create({"name": "Go-live", "project_id": self.private_project.id}),
            self.env["project.update"].create(
                {"name": "Week 1", "project_id": self.private_project.id, "status": "on_track"}
            ),
        )
        for record in records:
            self.assertEqual(
                self._allowed_matrix(record, self.users),
                {
                    "member_access_reader": READ_ONLY,
                    "member_access_contributor": READ_ONLY,
                    "member_access_editor": READ_ONLY,
                    "member_access_admin": ALL,
                    "member_access_manager": READ_ONLY,
                    "member_access_sysadmin": ALL,
                    "member_access_outsider": NOTHING,
                    "member_access_employee": NOTHING,
                    "member_access_portal": NOTHING,
                },
                record._name,
            )

    def test_project_settings_are_changed_by_an_admin_only(self):
        milestone = self.env["project.milestone"].create({"name": "Go-live", "project_id": self.private_project.id})
        with self.assertRaises(AccessError):
            self.env["project.update"].with_user(self.outsider).create(
                {"name": "Planted", "project_id": self.private_project.id, "status": "off_track"}
            )
        for user in self.outsider | self.editor:
            with self.assertRaises(AccessError):
                milestone.with_user(user).write({"name": "Moved"})
            with self.assertRaises(AccessError):
                milestone.with_user(user).unlink()
        milestone.with_user(self.admin).write({"name": "Moved"})
        milestone.with_user(self.admin).unlink()
        self.assertFalse(milestone.exists())

    def test_project_settings_move_into_a_project_by_its_admins_only(self):
        """The write restriction reads a milestone or an update before the write, in the
        project it leaves: moved, it is checked again in the project it enters.
        """
        values_by_model = {
            "project.milestone": {"name": "Go-live"},
            "project.update": {"name": "Week 1", "status": "on_track"},
        }
        for model_name, values in values_by_model.items():
            for project in self.public_project | self.private_project:
                for user in self.outsider | self.reader:
                    with self.subTest(model=model_name, project=project.name, user=user.login):
                        record = self.env[model_name].create({**values, "project_id": self.standard_project.id})
                        record.with_user(user).write({"name": "Renamed"})
                        with self.assertRaises(AccessError):
                            record.with_user(user).write({"project_id": project.id})
                record = self.env[model_name].create({**values, "project_id": self.standard_project.id})
                record.with_user(self.admin).write({"project_id": project.id})
                self.assertEqual(record.project_id, project)

    def test_an_admin_keeps_the_settings_of_a_project_they_are_no_team_member_of(self):
        self.assertNotIn(self.admin, self.public_project.allowed_internal_user_ids)
        milestone = (
            self.env["project.milestone"]
            .with_user(self.admin)
            .create({"name": "Go-live", "project_id": self.public_project.id})
        )
        milestone.with_user(self.admin).write({"name": "Moved"})
        self.assertEqual(milestone.name, "Moved")

    def test_pull_requests_are_read_through_one_of_their_tasks(self):
        archived_task = self._create_task(self.standard_project)
        archived_task.active = False
        loose_task = self.env["project.task"].create({"name": "Without project"})
        tasks_by_name = {
            "without task": self.env["project.task"],
            "private": self.private_task,
            "private and standard": self.private_task | self.standard_task,
            "private and without project": self.private_task | loose_task,
            "archived standard": archived_task,
        }
        pull_requests = {
            name: self.env["project.pull.request"].create(
                {"url": "https://example.com/org/repo/pull/%s" % number, "task_ids": [Command.set(tasks.ids)]}
            )
            for number, (name, tasks) in enumerate(tasks_by_name.items(), start=1)
        }
        for user, unread_names in ((self.outsider, {"private"}), (self.reader, set()), (self.manager, set())):
            read_names = {name for name, record in pull_requests.items() if self._allowed(record, user, "read")}
            self.assertEqual(read_names, set(pull_requests) - unread_names, user.login)

    def test_the_link_of_a_hidden_pull_request_is_linked_to_an_own_task(self):
        url = "https://example.com/org/repo/pull/1"
        pull_request = self.env["project.pull.request"].create(
            {"url": url, "task_ids": [Command.set(self.private_task.ids)]}
        )
        self.assertFalse(self._allowed(pull_request, self.outsider, "read"))
        own_task = self._create_task(self.standard_project, self.outsider)
        own_task.pr_uri = url
        self.assertEqual(own_task.sudo().pull_request_ids, pull_request)
        self.assertTrue(self._allowed(pull_request, self.outsider, "read"))

    def test_a_private_project_is_invisible_to_non_members(self):
        private_tasks = self.private_task | self.private_reader_task
        for user in self.outsider | self.employee:
            self.assertFalse(self._visible("project.project", user, self.private_project), user.login)
            self.assertFalse(self._visible("project.task", user, private_tasks), user.login)
            # in place: Odoo's assertRaises clears the access cache first
            with unittest.TestCase.assertRaises(self, AccessError):
                self.private_task.with_user(user).read(["name"])
        for user in self.reader | self.manager | self.sysadmin:
            self.assertEqual(self._visible("project.task", user, private_tasks), private_tasks, user.login)

    def test_a_portal_user_reads_nothing_inside(self):
        """Even one invited to the project: following and sharing open nothing inside."""
        for project in self.public_project | self.private_project:
            project.sudo().write({"privacy_visibility": "portal"})
            project._add_collaborators(self.portal.partner_id)
        for model_name, records in (
            ("project.project", self.public_project | self.private_project),
            ("project.task", self.public_task | self.private_task),
        ):
            self.assertFalse(self._visible(model_name, self.portal, records), model_name)
        self.standard_project.write({"privacy_visibility": "portal"})
        self.standard_project._add_collaborators(self.portal.partner_id)
        self.assertEqual(self._visible("project.task", self.portal, self.standard_task), self.standard_task)

    def test_followers_get_nothing_inside(self):
        self.private_project.message_subscribe(self.outsider.partner_id.ids)
        self.private_task.message_subscribe(self.outsider.partner_id.ids)
        self.assertFalse(self._visible("project.task", self.outsider, self.private_task))
        self.assertFalse(self._visible("project.project", self.outsider, self.private_project))
        # the Visibility under which core opens a task to a portal follower
        (self.public_project | self.standard_project).privacy_visibility = "portal"
        (self.public_task | self.standard_task).message_subscribe(self.portal.partner_id.ids)
        self.assertFalse(self._visible("project.task", self.portal, self.public_task))
        self.assertEqual(self._visible("project.task", self.portal, self.standard_task), self.standard_task)

    def test_team_members_get_nothing_inside(self):
        self.private_project.write({"privacy_visibility": "followers", "allowed_internal_user_ids": self.outsider})
        self.assertFalse(self._visible("project.project", self.outsider, self.private_project))
        self.assertFalse(self._visible("project.task", self.outsider, self.private_task))

    def test_a_public_project_is_read_by_every_internal_user(self):
        for user in self.outsider | self.employee:
            self.assertEqual(self._visible("project.task", user, self.public_task), self.public_task, user.login)
            self.assertEqual(self._visible("project.project", user, self.public_project), self.public_project)

    def test_project_reports_follow_the_project(self):
        self.env["project.milestone"].create({"name": "Go-live", "project_id": self.private_project.id})
        self.env["project.update"].create(
            {"name": "Week 1", "project_id": self.private_project.id, "status": "on_track"}
        )
        self.env.flush_all()
        for model_name in (BURNDOWN, *SETTING_MODELS):
            rows_by_login = {
                user.login: self._count_rows(model_name, user, self.private_project)
                for user in self.reader | self.manager | self.outsider
            }
            self.assertTrue(rows_by_login["member_access_reader"], model_name)
            self.assertEqual(rows_by_login["member_access_manager"], rows_by_login["member_access_reader"], model_name)
            self.assertFalse(rows_by_login["member_access_outsider"], model_name)

    def test_reports_and_settings_of_a_public_project_are_read_by_every_internal_user(self):
        """The project is for its Team Members only: the permissions of core alone would hide them."""
        self.env["project.milestone"].create({"name": "Go-live", "project_id": self.public_project.id})
        self.env["project.update"].create(
            {"name": "Week 1", "project_id": self.public_project.id, "status": "on_track"}
        )
        self.env.flush_all()
        self.assertTrue(self._count_rows(BURNDOWN, self.outsider, self.public_project))
        for model_name in SETTING_MODELS:
            for user in self.outsider | self.employee:
                self.assertTrue(self._count_rows(model_name, user, self.public_project), (model_name, user.login))

    def _count_rows(self, model_name, user, project):
        Model = self.env[model_name].with_user(user)
        domain = [("project_id", "=", project.id)]
        if model_name != BURNDOWN:
            return Model.search_count(domain)
        # no table behind it: the chart is only read grouped by date and stage
        groups = Model._read_group(domain, ["date:month", "stage_id"], ["__count"])
        return sum(count for *_groupby, count in groups)

    def test_real_operations_follow_the_role(self):
        with self.assertRaises(AccessError):
            self._create_task(self.private_project, self.reader)
        with self.assertRaises(AccessError):
            self.private_task.with_user(self.contributor).write({"name": "Not theirs"})
        self.public_task.with_user(self.editor).write({"name": "Edited"})
        with self.assertRaises(AccessError):
            self.public_task.with_user(self.editor).unlink()
        self.public_task.with_user(self.admin).unlink()
        self.assertFalse(self.public_task.exists())

    def _module_restriction_and_grant_ids(self):
        return (
            self.env["ir.model.data"]
            .search([("module", "=", MODULE), ("model", "=", "ir.access"), ("name", "like", "%_rule_member_access_%")])
            .mapped("res_id")
        )

    def test_standard_projects_are_left_to_core(self):
        """Member rows of a Standard project are inert: the outsider is its Admin and
        still does not see a Standard project for its Team Members only.
        """
        hidden_project = self.env["project.project"].create(
            {
                "name": "Standard, Team Members only",
                "privacy_visibility": "followers",
                "member_ids": [Command.create({"user_id": self.outsider.id, "role": "admin"})],
            }
        )
        hidden_task = self._create_task(hidden_project)
        private_task = self.env["project.task"].with_user(self.outsider).create({"name": "No project"})
        followed_task = self._create_task(hidden_project)
        followed_task.message_subscribe(self.outsider.partner_id.ids)
        records = (
            self.standard_project | hidden_project,
            self.standard_task | hidden_task | private_task | followed_task,
        )

        def matrices():
            return [self._allowed_matrix(record, self.users) for recordset in records for record in recordset]

        with_module_rows = matrices()
        self.env["ir.access"].browse(self._module_restriction_and_grant_ids()).active = False
        self.assertEqual(with_module_rows, matrices())
        self.assertFalse(with_module_rows[1]["member_access_outsider"][0], "the Team Members only project stays hidden")
        self.assertTrue(with_module_rows[-1]["member_access_outsider"][0], "a follower reads the task")

    def test_standard_lines_settings_and_reports_are_left_to_core(self):
        hidden_project = self.env["project.project"].create(
            {"name": "Standard, Team Members only", "privacy_visibility": "followers"}
        )
        projects = self.standard_project | hidden_project
        records = [self.env["project.pull.request"].create({"url": "https://example.com/org/repo/pull/9"})]
        for project in projects:
            task = self._create_task(project)
            records += [
                *self._create_lines(task),
                self.env["project.pull.request"].create(
                    {"url": "https://example.com/org/repo/pull/%s" % task.id, "task_ids": [Command.set(task.ids)]}
                ),
                self.env["project.milestone"].create({"name": "Go-live", "project_id": project.id}),
                self.env["project.update"].create({"name": "Week 1", "project_id": project.id, "status": "on_track"}),
            ]
        self.env.flush_all()
        project_users = self.users - self.employee - self.portal

        def access():
            report_rows = {
                (user.login, project.name): self._count_rows(BURNDOWN, user, project)
                for user in project_users
                for project in projects
            }
            return [self._allowed_matrix(record, self.users) for record in records], report_rows

        with_module_rows = access()
        self.env["ir.access"].browse(self._module_restriction_and_grant_ids()).active = False
        self.assertEqual(with_module_rows, access())
