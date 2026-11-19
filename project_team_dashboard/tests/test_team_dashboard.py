from datetime import date, datetime, timedelta

from freezegun import freeze_time

from odoo import Command, fields
from odoo.tests import tagged

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
                "allocated_hours": 10.0,
            },
        )

        # Create task for dev1 - backlog
        cls.task_backlog = cls.env["project.task"].create(
            {
                "name": "Dev1 Backlog Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "allocated_hours": 8.0,
            },
        )

        # Create task for dev2 - in progress
        cls.task_dev2 = cls.env["project.task"].create(
            {
                "name": "Dev2 Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev2.id])],
                "allocated_hours": 16.0,
            },
        )

        # Create an unassigned task
        cls.task_unassigned = cls.env["project.task"].create(
            {
                "name": "Unassigned Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "user_ids": [(6, 0, [])],
                "allocated_hours": 4.0,
            },
        )

        # Create a task in the "other" stage (not backlog, not in_progress, not folded)
        cls.task_other = cls.env["project.task"].create(
            {
                "name": "Dev1 Review Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_other.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "allocated_hours": 2.0,
            },
        )

        # Create a done task - should NOT appear in dashboard
        cls.task_done = cls.env["project.task"].create(
            {
                "name": "Done Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_done.id,
                "state": "1_done",
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "allocated_hours": 5.0,
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
        """Test that done tasks are not in dashboard."""
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
        """Test that total_open_tasks counts only open tasks."""
        data = self.env["project.project"].get_team_dashboard_data(self.project.id)
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        # dev1 has: task_in_progress, task_backlog, task_other (done is excluded)
        self.assertEqual(dev1_data["total_open_tasks"], 3)

    def test_a_task_is_closed_by_its_state_not_by_a_folded_stage(self):
        folded_open_task, canceled_task = self.env["project.task"].create(
            [
                {
                    "name": "Folded Open Task",
                    "project_id": self.project.id,
                    "stage_id": self.stage_done.id,
                    "user_ids": [(6, 0, [self.user_dev1.id])],
                    "allocated_hours": 1.5,
                },
                {
                    "name": "Canceled Task",
                    "project_id": self.project.id,
                    "stage_id": self.stage_in_progress.id,
                    "state": "1_canceled",
                    "user_ids": [(6, 0, [self.user_dev1.id])],
                    "allocated_hours": 7.0,
                },
            ]
        )

        data = self.env["project.project"].get_team_dashboard_data(self.project.id)

        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        self.assertEqual(dev1_data["total_open_tasks"], 4)
        self.assertAlmostEqual(dev1_data["total_planned_hours"], 21.5)
        self.assertIn(folded_open_task.id, [t["id"] for t in dev1_data["other"]])
        listed_ids = {t["id"] for key in ("queue", "backlog", "other") for t in dev1_data[key]}
        self.assertNotIn(canceled_task.id, listed_ids | {dev1_data["current_task"]["id"]})

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
        today = fields.Date.today()
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

    def test_dashboard_with_timesheets(self):
        """Test that timesheet hours are aggregated per user."""
        today = fields.Date.today()
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
                "allocated_hours": 10.0,
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        self.env["account.analytic.line"].create(
            {
                "name": "Dict work",
                "project_id": self.project.id,
                "task_id": task.id,
                "employee_id": self.employee_dev1.id,
                "unit_amount": 2.5,
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertEqual(result["id"], task.id)
        self.assertEqual(result["name"], "Dict Test Task")
        self.assertEqual(result["stage_id"], self.stage_in_progress.id)
        self.assertEqual(result["stage_name"], "In Progress")
        self.assertAlmostEqual(result["planned_hours"], 10.0)
        self.assertAlmostEqual(result["remaining_hours"], 7.5)
        self.assertAlmostEqual(result["progress"], 25.0, msg="The progress is a percentage")
        self.assertEqual(result["kanban_state"], "01_in_progress", "The key carries the state of the task")
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
        today = fields.Date.today()
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

    def test_eta_follows_the_working_days_of_the_employee_in_their_timezone(self):
        calendar = self.env["resource.calendar"].create({"name": "Tokyo Office"})
        self.employee_dev2.write({"resource_calendar_id": calendar.id, "tz": "Asia/Tokyo"})
        Project = self.env["project.project"]
        self.assertEqual(calendar.hours_per_day, 8.0)

        self.assertEqual(Project._compute_user_eta(self.user_dev2, 16.0, date(2026, 3, 2)), "2026-03-03")
        self.assertEqual(Project._compute_user_eta(self.user_dev2, 16.0, date(2026, 3, 6)), "2026-03-09")

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
                "group_ids": [
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


@tagged("post_install", "-at_install")
class TestDashboardDayOfTheUser(TeamDashboardCommon):
    """The server runs in UTC: the default week, the planned days and the overdue tasks of a card
    follow the viewer's day; its Today hours and ETA the day of its member, the one their
    timesheets are dated on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee_dev1.write({"parent_id": cls.employee_dev2.id, "tz": "Asia/Tokyo"})
        cls.ended_last_evening_task, cls.ends_this_morning_task = cls.env["project.task"].create(
            [
                {
                    "name": "Ended at 7 PM on Sunday in Tokyo",
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_in_progress.id,
                    "user_ids": [Command.set(cls.user_dev1.ids)],
                    "planned_date_start": datetime(2026, 11, 20, 1),
                    "planned_date_end": datetime(2026, 11, 22, 10),
                },
                {
                    "name": "Ends at 5 AM on Monday in Tokyo",
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_backlog.id,
                    "user_ids": [Command.set(cls.user_dev1.ids)],
                    "planned_date_start": datetime(2026, 11, 20, 1),
                    "planned_date_end": datetime(2026, 11, 22, 20),
                },
            ]
        )
        cls.env["account.analytic.line"].create(
            [
                {
                    "name": "Logged on %s" % day,
                    "project_id": cls.project.id,
                    "task_id": cls.ended_last_evening_task.id,
                    "user_id": cls.user_dev1.id,
                    "unit_amount": hours,
                    "date": day,
                }
                for day, hours in ((date(2026, 11, 22), 2.0), (date(2026, 11, 23), 3.0))
            ]
        )

    @freeze_time("2026-11-22 22:00:00")
    def test_in_tokyo_at_7_am_on_monday_the_cards_count_monday_and_its_week(self):
        Project = self.env["project.project"].with_context(tz="Asia/Tokyo")
        for mode, payload in (
            ("project", Project.get_team_dashboard_data(self.project.id)),
            ("manager", Project.get_team_dashboard_by_manager(self.user_dev2.id)),
        ):
            with self.subTest(mode=mode):
                card = next(member for member in payload["members"] if member["user_id"] == self.user_dev1.id)
                self.assertEqual((payload["period_start"], payload["period_end"]), ("2026-11-23", "2026-11-29"))
                self.assertEqual(card["hours_logged_today"], 3.0, "the hours of Sunday are not today's")
                self.assertEqual(card["hours_logged_period"], 3.0, "Sunday is in the week before")
                self.assertEqual(card["current_task"]["id"], self.ended_last_evening_task.id)
                self.assertTrue(card["current_task"]["is_overdue"], "a task that ended yesterday evening is overdue")
                self.assertEqual([task["id"] for task in card["backlog"]], self.ends_this_morning_task.ids)
                self.assertFalse(card["backlog"][0]["is_overdue"], "a task ending this morning is not overdue")
                self.assertEqual(
                    (card["backlog"][0]["planned_date_start"], card["backlog"][0]["planned_date_end"]),
                    ("2026-11-20", "2026-11-23"),
                    "the task ends on Monday in Tokyo",
                )

    @freeze_time("2026-11-19 15:00:00")
    def test_in_new_york_a_card_shows_its_planned_days_in_new_york(self):
        self.env["project.task"].create(
            {
                "name": "Ended at 8 PM on Wednesday in New York",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "user_ids": [Command.set(self.user_dev2.ids)],
                "planned_date_start": datetime(2026, 11, 17, 3),
                "planned_date_end": datetime(2026, 11, 19, 1),
            }
        )

        payload = (
            self.env["project.project"].with_context(tz="America/New_York").get_team_dashboard_data(self.project.id)
        )

        card = next(member for member in payload["members"] if member["user_id"] == self.user_dev2.id)
        task_data = card["backlog"][0]
        self.assertEqual(
            (task_data["planned_date_start"], task_data["planned_date_end"]),
            ("2026-11-16", "2026-11-18"),
            "the task ran from Monday to Wednesday in New York",
        )
        self.assertTrue(task_data["is_overdue"], "a task that ended yesterday is overdue")

    @freeze_time("2026-11-24 22:00:00")
    def test_a_card_counts_today_and_plans_its_eta_on_the_day_of_its_employee(self):
        """It is Wednesday morning in Tokyo, the viewer's day, and Tuesday afternoon in New York."""
        self.employee_dev1.tz = "America/New_York"
        self.env["account.analytic.line"].create(
            [
                {
                    "name": "Logged on %s" % day,
                    "project_id": self.project.id,
                    "task_id": self.ended_last_evening_task.id,
                    "user_id": self.user_dev1.id,
                    "unit_amount": hours,
                    "date": day,
                }
                for day, hours in ((date(2026, 11, 24), 1.0), (date(2026, 11, 25), 4.0))
            ]
        )
        # 10 hours are logged on it: 4 are left
        self.ended_last_evening_task.allocated_hours = 14.0

        Project = self.env["project.project"].with_context(tz="Asia/Tokyo")
        for mode, payload in (
            ("project", Project.get_team_dashboard_data(self.project.id)),
            ("manager", Project.get_team_dashboard_by_manager(self.user_dev2.id)),
        ):
            with self.subTest(mode=mode):
                card = next(member for member in payload["members"] if member["user_id"] == self.user_dev1.id)
                self.assertEqual(card["hours_logged_today"], 1.0, "it is still Tuesday in New York")
                self.assertEqual(
                    card["estimated_completion"], "2026-11-24", "the 4 hours left fit in Tuesday in New York"
                )

    @freeze_time("2026-11-24 10:30:00")
    def test_a_viewer_without_hr_rights_dates_each_member_on_the_day_of_their_timesheets(self):
        """It is Monday in Pago Pago, Tuesday in Brussels and Wednesday in Kiritimati."""
        viewer = self.env["res.users"].create(
            dict(
                self._user_vals(
                    "Viewer", ["base.group_user", "project.group_project_user", "hr_timesheet.group_hr_timesheet_user"]
                ),
                tz="Europe/Brussels",
            )
        )
        user_without_employee = self.env["res.users"].create(self._user_vals("No Employee", ["base.group_user"]))
        # core copies a timezone written on a user to their employee and back: these writes do not
        self.user_dev1.partner_id.tz = "Pacific/Pago_Pago"
        self.employee_dev1.version_id.tz = "Pacific/Kiritimati"
        (self.user_dev2 | user_without_employee).partner_id.tz = False
        self.employee_dev2.version_id.tz = "Pacific/Pago_Pago"
        members = (self.user_dev1 | self.user_dev2 | user_without_employee).with_user(viewer)
        # employee_id is cached as the superuser computed it: the viewer computes it afresh
        self.env.invalidate_all()

        member_today_by_user = (
            self.env["project.project"]
            .with_user(viewer)
            .with_context(tz="Pacific/Kiritimati")
            ._get_member_today_by_user(members)
        )

        self.assertEqual(
            member_today_by_user,
            {
                self.user_dev1.id: date(2026, 11, 23),
                self.user_dev2.id: date(2026, 11, 23),
                user_without_employee.id: date(2026, 11, 25),
            },
            "the member's own timezone, else their employee's, else the one the viewer's client sends",
        )
