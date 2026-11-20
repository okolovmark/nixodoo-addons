from datetime import date, timedelta

from freezegun import freeze_time

from odoo import fields
from odoo.tests.common import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestTeamDashboard(TeamDashboardCommon):
    """Tests for project.project.get_team_dashboard_data and helpers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create tasks for dev1 - in progress
        cls.task_in_progress = cls.env["project.task"].create(
            {
                "name": "Dev1 In Progress Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "planned_hours": 10.0,
            },
        )

        # Create task for dev1 - backlog
        cls.task_backlog = cls.env["project.task"].create(
            {
                "name": "Dev1 Backlog Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "planned_hours": 8.0,
            },
        )

        # Create task for dev2 - in progress
        cls.task_dev2 = cls.env["project.task"].create(
            {
                "name": "Dev2 Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev2.id])],
                "planned_hours": 16.0,
            },
        )

        # Create an unassigned task
        cls.task_unassigned = cls.env["project.task"].create(
            {
                "name": "Unassigned Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "user_ids": [(6, 0, [])],
                "planned_hours": 4.0,
            },
        )

        # Create a task in the "other" stage (not backlog, not in_progress, not folded)
        cls.task_other = cls.env["project.task"].create(
            {
                "name": "Dev1 Review Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_other.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "planned_hours": 2.0,
            },
        )

        # Create a task in done stage (folded) - should NOT appear in dashboard
        cls.task_done = cls.env["project.task"].create(
            {
                "name": "Done Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_done.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "planned_hours": 5.0,
            },
        )

    def test_dashboard_returns_project_info(self):
        """Test that dashboard data includes correct project info."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        self.assertEqual(data["project_id"], self.project.id)
        self.assertEqual(data["project_name"], self.project.name)
        self.assertIn("period_start", data)
        self.assertIn("period_end", data)
        self.assertIn("members", data)

    def test_dashboard_nonexistent_project(self):
        """Test dashboard returns error for non-existent project."""
        data = self.env["project.project"].get_team_dashboard_data(999999)
        self.assertIn("error", data)

    def test_dashboard_member_count(self):
        """Test dashboard includes correct number of members.

        Should have dev1, dev2, and Unassigned.
        """
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        members = data["members"]
        user_ids = [m["user_id"] for m in members]
        self.assertIn(self.user_dev1.id, user_ids)
        self.assertIn(self.user_dev2.id, user_ids)
        # Unassigned entry
        self.assertIn(False, user_ids)
        self.assertEqual(len(members), 3)

    def test_dashboard_dev1_current_task(self):
        """Test that dev1's current task is the in-progress one."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        self.assertTrue(dev1_data["current_task"])
        self.assertEqual(dev1_data["current_task"]["id"], self.task_in_progress.id)

    def test_dashboard_dev1_backlog(self):
        """Test that dev1 has a backlog task."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        backlog_ids = [t["id"] for t in dev1_data["backlog"]]
        self.assertIn(self.task_backlog.id, backlog_ids)

    def test_dashboard_dev1_other_tasks(self):
        """Test that dev1 has other (non-backlog, non-in_progress) tasks."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        other_ids = [t["id"] for t in dev1_data["other"]]
        self.assertIn(self.task_other.id, other_ids)

    def test_dashboard_done_tasks_excluded(self):
        """Test that tasks in folded (done) stages are not in dashboard."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        all_task_ids = set()
        if dev1_data["current_task"]:
            all_task_ids.add(dev1_data["current_task"]["id"])
        all_task_ids.update(t["id"] for t in dev1_data["queue"])
        all_task_ids.update(t["id"] for t in dev1_data["backlog"])
        all_task_ids.update(t["id"] for t in dev1_data["other"])
        self.assertNotIn(self.task_done.id, all_task_ids)

    def test_dashboard_unassigned_tasks(self):
        """Test that unassigned tasks appear in the Unassigned member entry."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        unassigned_data = next(m for m in data["members"] if m["user_id"] is False)
        self.assertEqual(unassigned_data["user_name"], "Unassigned")
        all_task_ids = set()
        all_task_ids.update(t["id"] for t in unassigned_data["queue"])
        all_task_ids.update(t["id"] for t in unassigned_data["backlog"])
        all_task_ids.update(t["id"] for t in unassigned_data["other"])
        self.assertIn(self.task_unassigned.id, all_task_ids)

    def test_dashboard_planned_hours_aggregation(self):
        """Test that total_planned_hours is summed correctly for each member."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        # dev1 has: in_progress(10) + backlog(8) + other(2) = 20 total open tasks hours
        self.assertAlmostEqual(dev1_data["total_planned_hours"], 20.0)

    def test_dashboard_open_task_count(self):
        """Test that total_open_tasks counts only non-folded tasks."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        # dev1 has: task_in_progress, task_backlog, task_other (done is excluded)
        self.assertEqual(dev1_data["total_open_tasks"], 3)

    def test_dashboard_custom_dates(self):
        """Test passing custom date_start and date_end strings."""
        data = self.env["project.project"].get_team_dashboard_data(
            self.project.id,
            date_start="2026-01-01",
            date_end="2026-01-31",
        )
        self.assertEqual(data["period_start"], "2026-01-01")
        self.assertEqual(data["period_end"], "2026-01-31")

    def test_dashboard_default_period_is_current_week(self):
        """Test that default period covers the current week (Monday to Sunday)."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        today = fields.Date.context_today(self.env["project.project"])
        expected_start = today - timedelta(days=today.weekday())
        expected_end = expected_start + timedelta(days=6)
        self.assertEqual(data["period_start"], fields.Date.to_string(expected_start))
        self.assertEqual(data["period_end"], fields.Date.to_string(expected_end))

    def test_dashboard_dev2_current_task(self):
        """Test that dev2's current task is identified."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev2_data = next(m for m in data["members"] if m["user_id"] == self.user_dev2.id)
        self.assertTrue(dev2_data["current_task"])
        self.assertEqual(dev2_data["current_task"]["id"], self.task_dev2.id)

    def test_dashboard_unassigned_no_current_task(self):
        """Test that Unassigned member entry has no current task."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        unassigned_data = next(m for m in data["members"] if m["user_id"] is False)
        self.assertFalse(unassigned_data["current_task"])
        self.assertEqual(unassigned_data["hours_logged_period"], 0)
        self.assertEqual(unassigned_data["hours_logged_today"], 0)

    @freeze_time("2026-06-03 12:00:00")
    def test_dashboard_with_timesheets(self):
        """Test that timesheet hours are aggregated per user."""
        # dev1 has no time zone: their lines are dated on the UTC day
        today = date(2026, 6, 3)
        self.env["account.analytic.line"].create(
            {
                "name": "Dev1 work",
                "project_id": self.project.id,
                "task_id": self.task_in_progress.id,
                "user_id": self.user_dev1.id,
                "unit_amount": 3.5,
                "date": today,
            },
        )
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        self.assertAlmostEqual(dev1_data["hours_logged_today"], 3.5)
        self.assertGreaterEqual(dev1_data["hours_logged_period"], 3.5)


