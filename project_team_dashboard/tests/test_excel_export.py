import base64
import io
from datetime import datetime, time, timedelta

import openpyxl
from freezegun import freeze_time

from odoo import fields
from odoo.tests import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestExcelExport(TeamDashboardCommon):
    """Tests for Excel Gantt export (export_gantt_excel)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Project = cls.env["project.project"]
        cls.Task = cls.env["project.task"]
        cls.now = fields.Datetime.now()

        # Create tasks with dates so the Excel output is non-trivial
        cls.task_a = cls.Task.create(
            {
                "name": "Backend API",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "planned_date_start": cls.now,
                "planned_date_end": cls.now + timedelta(days=5),
                "user_ids": [(6, 0, [cls.user_dev1.id])],
            },
        )
        cls.task_b = cls.Task.create(
            {
                "name": "Frontend UI",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "planned_date_start": cls.now + timedelta(days=3),
                "planned_date_end": cls.now + timedelta(days=10),
            },
        )

    def test_export_returns_data(self):
        """export_gantt_excel with valid project returns data and filename."""
        result = self.Project.export_gantt_excel([self.project.id])
        self.assertIn("data", result, "Result must contain 'data' key")
        self.assertIn("filename", result, "Result must contain 'filename' key")
        self.assertTrue(result["data"], "Data should not be empty")
        self.assertTrue(result["filename"], "Filename should not be empty")

    def test_export_empty_project(self):
        """Project with no dated tasks returns an error."""
        empty_project = self.env["project.project"].create(
            {
                "name": "Empty Project",
                "is_ai_driven": True,
            },
        )
        result = self.Project.export_gantt_excel([empty_project.id])
        self.assertIn("error", result, "Result must contain 'error' for empty project")

    def test_export_no_projects_selected(self):
        """Empty project_ids list returns an error."""
        result = self.Project.export_gantt_excel([])
        self.assertIn("error", result)

    def test_export_filename_contains_project_name(self):
        """Filename should contain the project name."""
        result = self.Project.export_gantt_excel([self.project.id])
        self.assertIn("filename", result)
        # Project name is "Test AI-driven Project"
        self.assertIn("Test_AI-driven_Project", result["filename"])
        self.assertTrue(result["filename"].endswith(".xlsx"))

    def test_the_file_is_named_after_the_first_project_the_user_can_read(self):
        project_hidden = self.Project.create({"name": "Hidden Plan", "privacy_visibility": "followers"})
        followed_task = self.Task.create(
            {
                "name": "Followed Task",
                "project_id": project_hidden.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            }
        )
        followed_task.message_subscribe(partner_ids=self.user_dev1.partner_id.ids)
        project_first_in_order = self.Project.create({"name": "Alpha Plan"})
        Project = self.Project.with_user(self.user_dev1)
        self.assertFalse(project_hidden.with_user(self.user_dev1).has_access("read"))
        self.assertEqual(
            Project.search([("id", "in", (self.project | project_first_in_order).ids)])[:1], project_first_in_order
        )

        result = Project.export_gantt_excel([project_hidden.id, self.project.id, project_first_in_order.id])
        self.assertEqual(result["filename"], "Gantt_Test_AI-driven_Project.xlsx")

        result = Project.export_gantt_excel([project_hidden.id])
        self.assertEqual(result, {"error": "No tasks with planned dates"})

    def test_export_data_is_valid_base64(self):
        """Returned data is valid base64 that decodes without error."""
        result = self.Project.export_gantt_excel([self.project.id])
        self.assertIn("data", result)
        try:
            decoded = base64.b64decode(result["data"])
        except ValueError as exc:
            self.fail("base64 decode failed: %s" % exc)
        # XLSX files start with PK (zip) magic bytes
        self.assertTrue(decoded[:2] == b"PK", "Decoded data should be a valid ZIP/XLSX file")

    def test_deadline_only_task_exported_as_milestone(self):
        deadline = fields.Date.today() + timedelta(days=4)
        self.Task.create(
            {
                "name": "Deadline Only",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "date_deadline": datetime.combine(deadline, time(12)),
            },
        )
        result = self.Project.export_gantt_excel([self.project.id])
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        header = [cell.value for cell in sheet[1]]
        deadline_row = next(row for row in sheet.iter_rows(min_row=2) if row[1].value == "Deadline Only")
        deadline_cell = deadline_row[header.index(deadline.strftime("%d %b"))]
        self.assertEqual(deadline_cell.value, "\u25c6")

    @freeze_time("2026-11-18 22:00:00")
    def test_the_today_column_is_the_day_of_the_user(self):
        project = self.Project.create({"name": "Tokyo Office"})
        self.Task.create(
            {
                "name": "Across the week",
                "project_id": project.id,
                "planned_date_start": datetime(2026, 11, 16, 1),
                "planned_date_end": datetime(2026, 11, 20, 9),
            }
        )

        result = self.Project.with_context(tz="Asia/Tokyo").export_gantt_excel([project.id])

        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        today_headers = [cell.value for cell in sheet[1] if cell.fill.fgColor.rgb == "FFFDEBD0"]
        self.assertEqual(today_headers, ["19 Nov"], "07:00 in Tokyo is already the 19th")

    @freeze_time("2026-11-18 22:00:00")
    def test_a_bar_and_its_dates_are_on_the_days_of_the_user(self):
        project = self.Project.create({"name": "Tokyo Office"})
        self.Task.create(
            {
                "name": "From 5 AM on the 17th to 5 AM on the 20th in Tokyo",
                "project_id": project.id,
                "planned_date_start": datetime(2026, 11, 16, 20),
                "planned_date_end": datetime(2026, 11, 19, 20),
            }
        )

        result = self.Project.with_context(tz="Asia/Tokyo").export_gantt_excel([project.id])

        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        header = [cell.value for cell in sheet[1]]
        task_row = sheet[2]
        bar_days = [header[cell.column - 1] for cell in task_row[header.index("Stage") + 1 :] if cell.fill.fill_type]
        self.assertEqual(bar_days, ["17 Nov", "18 Nov", "19 Nov", "20 Nov"])
        self.assertEqual(
            (task_row[header.index("Start")].value, task_row[header.index("End")].value),
            (datetime(2026, 11, 17), datetime(2026, 11, 20)),
        )
        today_headers = [cell.value for cell in sheet[1] if cell.fill.fgColor.rgb == "FFFDEBD0"]
        self.assertEqual(today_headers, ["19 Nov"], "today is a day of the bar")

    def _export_rows(self, project):
        result = self.Project.with_context(tz="UTC").export_gantt_excel([project.id])
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        header = [cell.value for cell in sheet[1]]
        return header, {row[1].value: row for row in sheet.iter_rows(min_row=2)}

    def test_the_start_and_end_columns_leave_a_padded_date_empty(self):
        project = self.Project.create({"name": "Padded Dates"})
        self.Task.create(
            [
                {"name": "Start Only", "project_id": project.id, "planned_date_start": datetime(2026, 11, 16, 8)},
                {"name": "End Only", "project_id": project.id, "planned_date_end": datetime(2026, 11, 20, 8)},
                {"name": "Deadline Only", "project_id": project.id, "date_deadline": datetime(2026, 11, 24, 8)},
                {
                    "name": "Both Dates",
                    "project_id": project.id,
                    "planned_date_start": datetime(2026, 11, 17, 8),
                    "planned_date_end": datetime(2026, 11, 19, 8),
                },
            ]
        )

        header, rows_by_name = self._export_rows(project)

        self.assertEqual(
            {
                name: (row[header.index("Start")].value, row[header.index("End")].value)
                for name, row in rows_by_name.items()
            },
            {
                "Start Only": (datetime(2026, 11, 16), None),
                "End Only": (None, datetime(2026, 11, 20)),
                "Deadline Only": (None, None),
                "Both Dates": (datetime(2026, 11, 17), datetime(2026, 11, 19)),
            },
        )

    def test_a_milestone_with_its_end_date_alone_is_marked_on_that_date(self):
        project = self.Project.create({"name": "Launch Plan"})
        self.Task.create(
            {
                "name": "Launch",
                "project_id": project.id,
                "task_type": "milestone",
                "planned_date_end": datetime(2026, 11, 20, 8),
            }
        )

        header, rows_by_name = self._export_rows(project)

        marked_days = [header[cell.column - 1] for cell in rows_by_name["Launch"] if cell.value == "◆"]
        self.assertEqual(marked_days, ["20 Nov"])

    def test_the_rows_follow_the_start_time_of_the_user(self):
        project = self.Project.create({"name": "Tokyo Office"})
        self.Task.create(
            [
                {
                    "name": "From 2 PM on the 17th in Tokyo",
                    "project_id": project.id,
                    "planned_date_start": datetime(2026, 11, 17, 5),
                    "planned_date_end": datetime(2026, 11, 20, 5),
                },
                {
                    "name": "From 8 AM on the 17th in Tokyo",
                    "project_id": project.id,
                    "planned_date_start": datetime(2026, 11, 16, 23),
                    "planned_date_end": datetime(2026, 11, 22, 5),
                },
                {
                    "name": "Due on the 17th in Tokyo",
                    "project_id": project.id,
                    "date_deadline": datetime(2026, 11, 17, 3),
                },
            ]
        )

        result = self.Project.with_context(tz="Asia/Tokyo").export_gantt_excel([project.id])

        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        self.assertEqual(
            [row[1].value for row in sheet.iter_rows(min_row=2)],
            ["Due on the 17th in Tokyo", "From 8 AM on the 17th in Tokyo", "From 2 PM on the 17th in Tokyo"],
        )
