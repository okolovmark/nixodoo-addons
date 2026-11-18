import json
from contextlib import contextmanager, nullcontext
from unittest.mock import patch

from lxml import etree

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.project_team_dashboard.models.project_task import PLANNED_DATE_FIELDS
from odoo.addons.project_team_dashboard.models.project_task import Task as DashboardTask
from odoo.addons.project_timeline.models.project_task import ProjectTask as TimelineTask

GLUE_TASK_MODULE = "odoo.addons.project_team_dashboard_timeline.models.project_task"
DASHBOARD_TASK_MODULE = "odoo.addons.project_team_dashboard.models.project_task"
DASHBOARD_VIEWS = (
    "project_team_dashboard.view_task_form2_planned_dates",
    "project_team_dashboard.view_task_tree2_planned_dates",
)
READ_ONLY_ON_ROLLED_UP_TASKS = [["is_planned_from_subtasks", "=", True]]


class DashboardComputeReached(Exception):
    pass


@tagged("post_install", "-at_install")
class TestPlannedDates(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]

    def _arch(self, view_xmlid, view_type):
        return etree.fromstring(self.Task.get_view(self.env.ref(view_xmlid).id, view_type)["arch"])

    def _assert_form_shows_the_planned_dates_of_project_timeline_once(self):
        arch = self._arch("project.view_task_form2", "form")
        self.assertFalse(arch.xpath("//*[@name='planned_dates']"))
        self.assertEqual(len(arch.xpath("//label[@for='planned_date_start']")), 1)
        for field_name in PLANNED_DATE_FIELDS:
            nodes = arch.xpath("//field[@name='%s'][not(ancestor::field)]" % field_name)
            self.assertEqual(len(nodes), 1, field_name)
            self.assertEqual(json.loads(nodes[0].get("modifiers", "{}")).get("readonly"), READ_ONLY_ON_ROLLED_UP_TASKS)

    def _assert_list_shows_the_planned_dates_of_project_timeline_once(self):
        arch = self._arch("project.view_task_tree2", "tree")
        for field_name in PLANNED_DATE_FIELDS:
            nodes = arch.xpath("//field[@name='%s']" % field_name)
            self.assertEqual(len(nodes), 1, field_name)
            self.assertEqual(nodes[0].get("optional"), "hide")
            self.assertEqual(json.loads(nodes[0].get("modifiers", "{}")).get("readonly"), READ_ONLY_ON_ROLLED_UP_TASKS)
        self.assertEqual(len(arch.xpath("//field[@name='is_planned_from_subtasks']")), 1)

    @contextmanager
    def _project_timeline_loaded_last(self):
        """Order the task classes as the process that installs project_timeline next to the dashboard does."""
        task_class = type(self.Task)
        loaded_bases = task_class.__bases__
        bases = list(loaded_bases)
        dashboard_index, timeline_index = bases.index(DashboardTask), bases.index(TimelineTask)
        bases[dashboard_index], bases[timeline_index] = TimelineTask, DashboardTask
        task_class.__bases__ = tuple(bases)
        try:
            yield
        finally:
            task_class.__bases__ = loaded_bases

    def test_the_roll_up_of_the_dashboard_computes_the_planned_dates_whatever_the_load_order(self):
        task_class = type(self.Task)
        for field_name in PLANNED_DATE_FIELDS:
            field = self.Task._fields[field_name]
            self.assertEqual(getattr(task_class, field.compute).__module__, GLUE_TASK_MODULE, field_name)
            self.assertCountEqual(
                self.Task.pool.field_depends[field],
                getattr(DashboardTask, field.compute)._depends + getattr(TimelineTask, field.compute)._depends,
                field_name,
            )
            self.assertTrue(field.store and field.recursive and not field.readonly, field_name)
        checks = [check for check in self.Task._constraint_methods if check.__name__ == "_check_planned_dates"]
        self.assertEqual([check.__module__ for check in checks], [DASHBOARD_TASK_MODULE])

    def test_the_computes_run_the_computes_of_the_dashboard_in_either_load_order(self):
        task = self.Task.create({"name": "Leaf"})
        for load_order in (nullcontext, self._project_timeline_loaded_last):
            for field_name in PLANNED_DATE_FIELDS:
                field = self.Task._fields[field_name]
                # assertRaises flushes on entering: the patch comes after it, so that only the read computes
                with (
                    self.subTest(load_order=load_order.__name__, field=field_name),
                    load_order(),
                    self.assertRaises(DashboardComputeReached),
                    patch.object(DashboardTask, field.compute, side_effect=DashboardComputeReached),
                ):
                    self.env.add_to_compute(field, task)
                    task[field_name]
                self.env.remove_to_compute(field, task)

    def test_the_form_shows_the_planned_dates_of_project_timeline_once(self):
        self._assert_form_shows_the_planned_dates_of_project_timeline_once()

    def test_the_list_shows_the_planned_dates_of_project_timeline_once(self):
        self._assert_list_shows_the_planned_dates_of_project_timeline_once()

    def test_the_planned_dates_show_once_whichever_views_apply_first(self):
        # project_timeline's views keep the default priority 16: 1 applies the dashboard's before, 99 after
        for priority in (1, 99):
            with self.subTest(priority=priority):
                for xmlid in DASHBOARD_VIEWS:
                    self.env.ref(xmlid).priority = priority

                self._assert_form_shows_the_planned_dates_of_project_timeline_once()
                self._assert_list_shows_the_planned_dates_of_project_timeline_once()
