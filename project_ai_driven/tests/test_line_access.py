import unittest

from odoo.exceptions import AccessError

from .common import ProjectAiDrivenCommon

# The values of a new line of each model, and a change that runs no rule of the model.
LINE_VALUES = {
    "project.task.checklist.item": ({"name": "Item"}, {"deadline": "2026-12-01 10:00:00"}),
    "project.task.testcase": ({"name": "Case"}, {"comments": "<p>Checked</p>"}),
    "project.task.question": ({"question": "<p>Why?</p>"}, {"round": "Second round"}),
    "project.task.session": ({"session_id": "0f8e2c1a-5b7d-4e3a-9c61-2d4b8a7f1e90"}, {"outcome": "Reviewed"}),
}


class TestLineWriteAccess(ProjectAiDrivenCommon):
    """A line is changed by whoever may write its task: core lets a project
    user read a task template without project, not write it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.task_template = cls.env["project.task"].create({"name": "Task Template", "is_template": True})
        cls.template_lines = {
            model_name: cls._lines(model_name).create({**new_values, "task_id": cls.task_template.id})
            for model_name, (new_values, _change) in LINE_VALUES.items()
        }

    @classmethod
    def _lines(cls, model_name, user=None):
        lines = cls.env[model_name].with_context(mail_auto_subscribe_no_notify=True)
        return lines.with_user(user) if user else lines

    def test_a_project_user_reads_the_lines_of_a_task_template_without_project_and_changes_none(self):
        task_template = self.task_template.with_user(self.user_dev)
        self.assertFalse(task_template.project_id)
        task_template.check_access("read")
        with self.assertRaises(AccessError):
            task_template.check_access("write")
        for model_name, (new_values, change) in LINE_VALUES.items():
            (changed_field,) = change
            with self.subTest(model=model_name):
                lines = self._lines(model_name, self.user_dev)
                template_line = self.template_lines[model_name]
                line = template_line.with_user(self.user_dev)
                self.assertEqual(lines.search([("task_id", "=", self.task_template.id)]), line)
                self.assertEqual(len(line.read(list(change))), 1)
                # in place: Odoo's assertRaises rolls back what a refused write or unlink would leave behind
                with unittest.TestCase.assertRaises(self, AccessError):
                    line.write(change)
                with unittest.TestCase.assertRaises(self, AccessError):
                    line.unlink()
                template_line.invalidate_recordset()
                self.assertTrue(template_line.exists())
                self.assertFalse(template_line[changed_field])
                # core refuses a new line after its INSERT: the row goes with the rollback of the request
                with self.assertRaises(AccessError):
                    lines.create({**new_values, "task_id": self.task_template.id})

    def test_a_project_user_moves_a_line_only_onto_a_task_they_may_write(self):
        self.task_template.with_user(self.user_dev).check_access("read")
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        # not the template holding the template lines: a task lists a session once
        other_template = self.env["project.task"].create({"name": "Other Task Template", "is_template": True})
        for model_name, (new_values, _change) in LINE_VALUES.items():
            with self.subTest(model=model_name):
                line = self._lines(model_name, self.user_dev).create({**new_values, "task_id": self.task.id})
                with unittest.TestCase.assertRaises(self, AccessError):
                    line.write({"task_id": self.task_template.id})
                line.invalidate_recordset()
                self.assertEqual(line.task_id, self.task, "nothing moves")
                line.write({"task_id": other_task.id})
                self.assertEqual(line.task_id, other_task)
                line.sudo().write({"task_id": other_template.id})
                self.assertEqual(line.sudo().task_id, other_template)

    def test_a_project_user_creates_changes_and_deletes_the_lines_of_a_task_they_may_write(self):
        self.task.with_user(self.user_dev).check_access("write")
        for model_name, (new_values, change) in LINE_VALUES.items():
            (changed_field,) = change
            with self.subTest(model=model_name):
                line = self._lines(model_name, self.user_dev).create({**new_values, "task_id": self.task.id})
                line.write(change)
                self.assertTrue(line[changed_field])
                line.unlink()
                self.assertFalse(line.exists())
