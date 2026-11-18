from odoo.tests.common import tagged

from .common import TeamDashboardCommon


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

    def test_self_readable_fields(self):
        """Test that dashboard_default_project_id is in SELF_READABLE_FIELDS."""
        self.assertIn(
            "dashboard_default_project_id",
            self.user_dev1.SELF_READABLE_FIELDS,
        )

    def test_self_writeable_fields(self):
        """Test that dashboard_default_project_id is in SELF_WRITEABLE_FIELDS."""
        self.assertIn(
            "dashboard_default_project_id",
            self.user_dev1.SELF_WRITEABLE_FIELDS,
        )

    def test_user_can_write_own_dashboard_project(self):
        """Test that user can write their own dashboard_default_project_id."""
        self.user_dev1.with_user(self.user_dev1).write(
            {"dashboard_default_project_id": self.project.id},
        )
        self.assertEqual(
            self.user_dev1.dashboard_default_project_id,
            self.project,
        )
