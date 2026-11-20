from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import tagged

from .common import MemberAccessCommon

ALL = (True, True, True, True)
NOTHING = (False, False, False, False)
READ_ONLY = (True, False, False, False)
REPORT_MODELS = ("report.project.task.user", "project.task.burndown.chart.report")
SETTING_MODELS = ("project.milestone", "project.update")


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
        """The project is followers-only and none of them follows it."""
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
        operations = ("read", "write", "unlink")
        expected_by_login = {
            "member_access_reader": (True, False, False),
            "member_access_contributor": (True, False, False),
            "member_access_editor": (True, False, False),
            "member_access_admin": (True, True, True),
            "member_access_manager": (True, False, False),
            "member_access_sysadmin": (True, True, True),
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

    def test_members_by_role(self):
        reader_row = self.private_project.member_ids.filtered(lambda member: member.user_id == self.reader)
        self.assertEqual(
            self._allowed_matrix(reader_row, self.users),
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
        )

    def test_members_of_a_standard_project_are_kept_by_project_administrators(self):
        standard_row = self.env["project.member"].create(
            {"project_id": self.standard_project.id, "user_id": self.outsider.id, "role": "admin"}
        )
        self.assertEqual(
            self._allowed_matrix(standard_row, self.manager | self.outsider | self.editor),
            {"member_access_manager": ALL, "member_access_outsider": ALL, "member_access_editor": READ_ONLY},
        )

    def test_lines_of_a_task_follow_its_access(self):
        lines = (
            self.env["project.task.checklist.item"].create(
                {"name": "Check", "task_id": self.private_task.id, "user_id": self.admin.id}
            ),
            self.env["project.task.question"].create({"question": "Which?", "task_id": self.private_task.id}),
            self.env["project.task.testcase"].create({"name": "Case", "task_id": self.private_task.id}),
            self.env["project.task.session"].create({"session_id": "session-1", "task_id": self.private_task.id}),
        )
        users = self.reader | self.contributor | self.editor | self.admin | self.manager | self.sysadmin | self.outsider
        for line in lines:
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

    def _create_lines(self, task):
        return (
            self.env["project.task.checklist.item"].create({"name": "Check", "task_id": task.id}),
            self.env["project.task.question"].create({"question": "Which?", "task_id": task.id}),
            self.env["project.task.testcase"].create({"name": "Case", "task_id": task.id}),
            self.env["project.task.session"].create({"session_id": "session-1", "task_id": task.id}),
        )

    def _assert_found_on_task(self, line, user, task):
        found_lines = self.env[line._name].with_user(user).search([("id", "=", line.id), ("task_id", "=", task.id)])
        self.assertEqual(found_lines, line, line._name)

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
            self._assert_found_on_task(line, self.editor, self.private_task)

    def test_a_line_moves_between_standard_tasks_as_before(self):
        other_standard_task = self._create_task(self.standard_project)
        for line in self._create_lines(self.standard_task):
            line.with_user(self.outsider).write({"task_id": other_standard_task.id})
            self._assert_found_on_task(line, self.outsider, other_standard_task)

    def test_a_line_moves_only_to_a_task_one_may_read(self):
        """ADR 0007: whoever can read its task writes it, in a Standard project too."""
        hidden_project = self.env["project.project"].create(
            {"name": "Standard, followers-only", "privacy_visibility": "followers"}
        )
        hidden_task = self._create_task(hidden_project)
        for line in self._create_lines(self.standard_task):
            with self.assertRaises(AccessError, msg=line._name):
                line.with_user(self.outsider).write({"task_id": hidden_task.id})

    def test_a_line_takes_the_project_of_its_task(self):
        for line in self._create_lines(self.private_task):
            with self.assertRefused(UserError, "the project of their task"):
                line.with_user(self.editor).write({"project_id": self.standard_project.id})
            line.with_user(self.editor).write({"project_id": self.private_project.id})

    def test_a_line_is_created_in_the_project_of_its_task(self):
        """The reader reads the private task without being on it or its Editor: only a
        stored Standard project would let them write a line of it. A session given with
        a task and session id that already have a row writes that row.
        """
        logged_session = self.env["project.task.session"].create(
            {"session_id": "session-1", "task_id": self.private_task.id}
        )
        foreign_project_vals = {"task_id": self.private_task.id, "project_id": self.standard_project.id}
        for model_name, line_vals in (
            ("project.task.checklist.item", {"name": "Check"}),
            ("project.task.question", {"question": "Which?"}),
            ("project.task.testcase", {"name": "Case"}),
            ("project.task.session", {"session_id": "session-2"}),
        ):
            line = self.env[model_name].with_user(self.editor).create(dict(foreign_project_vals, **line_vals))
            self.assertFalse(self._allowed(line, self.reader, "write"), model_name)
        session_vals = dict(foreign_project_vals, session_id="session-1", outcome="Reviewed")
        updated_session = self.env["project.task.session"].with_user(self.editor).create(session_vals)
        self.assertEqual(updated_session, logged_session)
        self.assertEqual(updated_session.outcome, "Reviewed")
        self.assertFalse(self._allowed(updated_session, self.reader, "write"), "the logged session")

    def test_lines_follow_their_task_into_a_private_project(self):
        parent = self._create_task(self.standard_project)
        sub_task = self._create_task(self.standard_project, parent_id=parent.id)
        lines = self._create_lines(parent) + self._create_lines(sub_task)
        for line in lines:
            self.assertTrue(self._allowed(line, self.outsider, "read"), line._name)
        parent.project_id = self.private_project
        for line in lines:
            self.assertEqual(line.project_id, self.private_project, line._name)
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
        last_update = self.private_project.last_update_id
        with self.assertRaises(AccessError):
            self.env["project.update"].with_user(self.outsider).create(
                {"name": "Planted", "project_id": self.private_project.id, "status": "off_track"}
            )
        self.assertEqual(self.private_project.last_update_id, last_update)
        for user in self.outsider | self.editor:
            with self.assertRaises(AccessError):
                milestone.with_user(user).write({"name": "Moved"})
            with self.assertRaises(AccessError):
                milestone.with_user(user).unlink()
        milestone.with_user(self.admin).write({"name": "Moved"})
        milestone.with_user(self.admin).unlink()
        self.assertFalse(milestone.exists())

    def test_project_settings_move_into_a_project_by_its_admins_only(self):
        """The write rules read a milestone or an update before the write, in the
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

    def test_an_admin_keeps_the_settings_of_a_followers_only_project_they_do_not_follow(self):
        self.assertNotIn(self.admin.partner_id, self.public_project.message_partner_ids)
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
        for user in self.outsider | self.employee | self.portal:
            self.assertFalse(self._visible("project.project", user, self.private_project), user.login)
            self.assertFalse(self._visible("project.task", user, private_tasks), user.login)
            with self.assertRaises(AccessError):
                self.private_task.with_user(user).read(["name"])
        for user in self.reader | self.manager | self.sysadmin:
            self.assertEqual(self._visible("project.task", user, private_tasks), private_tasks, user.login)

    def test_followers_get_nothing_inside(self):
        self.private_project.message_subscribe(self.outsider.partner_id.ids)
        self.private_task.message_subscribe(self.outsider.partner_id.ids)
        self.public_task.message_subscribe(self.portal.partner_id.ids)
        self.assertFalse(self._visible("project.task", self.outsider, self.private_task))
        self.assertFalse(self._visible("project.project", self.outsider, self.private_project))
        self.assertFalse(self._visible("project.task", self.portal, self.public_task))

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
        for model_name in (
            "report.project.task.user",
            "project.task.burndown.chart.report",
            "project.milestone",
            "project.update",
        ):
            rows_by_login = {
                user.login: self._count_private_rows(model_name, user)
                for user in self.reader | self.manager | self.outsider
            }
            self.assertTrue(rows_by_login["member_access_reader"], model_name)
            self.assertEqual(rows_by_login["member_access_manager"], rows_by_login["member_access_reader"], model_name)
            self.assertFalse(rows_by_login["member_access_outsider"], model_name)

    def test_reports_and_settings_of_a_public_project_are_read_by_every_internal_user(self):
        """The project is followers-only: the rules of other modules alone would hide them."""
        self.env["project.milestone"].create({"name": "Go-live", "project_id": self.public_project.id})
        self.env["project.update"].create(
            {"name": "Week 1", "project_id": self.public_project.id, "status": "on_track"}
        )
        self.env.flush_all()
        for model_name in REPORT_MODELS:
            self.assertTrue(self._count_rows(model_name, self.outsider, self.public_project), model_name)
        for model_name in SETTING_MODELS:
            for user in self.outsider | self.employee:
                self.assertTrue(self._count_rows(model_name, user, self.public_project), (model_name, user.login))

    def _count_private_rows(self, model_name, user):
        return self._count_rows(model_name, user, self.private_project)

    def _count_rows(self, model_name, user, project):
        Model = self.env[model_name].with_user(user)
        domain = [("project_id", "=", project.id)]
        if model_name != "project.task.burndown.chart.report":
            return Model.search_count(domain)
        # no table behind it: the chart is only read grouped by date and stage
        groups = Model.read_group(domain, ["project_id"], ["date:month", "stage_id"], lazy=False)
        return sum(group["__count"] for group in groups)

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

    def test_a_session_is_logged_on_a_task_by_whoever_may_write_it(self):
        Session = self.env["project.task.session"]
        session_vals = {"session_id": "session-1", "task_id": self.private_task.id, "outcome": "Reviewed"}
        with self.assertRaises(AccessError):
            Session.with_user(self.reader).create(session_vals)
        Session.with_user(self.editor).create(session_vals)
        with self.assertRaises(AccessError, msg="the row of a logged session is written, not created"):
            Session.with_user(self.reader).create(dict(session_vals, outcome="Read it"))
        own_session = Session.with_user(self.reader).create(
            {"session_id": "session-2", "task_id": self.private_reader_task.id}
        )
        self.assertEqual(own_session.user_id, self.reader)

    def test_standard_projects_are_left_to_the_other_rules(self):
        """Member rows of a Standard project are inert: the outsider is its Admin and
        still does not see a followers-only Standard project.
        """
        hidden_project = self.env["project.project"].create(
            {
                "name": "Standard, followers-only",
                "privacy_visibility": "followers",
                "member_ids": [Command.create({"user_id": self.outsider.id, "role": "admin"})],
            }
        )
        hidden_task = self._create_task(hidden_project)
        private_task = self.env["project.task"].with_user(self.outsider).create({"name": "No project"})
        records = (
            self.standard_project | hidden_project,
            self.standard_task | hidden_task | private_task,
        )

        def matrices():
            return [self._allowed_matrix(record, self.users) for recordset in records for record in recordset]

        with_module_rules = matrices()
        self.env["ir.rule"].search([("id", "in", self._module_rule_ids())]).active = False
        self.assertEqual(with_module_rules, matrices())
        self.assertFalse(with_module_rules[1]["member_access_outsider"][0], "the follower-only project stays hidden")

    def test_standard_lines_settings_and_reports_are_left_to_the_other_rules(self):
        hidden_project = self.env["project.project"].create(
            {"name": "Standard, followers-only", "privacy_visibility": "followers"}
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
                (model_name, user.login, project.name): self._count_rows(model_name, user, project)
                for model_name in REPORT_MODELS
                for user in project_users
                for project in projects
            }
            return [self._allowed_matrix(record, self.users) for record in records], report_rows

        with_module_rules = access()
        self.env["ir.rule"].search([("id", "in", self._module_rule_ids())]).active = False
        self.assertEqual(with_module_rules, access())

    def _module_rule_ids(self):
        return (
            self.env["ir.model.data"]
            .search([("module", "=", "project_member_access"), ("model", "=", "ir.rule")])
            .mapped("res_id")
        )
