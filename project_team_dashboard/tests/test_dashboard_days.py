import base64
import io
from datetime import date

import openpyxl
from freezegun import freeze_time

from odoo import Command
from odoo.tests.common import tagged

from .common import TeamDashboardCommon

# Late on Sunday 31 May 2026 in UTC it is Monday 1 June in Manila.
NOW = "2026-05-31 23:30:00"
# Monday 1 June in UTC, already Tuesday in Manila: a working day in both.
WEEKDAY_NOW = "2026-06-01 20:00:00"
TODAY_HEADER_COLOR = "FFFDEBD0"


@tagged("post_install", "-at_install")
class TestDashboardDays(TeamDashboardCommon):
    """The period, today and the overdue tasks are the reader's day; a member's hours of today
    and estimated completion count from the member's own day, UTC without a time zone."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        reader_groups = ["project.group_project_manager"]
        member_groups = ["project.group_project_user"]
        cls.reader_manila, cls.reader_utc, cls.user_lead, cls.user_dev3, cls.user_without_employee = cls.env[
            "res.users"
        ].create(
            [
                dict(cls._user_vals("Reader in Manila", reader_groups), tz="Asia/Manila"),
                dict(cls._user_vals("Reader in UTC", reader_groups), tz="UTC"),
                dict(cls._user_vals("Day Lead", member_groups), tz="UTC"),
                dict(cls._user_vals("Dev Three", member_groups), tz=False),
                dict(cls._user_vals("Without Employee", member_groups), tz=False),
            ]
        )
        cls.user_dev1.tz = "Asia/Manila"
        cls.user_dev2.tz = "UTC"
        cls.department = cls.env["hr.department"].create({"name": "Day Department"})
        employee_lead = cls.env["hr.employee"].create({"name": "Day Lead", "user_id": cls.user_lead.id})
        employee_dev3 = cls.env["hr.employee"].create({"name": "Dev Three", "user_id": cls.user_dev3.id})
        cls.employee_dev1.tz = "Asia/Manila"
        cls.employee_dev2.tz = "UTC"
        employee_dev3.tz = "Asia/Manila"
        # not the company's calendar, whose hours and leaves are the database's: eight hours, Monday to Friday
        calendar = cls.env["resource.calendar"].create(
            {
                "name": "Eight Hours a Day",
                "attendance_ids": [
                    Command.create({"name": "Day", "dayofweek": str(weekday), "hour_from": 9, "hour_to": 17})
                    for weekday in range(5)
                ],
            }
        )
        (cls.employee_dev1 | cls.employee_dev2 | employee_dev3).write(
            {"parent_id": employee_lead.id, "department_id": cls.department.id, "resource_calendar_id": calendar.id}
        )
        task_vals = {"project_id": cls.project.id, "stage_id": cls.stage_in_progress.id, "planned_hours": 11.0}
        cls.task_dev1, cls.task_dev2, cls.task_dev3, cls.task_without_employee, cls.task_deadline_only = cls.env[
            "project.task"
        ].create(
            [
                # ends in the afternoon of 31 May in Manila
                dict(
                    task_vals,
                    name="Ends on 31 May",
                    user_ids=[Command.set(cls.user_dev1.ids)],
                    planned_date_start="2026-05-30 10:00:00",
                    planned_date_end="2026-05-31 10:00:00",
                ),
                # ends at 04:00 on 1 June in Manila
                dict(
                    task_vals,
                    name="Ends on 1 June in Manila",
                    user_ids=[Command.set(cls.user_dev2.ids)],
                    planned_date_start="2026-05-30 10:00:00",
                    planned_date_end="2026-05-31 20:00:00",
                ),
                dict(task_vals, name="Undated", user_ids=[Command.set(cls.user_dev3.ids)]),
                dict(task_vals, name="Of a user without employee", planned_hours=5.7),
                dict(task_vals, name="Deadline on 31 May", planned_hours=0.0, date_deadline=date(2026, 5, 31)),
            ]
        )
        cls.task_without_employee.user_ids = cls.user_without_employee
        cls.task_deadline_only.user_ids = cls.user_dev3
        cls.env["account.analytic.line"].create(
            [
                {
                    "name": "Work",
                    "project_id": cls.project.id,
                    "task_id": task.id,
                    "user_id": task.user_ids.id,
                    "unit_amount": hours,
                    "date": day,
                }
                for task in (cls.task_dev1, cls.task_dev2, cls.task_dev3)
                for day, hours in ((date(2026, 5, 31), 1.0), (date(2026, 6, 1), 2.0))
            ]
        )

    def _project_model(self, reader, **context):
        return self.env["project.project"].with_user(reader).with_context(**context)

    def _team_data(self, Project):
        return {
            "project": Project.get_team_dashboard_data(self.project.id),
            "manager": Project.get_team_dashboard_by_manager(self.user_lead.id),
            "department": Project.get_team_dashboard_by_department(self.department.id),
        }

    def _cards(self, data):
        return {card["user_id"]: card for card in data["members"]}

    @freeze_time(NOW)
    def test_the_period_and_today_are_the_reader_s_day(self):
        manila_days = (("2026-06-01", "2026-06-07"), "2026-06-01")
        utc_days = (("2026-05-25", "2026-05-31"), "2026-05-31")
        for Project, (period, today) in (
            (self._project_model(self.reader_manila), manila_days),
            (self._project_model(self.reader_utc), utc_days),
            (self._project_model(self.reader_utc, tz="Asia/Manila"), manila_days),
        ):
            reader = {"reader": Project.env.user.name, "context_tz": Project.env.context.get("tz")}
            for mode, data in self._team_data(Project).items():
                with self.subTest(mode=mode, **reader):
                    self.assertEqual((data["period_start"], data["period_end"]), period)
            with self.subTest(**reader):
                self.assertEqual(Project.get_gantt_chart_data(self.project.id)["today"], today)
                board = self.env["project.team.board"].with_env(Project.env)
                this_week = board.search(
                    [("mode", "=", "project"), ("project_id", "=", self.project.id), ("period", "=", "this_week")]
                )
                self.assertEqual((this_week.data["period_start"], this_week.data["period_end"]), period)

    @freeze_time(NOW)
    def test_overdue_and_the_days_of_the_tasks_are_the_reader_s(self):
        for reader, is_overdue, late_end_day in (
            (self.reader_manila, True, "2026-06-01"),
            (self.reader_utc, False, "2026-05-31"),
        ):
            Project = self._project_model(reader)
            for mode, data in self._team_data(Project).items():
                with self.subTest(reader=reader.name, mode=mode):
                    cards = self._cards(data)
                    self.assertEqual(cards[self.user_dev1.id]["current_task"]["is_overdue"], is_overdue)
                    self.assertEqual(cards[self.user_dev2.id]["current_task"]["planned_date_end"], late_end_day)
                    self.assertFalse(cards[self.user_dev2.id]["current_task"]["is_overdue"])
            with self.subTest(reader=reader.name, mode="gantt"):
                gantt_tasks = {task["id"]: task for task in Project.get_gantt_chart_data(self.project.id)["tasks"]}
                self.assertEqual(
                    [gantt_tasks[task.id]["is_overdue"] for task in (self.task_dev1, self.task_deadline_only)],
                    [is_overdue, is_overdue],
                )
                self.assertFalse(gantt_tasks[self.task_dev2.id]["is_overdue"])
                self.assertEqual(gantt_tasks[self.task_dev2.id]["plan_date_end"], late_end_day)

    def test_the_days_of_the_tasks_follow_the_time_zone_of_the_context_over_the_stored_one(self):
        gantt_data = self._project_model(self.reader_utc, tz="Asia/Manila").get_gantt_chart_data(self.project.id)

        self.assertEqual(gantt_data["user_tz"], "Asia/Manila")
        gantt_tasks = {task["id"]: task for task in gantt_data["tasks"]}
        self.assertEqual(gantt_tasks[self.task_dev2.id]["plan_date_end"], "2026-06-01")

    @freeze_time(NOW)
    def test_the_export_marks_the_reader_s_today(self):
        for reader, today_header in ((self.reader_manila, "01 Jun"), (self.reader_utc, "31 May")):
            with self.subTest(reader=reader.name):
                result = self._project_model(reader).export_gantt_excel([self.project.id])
                sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
                today_headers = [cell.value for cell in sheet[1] if cell.fill.fgColor.rgb == TODAY_HEADER_COLOR]
                self.assertEqual(today_headers, [today_header])

    @freeze_time(NOW)
    def test_a_member_s_hours_of_today_are_those_of_their_own_day(self):
        # Dev Three has no time zone: core dates their lines on the UTC day, not their employee's Manila one
        hours_today = {self.user_dev1.id: 2.0, self.user_dev2.id: 1.0, self.user_dev3.id: 1.0}
        for reader in self.reader_manila | self.reader_utc:
            for mode, data in self._team_data(self._project_model(reader)).items():
                with self.subTest(reader=reader.name, mode=mode):
                    cards = self._cards(data)
                    self.assertEqual(
                        {user_id: cards[user_id]["hours_logged_today"] for user_id in hours_today}, hours_today
                    )

    @freeze_time(WEEKDAY_NOW)
    def test_the_estimated_completion_starts_on_the_member_s_own_day(self):
        # 8 hours left each: a working day from the member's midnight, on their employee's schedule;
        # 5.7 hours left without an employee: a day of 5.7 hours after the member's own day
        own_day_completions = {
            self.user_dev1.id: "2026-06-02",
            self.user_dev2.id: "2026-06-01",
            self.user_dev3.id: "2026-06-01",
            self.user_without_employee.id: "2026-06-02",
        }
        for reader in self.reader_manila | self.reader_utc:
            with self.subTest(reader=reader.name):
                cards = self._cards(self._project_model(reader).get_team_dashboard_data(self.project.id))
                self.assertEqual(
                    {user_id: cards[user_id]["estimated_completion"] for user_id in own_day_completions},
                    own_day_completions,
                )