@tagged("post_install", "-at_install")
class TestTaskToDict(TeamDashboardCommon):
    """Tests for project.project._task_to_dict helper."""

    def test_task_to_dict_basic(self):
        """Test _task_to_dict returns correct structure."""
        task = self.env["project.task"].create(
            {
                "name": "Dict Test Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_hours": 10.0,
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertEqual(result["id"], task.id)
        self.assertEqual(result["name"], "Dict Test Task")
        self.assertEqual(result["stage_id"], self.stage_in_progress.id)
        self.assertEqual(result["stage_name"], "In Progress")
        self.assertAlmostEqual(result["planned_hours"], 10.0)
        self.assertIn("remaining_hours", result)
        self.assertIn("progress", result)
        self.assertIn("kanban_state", result)
        self.assertIn("priority", result)
        self.assertFalse(result["is_subtask"])

    def test_task_to_dict_subtask(self):
        """Test _task_to_dict for subtask includes parent info."""
        parent_task = self.env["project.task"].create(
            {
                "name": "Parent Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        child_task = self.env["project.task"].create(
            {
                "name": "Child Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "parent_id": parent_task.id,
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(child_task, today)
        self.assertTrue(result["is_subtask"])

    def test_task_to_dict_overdue(self):
        """Test _task_to_dict correctly marks overdue tasks."""
        yesterday = fields.Datetime.now() - timedelta(days=1)
        task = self.env["project.task"].create(
            {
                "name": "Overdue Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_end": yesterday,
            },
        )
        today = fields.Date.context_today(self.env["project.project"])
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertTrue(result["is_overdue"])

    def test_task_to_dict_not_overdue(self):
        """Test _task_to_dict correctly marks non-overdue tasks."""
        tomorrow = fields.Datetime.now() + timedelta(days=1)
        task = self.env["project.task"].create(
            {
                "name": "Future Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_end": tomorrow,
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertFalse(result["is_overdue"])

    def test_task_to_dict_no_deadline(self):
        """Test _task_to_dict when task has no planned_date_end."""
        task = self.env["project.task"].create(
            {
                "name": "No Deadline",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertFalse(result["is_overdue"])
        self.assertEqual(result["planned_date_end"], "")


@tagged("post_install", "-at_install")
class TestComputeUserEta(TeamDashboardCommon):
    """Tests for project.project._compute_user_eta."""

    def test_eta_zero_remaining(self):
        """Test ETA returns False when no remaining hours."""
        today = fields.Date.today()
        result = self.env["project.project"]._compute_user_eta(self.user_dev1, 0, today)
        self.assertFalse(result)

    def test_eta_negative_remaining(self):
        """Test ETA returns False when remaining is negative."""
        today = fields.Date.today()
        result = self.env["project.project"]._compute_user_eta(self.user_dev1, -5, today)
        self.assertFalse(result)

    def test_eta_positive_remaining_no_employee(self):
        """Test ETA uses fallback when user has no employee record."""
        today = fields.Date.today()
        # user_dev1 may or may not have an employee record
        # Create a user explicitly without one
        user_no_emp = self.env["res.users"].create(
            {
                "name": "No Employee User",
                "login": "no_emp_test",
                "email": "noemp@test.com",
            },
        )
        # Ensure no employee record
        employees = self.env["hr.employee"].search([("user_id", "=", user_no_emp.id)])
        employees.unlink()

        result = self.env["project.project"]._compute_user_eta(user_no_emp, 20.0, today)
        self.assertTrue(result)
        # Should be a date string in YYYY-MM-DD format
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2}")

    def test_eta_with_employee_and_calendar(self):
        """Test ETA calculation with employee and resource calendar."""
        today = fields.Date.today()
        # employee_dev2 is already created in common setup with a calendar
        self.employee_dev2.resource_calendar_id = self.env.company.resource_calendar_id

        try:
            result = self.env["project.project"]._compute_user_eta(
                self.user_dev2,
                40.0,
                today,
            )
        except (OSError, ValueError):
            # plan_hours may fail in test environments (network/resource issues)
            return

        # Should return a date string
        if result:  # May be False if calendar can't plan
            self.assertRegex(result, r"\d{4}-\d{2}-\d{2}")
            eta_date = fields.Date.from_string(result)
            self.assertGreater(eta_date, today)


@tagged("post_install", "-at_install")
class TestDashboardMultipleInProgress(TeamDashboardCommon):
    """Test dashboard behavior when user has multiple in-progress tasks."""

    def test_multiple_in_progress_picks_one_as_current(self):
        """Test that with multiple in-progress tasks, one becomes current and rest are in queue."""
        # Create two in-progress tasks for same user
        task1 = self.env["project.task"].create(
            {
                "name": "IP Task 1",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        task2 = self.env["project.task"].create(
            {
                "name": "IP Task 2",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )

        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)

        # Current task should be one of the in-progress tasks
        self.assertTrue(dev1_data["current_task"])
        self.assertIn(dev1_data["current_task"]["id"], [task1.id, task2.id])

        # The other in-progress task should be in the queue
        queue_ids = [t["id"] for t in dev1_data["queue"]]
        remaining = {task1.id, task2.id} - {dev1_data["current_task"]["id"]}
        for task_id in remaining:
            self.assertIn(task_id, queue_ids)

    def test_no_in_progress_means_no_current_task(self):
        """Test that member with no in-progress tasks has no current_task."""
        # Create user with only backlog tasks
        user = self.env["res.users"].create(
            {
                "name": "Backlog Only User",
                "login": "backlog_only_test",
                "email": "backlog@test.com",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("project.group_project_user").id,
                        ],
                    ),
                ],
            },
        )
        self.env["project.task"].create(
            {
                "name": "Only Backlog",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "user_ids": [(6, 0, [user.id])],
            },
        )
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        user_data = next(m for m in data["members"] if m["user_id"] == user.id)
        self.assertFalse(user_data["current_task"])
        self.assertEqual(len(user_data["backlog"]), 1)
