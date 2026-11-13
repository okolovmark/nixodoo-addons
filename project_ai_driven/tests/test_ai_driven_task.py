from odoo.tests.common import tagged

from ..hooks import MODULE
from ..models.project_task import TSHIRT_HOURS
from .common import ProjectAiDrivenCommon

DELETED_FIELDS = ("difficulty", "knowledge_area_level", "tester_id")


@tagged("post_install", "-at_install")
class TestAiDrivenTask(ProjectAiDrivenCommon):
    def _selection_values(self, field_name):
        return [value for value, _label in self.env["project.task"]._fields[field_name].selection]

    def _task_views(self):
        external_ids = self.env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "ir.ui.view")])
        views = self.env["ir.ui.view"].browse(external_ids.mapped("res_id"))
        return views.filtered(lambda view: view.model == "project.task")

    def test_deleted_fields_are_not_on_the_model(self):
        self.assertFalse(set(DELETED_FIELDS) & set(self.env["project.task"]._fields))

    def test_no_task_view_names_a_deleted_field(self):
        views = self._task_views()
        self.assertTrue(views)
        for view in views:
            # the whole form or list the view extends, as the web client gets it
            names = {node.get("name") for node in view._get_combined_arch().iter("field")}
            self.assertFalse(names & set(DELETED_FIELDS), view.xml_id)

    def test_is_ai_driven_follows_the_project(self):
        self.assertTrue(self.task.is_ai_driven)
        plain_project = self.env["project.project"].create({"name": "Plain Project"})
        plain_task = self.env["project.task"].create({"name": "Plain Task", "project_id": plain_project.id})
        self.assertFalse(plain_task.is_ai_driven)
        plain_project.is_ai_driven = True
        self.assertTrue(plain_task.is_ai_driven)

    def test_is_ai_driven_label(self):
        for model in ("project.project", "project.task"):
            label = self.env[model].fields_get(["is_ai_driven"])["is_ai_driven"]["string"]
            self.assertEqual(label, "AI-driven Project", model)

    def test_defaults(self):
        self.assertRecordValues(
            self.task,
            [
                {
                    "task_type": "improvement",
                    "priority_level": "1",
                    "tshirt_estimate": "m",
                },
            ],
        )

    def test_selection_values_are_the_ones_tooling_writes(self):
        expected_values = {
            "task_type": ["feature", "bug", "improvement", "investigation", "milestone", "support", "technical_debt"],
            "priority_level": ["4", "3", "2", "1", "0"],
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
