import base64
import io
from datetime import timedelta

import openpyxl

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
