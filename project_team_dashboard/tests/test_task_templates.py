from datetime import datetime

from odoo import Command
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .test_gantt_planning import GanttPlanningCommon


@tagged("post_install", "-at_install")
class TestTaskTemplatesLeftOut(GanttPlanningCommon):
    """Odoo keeps task templates and the sub-tasks under them out of its task lists: so do the
    dashboard, the Gantt chart, its baselines and the Backlog."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Project = cls.env["project.project"]
        cls.department = cls.env["hr.department"].create({"name": "Template Keepers"})
        cls.employee_dev2.write({"parent_id": cls.employee_dev1.id, "department_id": cls.department.id})
        assignee = {"user_ids": [Command.set(cls.user_dev2.ids)]}
        cls.real_task = cls._create_task("Real", datetime(2026, 3, 2, 9), datetime(2026, 3, 6, 17), **assignee)
        cls.real_undated_task = cls._create_task("Real undated", **assignee)
        cls.template = cls._create_task("Template", is_template=True, **assignee)
        # Not a template itself: under one, as a template's sub-tasks are.
        cls.template_subtask = cls._create_task(
            "Template sub-task",
            datetime(2026, 3, 9, 9),
            datetime(2026, 3, 13, 17),
            parent_id=cls.template.id,
            **assignee,
        )
        cls.undated_template = cls._create_task("Undated template", is_template=True, **assignee)
        cls.backlog_template = cls._create_task(
            "Backlog template", stage_id=cls.stage_backlog.id, is_template=True, **assignee
        )
        cls.templates = cls.template | cls.template_subtask | cls.undated_template | cls.backlog_template

    def _dev2_card(self, payload):
        card = next(member for member in payload["members"] if member["user_id"] == self.user_dev2.id)
        card_tasks = [card["current_task"], *card["queue"], *card["backlog"], *card["other"]]
        return {task["id"] for task in card_tasks if task}, card["total_open_tasks"]

    def _assert_dev2_card_has_only_real_tasks(self, payload):
        task_ids, open_task_count = self._dev2_card(payload)
        self.assertEqual(task_ids, {self.real_task.id, self.real_undated_task.id})
        self.assertEqual(open_task_count, 2)

    def test_the_template_fixtures_are_templates(self):
        self.assertTrue(all(self.templates.mapped("has_template_ancestor")))
        self.assertFalse(self.template_subtask.is_template)

    def test_the_project_mode_leaves_template_tasks_out(self):
        self._assert_dev2_card_has_only_real_tasks(self.Project.get_team_dashboard_data(self.project.id))

    def test_the_manager_mode_leaves_template_tasks_out(self):
        self._assert_dev2_card_has_only_real_tasks(self.Project.get_team_dashboard_by_manager(self.user_dev1.id))

    def test_the_department_mode_leaves_template_tasks_out(self):
        self._assert_dev2_card_has_only_real_tasks(self.Project.get_team_dashboard_by_department(self.department.id))

    def test_a_template_and_the_sub_task_it_is_the_ancestor_of_are_not_on_the_gantt_chart(self):
        for show_completed in (False, True):
            with self.subTest(show_completed=show_completed):
                payload = self.Project.get_gantt_chart_data(
                    project_ids=[self.project.id], show_completed=show_completed
                )
                chart_task_ids = {task["id"] for task in payload["tasks"]}
                self.assertIn(self.real_task.id, chart_task_ids)
                self.assertFalse(chart_task_ids & set(self.templates.ids))

    def test_the_unscheduled_tasks_and_their_count_leave_template_tasks_out(self):
        payload = self.Project.get_gantt_chart_data(project_ids=[self.project.id])
        self.assertEqual([task["id"] for task in payload["unscheduled"]], self.real_undated_task.ids)
        self.assertEqual(payload["tasks_without_dates"], 1)

    def test_a_saved_baseline_has_no_line_for_a_template_task(self):
        self.project.with_user(self.user_manager).save_gantt_baselines()
        baseline_tasks = self.project.baseline_id.line_ids.task_id
        self.assertIn(self.real_task, baseline_tasks)
        self.assertFalse(baseline_tasks & self.templates)

    def test_the_backlog_leaves_template_tasks_out(self):
        backlog_task = self._create_task("Backlog", stage_id=self.stage_backlog.id)
        backlog_domain = safe_eval(self.env["project.task"].action_open_backlog()["domain"])
        backlog_tasks = self.env["project.task"].search(backlog_domain)
        self.assertIn(backlog_task, backlog_tasks)
        self.assertNotIn(self.backlog_template, backlog_tasks)

    def test_the_manager_and_department_modes_leave_the_tasks_of_a_project_template_out(self):
        project_template = self.Project.create({"name": "Project Template", "is_template": True})
        self._create_task(
            "In a project template", project_id=project_template.id, user_ids=[Command.set(self.user_dev2.ids)]
        )

        for mode, payload in (
            ("manager", self.Project.get_team_dashboard_by_manager(self.user_dev1.id)),
            ("department", self.Project.get_team_dashboard_by_department(self.department.id)),
        ):
            with self.subTest(mode=mode):
                self._assert_dev2_card_has_only_real_tasks(payload)

    def test_a_task_made_from_a_template_is_not_planned(self):
        leaf_template = self._create_task(
            "Dated template", datetime(2026, 3, 2, 9), datetime(2026, 3, 6, 17), is_template=True
        )
        parent_template = self._create_task("Template with a step", is_template=True)
        self._create_task("Dated step", datetime(2026, 3, 3, 9), datetime(2026, 3, 4, 17), parent_id=parent_template.id)

        for template in (leaf_template, parent_template):
            with self.subTest(template=template.name):
                task = self.Task.browse(template.action_create_from_template())
                self.assertFalse(task.has_template_ancestor)
                self.assertEqual(len(task.child_ids), len(template.child_ids))
                for made_task in task | task.child_ids:
                    self.assertFalse(made_task.planned_date_start, made_task.name)
                    self.assertFalse(made_task.planned_date_end, made_task.name)
