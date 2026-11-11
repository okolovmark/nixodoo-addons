from odoo.exceptions import AccessError

from .common import ProjectSoftwareCommon


class TestTestCases(ProjectSoftwareCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Testcase = cls.env["project.task.testcase"]

    def _new_case(self, **vals):
        return self.Testcase.create({"name": "Boot test", "task_id": self.task.id, **vals})

    def test_completion_counts_done_over_not_cancelled(self):
        self.assertEqual(self.task.testcase_completion, 100, "no case yet")
        first_case = self._new_case()
        self._new_case(name="Dropped", state="cancelled")
        self.assertEqual(self.task.testcase_completion, 0)
        self.assertIn("0.0% Done", self.task.testcase_completion_xml)
        first_case.write({"state": "done"})
        self.assertEqual(self.task.testcase_completion, 100)
        self.assertIn("100.0% Done", self.task.testcase_completion_xml)

    def test_kanban_summary_counts_todo_and_waiting(self):
        case = self._new_case()
        self.assertIn("TODO: 1", self.task.kanban_testcases)
        case.write({"state": "waiting"})
        self.assertIn("Waiting: 1", self.task.kanban_testcases)
        self.assertNotIn("TODO", self.task.kanban_testcases)

    def test_only_done_ticks_passed(self):
        case = self._new_case()
        case.change_state_done()
        self.assertRecordValues(case, [{"state": "done", "passed": True}])
        for action, state in (
            (case.change_state_waiting, "waiting"),
            (case.change_state_cancelled, "cancelled"),
            (case.change_state_todo, "todo"),
        ):
            case.passed = True
            action()
            self.assertRecordValues(case, [{"state": state, "passed": False}])

    def test_copying_a_task_copies_its_cases_with_their_state(self):
        self._new_case(name="Passed case", state="done", passed=True)
        self._new_case(name="Open case")
        task_copy = self.task.copy()
        self.assertEqual(
            sorted(task_copy.testcase_ids.mapped(lambda case: (case.name, case.state, case.passed))),
            [("Open case", "todo", False), ("Passed case", "done", True)],
        )
        self.assertEqual(len(self.task.testcase_ids), 2, "the original keeps its own cases")

    def test_test_cases_need_project_user(self):
        self._new_case()
        internal_user = self._create_user("Internal Only", "project_software_internal", "internal@example.com")
        self.assertTrue(self.Testcase.with_user(self.user_dev).search_count([("task_id", "=", self.task.id)]))
        with self.assertRaises(AccessError):
            self.Testcase.with_user(internal_user).search_count([])
