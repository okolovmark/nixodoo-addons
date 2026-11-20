from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import TeamDashboardCommon

DASHBOARD_USER_FIELDS = ("dashboard_default_project_id", "gantt_columns")


@tagged("post_install", "-at_install")
class TestProjectTask(TeamDashboardCommon):
    def test_action_open_backlog(self):
        """Test action_open_backlog returns correct action dict."""
        self.env.user.dashboard_default_project_id = self.project
        action = self.env["project.task"].action_open_backlog()
        self.assertEqual(action["res_model"], "project.task")
        self.assertEqual(
            action["context"]["search_default_project_id"],
            self.project.id,
        )

    def test_action_open_backlog_no_default_project(self):
        """Test action_open_backlog when user has no default project."""
        self.env.user.dashboard_default_project_id = False
        action = self.env["project.task"].action_open_backlog()
        self.assertFalse(action["context"]["search_default_project_id"])

    def test_the_backlog_preselects_the_default_project_only_where_the_user_finds_it(self):
        company_a = self.env.company
        company_b = self.env["res.company"].create({"name": "Backlog Company B"})
        project_b = self.env["project.project"].create({"name": "Project in B", "company_id": company_b.id})
        project_hidden = self.env["project.project"].create({"name": "Members Only", "privacy_visibility": "followers"})
        project_archived = self.env["project.project"].create({"name": "Archived", "active": False})
        self.user_dev1.company_ids = [Command.link(company_b.id)]
        Task = self.env["project.task"].with_user(self.user_dev1)
        self.assertTrue(project_archived.with_user(self.user_dev1).has_access("read"))
        for default_project, companies, preselected_project_id in (
            (project_b, company_a | company_b, project_b.id),
            (project_b, company_a, False),
            (project_hidden, company_a | company_b, False),
            (project_archived, company_a | company_b, False),
        ):
            self.user_dev1.dashboard_default_project_id = default_project
            with self.subTest(project=default_project.name, companies=companies.mapped("name")):
                action = Task.with_context(allowed_company_ids=companies.ids).action_open_backlog()
                self.assertEqual(action["context"]["search_default_project_id"], preselected_project_id)

    def test_the_backlog_leaves_done_and_cancelled_tasks_out(self):
        open_task, done_task, cancelled_task = self.env["project.task"].create(
            [
                {"name": name, "project_id": self.project.id, "stage_id": self.stage_backlog.id, "state": state}
                for name, state in (("Open", "01_in_progress"), ("Done", "1_done"), ("Cancelled", "1_canceled"))
            ]
        )
        self.assertTrue(done_task.is_closed and cancelled_task.is_closed)
        self.assertEqual(done_task.stage_id, self.stage_backlog, "closing a task leaves its stage")

        backlog_domain = safe_eval(self.env["project.task"].action_open_backlog()["domain"])
        backlog_tasks = self.env["project.task"].search(backlog_domain)

        self.assertIn(open_task, backlog_tasks)
        self.assertFalse(backlog_tasks & (done_task | cancelled_task))

    def test_the_backlog_menu_runs_the_backlog_action_of_the_user(self):
        self.user_dev1.dashboard_default_project_id = self.project
        menu_action = self.env.ref("project_team_dashboard.menu_ai_driven_project_backlog").action

        action = menu_action.with_user(self.user_dev1).run()

        self.assertEqual(action["id"], self.env.ref("project_team_dashboard.action_project_task_backlog").id)
        self.assertEqual(action["context"]["search_default_project_id"], self.project.id)


@tagged("post_install", "-at_install")
class TestProjectTaskType(TeamDashboardCommon):
    def test_stage_is_in_progress(self):
        """Test is_in_progress field on task stage."""
        self.assertTrue(self.stage_in_progress.is_in_progress)
        self.assertFalse(self.stage_backlog.is_in_progress)
        self.assertFalse(self.stage_done.is_in_progress)

    def test_stage_is_backlog(self):
        """Test is_backlog field on task stage."""
        self.assertTrue(self.stage_backlog.is_backlog)
        self.assertFalse(self.stage_in_progress.is_backlog)
        self.assertFalse(self.stage_done.is_backlog)

    def test_stage_both_flags_independent(self):
        """Test that is_in_progress and is_backlog are independent flags."""
        stage = self.env["project.task.type"].create(
            {
                "name": "Both Flags",
                "is_in_progress": True,
                "is_backlog": True,
            },
        )
        self.assertTrue(stage.is_in_progress)
        self.assertTrue(stage.is_backlog)


@tagged("post_install", "-at_install")
class TestResUsers(TeamDashboardCommon):
    def test_dashboard_default_project_field(self):
        """Test dashboard_default_project_id field can be set."""
        self.user_dev1.dashboard_default_project_id = self.project
        self.assertEqual(self.user_dev1.dashboard_default_project_id, self.project)

    def test_the_dashboard_fields_are_writeable_by_their_user(self):
        users = self.env["res.users"].with_user(self.user_dev1)
        for field_name in DASHBOARD_USER_FIELDS:
            self.assertTrue(users._has_field_access(users._fields[field_name], "write"), field_name)

    def test_user_can_write_own_dashboard_project(self):
        """Test that user can write their own dashboard_default_project_id."""
        self.user_dev1.with_user(self.user_dev1).write(
            {"dashboard_default_project_id": self.project.id, "gantt_columns": "stage"},
        )
        self.assertEqual(
            self.user_dev1.dashboard_default_project_id,
            self.project,
        )
        self.assertEqual(self.user_dev1.gantt_columns, "stage")

    def test_a_user_reads_own_dashboard_fields(self):
        self.user_dev1.dashboard_default_project_id = self.project

        values = self.user_dev1.with_user(self.user_dev1).read(list(DASHBOARD_USER_FIELDS))[0]

        self.assertEqual(values["dashboard_default_project_id"][0], self.project.id)
        self.assertEqual(values["gantt_columns"], self.user_dev1.gantt_columns)

    def test_a_user_cannot_write_the_dashboard_fields_of_another_user(self):
        for field_name, value in (("dashboard_default_project_id", self.project.id), ("gantt_columns", "stage")):
            with self.assertRaises(AccessError):
                self.user_dev2.with_user(self.user_dev1).write({field_name: value})

    def test_the_preferences_show_the_default_dashboard_project(self):
        preferences = self.env.ref("base.view_users_form_simple_modif")
        arch = self.env["res.users"].with_user(self.user_dev1).get_views([(preferences.id, "form")])

        form = etree.fromstring(arch["views"]["form"]["arch"])
        self.assertTrue(form.xpath("//field[@name='dashboard_default_project_id']"))
