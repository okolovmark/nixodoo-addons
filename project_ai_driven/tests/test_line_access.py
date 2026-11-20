import unittest

from lxml import etree

from odoo.exceptions import AccessError

from .common import ProjectAiDrivenCommon

# The values of a new line of each model, and a change that runs no rule of the model.
LINE_VALUES = {
    "project.task.checklist.item": ({"name": "Item"}, {"deadline": "2026-12-01 10:00:00"}),
    "project.task.testcase": ({"name": "Case"}, {"comments": "<p>Checked</p>"}),
    "project.task.question": ({"question": "<p>Why?</p>"}, {"round": "Second round"}),
    "project.task.session": ({"session_id": "0f8e2c1a-5b7d-4e3a-9c61-2d4b8a7f1e90"}, {"outcome": "Reviewed"}),
}
LINE_SUMMARY_FIELDS = (
    "completion",
    "completion_xml",
    "kanban_checklist",
    "testcase_completion",
    "testcase_completion_xml",
    "kanban_testcases",
)
CARD_SUMMARY_FIELDS = ("completion_xml", "kanban_checklist", "testcase_completion_xml", "kanban_testcases")


class TestLineSummaryAccess(ProjectAiDrivenCommon):
    """The task fields that sum up its checklist and its test cases are read
    by whoever reads those lines.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["project.task.checklist.item"].create({"name": "Item", "task_id": cls.task.id})
        cls.env["project.task.testcase"].create({"name": "Case", "task_id": cls.task.id})
        cls.internal_user = cls._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")

    def _get_card_arch(self, user):
        kanban_view = self.env.ref("project.view_task_kanban")
        views = self.env["project.task"].with_user(user).get_views([(kanban_view.id, "kanban")])
        return views["views"]["kanban"]["arch"]

    def test_an_internal_user_without_project_rights_reads_every_field_of_a_task_but_the_line_summaries(self):
        task = self.task.with_user(self.internal_user)
        field_names = list(task.fields_get())

        (task_values,) = task.read(field_names)

        self.assertEqual(task_values["name"], "AI-driven Task")
        self.assertFalse(set(LINE_SUMMARY_FIELDS) & set(task_values))
        card_arch = self._get_card_arch(self.internal_user)
        self.assertFalse([name for name in CARD_SUMMARY_FIELDS if name in card_arch], "the task cards name no summary")

    def test_a_project_user_reads_the_line_summaries_and_gets_them_on_the_task_cards(self):
        (task_values,) = self.task.with_user(self.user_dev).read(list(LINE_SUMMARY_FIELDS))

        self.assertEqual((task_values["completion"], task_values["testcase_completion"]), (100, 0))
        self.assertIn("Not run: 1", task_values["kanban_testcases"])
        card_arch = etree.fromstring(self._get_card_arch(self.user_dev))
        shown_summaries = [
            name for name in CARD_SUMMARY_FIELDS if card_arch.xpath("//t[@t-out='record.%s.raw_value']" % name)
        ]
        self.assertEqual(shown_summaries, list(CARD_SUMMARY_FIELDS))


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
