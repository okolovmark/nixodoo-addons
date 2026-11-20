import base64
import io
from datetime import datetime, timedelta

import openpyxl
from freezegun import freeze_time

from odoo import fields
from odoo.tests.common import tagged

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
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            }
        )
        followed_task.message_subscribe(partner_ids=self.user_dev1.partner_id.ids)
        Project = self.Project.with_user(self.user_dev1)
        self.assertFalse(Project.search([("id", "=", project_hidden.id)]))

        result = Project.export_gantt_excel([project_hidden.id, self.project.id])
        self.assertEqual(result["filename"], "Gantt_Test_AI-driven_Project.xlsx")

        result = Project.export_gantt_excel([project_hidden.id])
        self.assertEqual(result, {"error": "No tasks with planned dates"})

        project_last = self.Project.create({"name": "Zulu Plan"})
        projects = self.project | project_last
        self.assertEqual(Project.search([("id", "in", projects.ids)]).ids, projects.ids)
        result = Project.export_gantt_excel([project_hidden.id, project_last.id, self.project.id])
        self.assertEqual(result["filename"], "Gantt_Zulu_Plan.xlsx")

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
                "date_deadline": deadline,
            },
        )
        result = self.Project.export_gantt_excel([self.project.id])
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        header = [cell.value for cell in sheet[1]]
        deadline_row = next(row for row in sheet.iter_rows(min_row=2) if row[1].value == "Deadline Only")
        deadline_cell = deadline_row[header.index(deadline.strftime("%d %b"))]
        self.assertEqual(deadline_cell.value, "\u25c6")

    def _read_sheet(self, result):
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        return [cell.value for cell in sheet[1]], sheet

    @freeze_time("2026-05-31 21:00:00")
    def test_a_bar_and_its_dates_are_on_the_days_of_the_reader(self):
        project = self.Project.create({"name": "Manila Office"})
        self.Task.create(
            {
                "name": "From 4 AM on 29 May to 4 AM on 1 June in Manila",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": datetime(2026, 5, 28, 20),
                "planned_date_end": datetime(2026, 5, 31, 20),
            }
        )

        header, sheet = self._read_sheet(self.Project.with_context(tz="Asia/Manila").export_gantt_excel([project.id]))

        task_row = sheet[2]
        bar_days = [header[cell.column - 1] for cell in task_row[header.index("Stage") + 1 :] if cell.fill.fill_type]
        self.assertEqual(bar_days, ["29 May", "30 May", "31 May", "01 Jun"])
        self.assertEqual(
            (task_row[header.index("Start")].value, task_row[header.index("End")].value),
            (datetime(2026, 5, 29), datetime(2026, 6, 1)),
        )
        today_headers = [cell.value for cell in sheet[1] if cell.fill.fgColor.rgb == "FFFDEBD0"]
        self.assertEqual(today_headers, ["01 Jun"], "05:00 in Manila is the last day of the bar")

    def test_a_planned_date_with_microseconds_is_exported(self):
        project = self.Project.create({"name": "Precise Office"})
        self.Task.create(
            {
                "name": "Planned by a script to the microsecond",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": datetime(2026, 5, 29, 9, 0, 0, 123456),
                "planned_date_end": datetime(2026, 5, 30, 17, 0, 0, 654321),
            }
        )

        header, sheet = self._read_sheet(self.Project.with_context(tz="UTC").export_gantt_excel([project.id]))

        task_row = sheet[2]
        self.assertEqual(
            (task_row[header.index("Start")].value, task_row[header.index("End")].value),
            (datetime(2026, 5, 29), datetime(2026, 5, 30)),
        )

    def test_a_deadline_only_task_stays_on_its_deadline_for_a_reader_behind_utc(self):
        project = self.Project.create({"name": "New York Office"})
        self.Task.create(
            {
                "name": "Due on 2 June",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "date_deadline": "2026-06-02",
            }
        )

        header, sheet = self._read_sheet(
            self.Project.with_context(tz="America/New_York").export_gantt_excel([project.id])
        )

        marked_days = [header[cell.column - 1] for cell in sheet[2] if cell.value == "\u25c6"]
        self.assertEqual(marked_days, ["02 Jun"])
        self.assertEqual(sheet[2][header.index("End")].value, datetime(2026, 6, 2))
