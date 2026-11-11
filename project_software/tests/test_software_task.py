from odoo.tests.common import tagged

from ..models.project_task import TSHIRT_HOURS
from .common import ProjectSoftwareCommon


@tagged("post_install", "-at_install")
class TestSoftwareTask(ProjectSoftwareCommon):
    def _selection_values(self, field_name):
        return [value for value, _label in self.env["project.task"]._fields[field_name].selection]

    def test_is_software_project_follows_the_project(self):
        self.assertTrue(self.task.is_software_project)
        plain_project = self.env["project.project"].create({"name": "Plain Project"})
        plain_task = self.env["project.task"].create({"name": "Plain Task", "project_id": plain_project.id})
        self.assertFalse(plain_task.is_software_project)
        plain_project.is_software_project = True
        self.assertTrue(plain_task.is_software_project)

    def test_defaults(self):
        self.assertRecordValues(
            self.task,
            [
                {
                    "task_type": "improvement",
                    "priority_level": "1",
                    "difficulty": "easy",
                    "knowledge_area_level": "basic",
                    "tshirt_estimate": "m",
                },
            ],
        )

    def test_selection_values_are_the_ones_tooling_writes(self):
        expected_values = {
            "task_type": ["feature", "bug", "improvement", "investigation", "milestone", "support", "technical_debt"],
            "priority_level": ["4", "3", "2", "1", "0"],
            "difficulty": ["easy", "medium", "hard"],
            "knowledge_area_level": ["basic", "intermediate", "advanced"],
            "tshirt_estimate": ["xs", "s", "m", "l", "xl", "xxl"],
        }
        for field_name, values in expected_values.items():
            self.assertEqual(self._selection_values(field_name), values, field_name)
            for value in values:
                self.task.write({field_name: value})
                self.assertEqual(self.task[field_name], value)

    def test_tshirt_estimate_fills_empty_planned_hours(self):
        self.assertEqual(set(TSHIRT_HOURS), set(self._selection_values("tshirt_estimate")))
        for size, hours in TSHIRT_HOURS.items():
            task = self.env["project.task"].new({"name": "Estimated", "tshirt_estimate": size})
            task._onchange_tshirt_estimate()
            self.assertEqual(task.planned_hours, hours, size)

    def test_tshirt_estimate_keeps_planned_hours_already_set(self):
        task = self.env["project.task"].new({"name": "Estimated", "planned_hours": 42, "tshirt_estimate": "xs"})
        task._onchange_tshirt_estimate()
        self.assertEqual(task.planned_hours, 42)
