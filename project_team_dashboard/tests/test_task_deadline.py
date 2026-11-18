import importlib.util
import os
from datetime import date, datetime

from odoo.tests.common import tagged

from ..hooks import post_init_hook
from .common import TeamDashboardCommon

# 01:30 on 3 March for a user eight hours ahead of UTC
EVENING_UTC = datetime(2026, 3, 2, 17, 30)
MIGRATION = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "16.0.1.1.0", "post-migration.py")


def _run_update_migration(cr):
    spec = importlib.util.spec_from_file_location("project_team_dashboard_post_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.migrate(cr, "16.0.1.0.0")


@tagged("post_install", "-at_install")
class TestTaskDeadline(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        (cls.project | cls.project_plain).allow_subtasks = True
        planner_groups = ["base.group_user", "project.group_project_user"]
        cls.planner_utc_plus_8, cls.planner_utc, cls.planner_without_tz = cls.env["res.users"].create(
            [
                dict(cls._user_vals("Planner Plus Eight", planner_groups), tz="Australia/Perth"),
                dict(cls._user_vals("Planner UTC", planner_groups), tz="UTC"),
                dict(cls._user_vals("Planner Without Zone", planner_groups), tz=False),
            ]
        )

    def _create_task(self, name, project=None, **vals):
        task_vals = {
            "name": name,
            "project_id": (project or self.project).id,
            "stage_id": self.stage_in_progress.id,
        }
        task_vals.update(vals)
        return self.Task.create(task_vals)

    def test_deadline_is_the_date_of_the_planned_end_for_the_user_who_saves(self):
        for planner, deadline in (
            (self.planner_utc_plus_8, date(2026, 3, 3)),
            (self.planner_utc, date(2026, 3, 2)),
            (self.planner_without_tz, date(2026, 3, 2)),
        ):
            with self.subTest(planner=planner.name):
                task = self._create_task("Planned by %s" % planner.name).with_user(planner)

                task.planned_date_end = EVENING_UTC

                self.assertEqual(task.date_deadline, deadline)

    def test_create_with_a_planned_end_sets_the_deadline(self):
        task = self.Task.with_user(self.planner_utc_plus_8).create(
            {"name": "Planned", "project_id": self.project.id, "planned_date_end": EVENING_UTC}
        )
        self.assertEqual(task.date_deadline, date(2026, 3, 3))

    def test_a_deadline_saved_with_the_planned_end_is_kept(self):
        task = self._create_task("Planned", planned_date_end=EVENING_UTC, date_deadline=date(2026, 4, 1))
        self.assertEqual(task.date_deadline, date(2026, 4, 1))

        task.write({"planned_date_end": datetime(2026, 3, 9, 9), "date_deadline": date(2026, 4, 2)})

        self.assertEqual(task.date_deadline, date(2026, 4, 2))

    def test_the_deadline_alone_does_not_move_the_plan(self):
        task = self._create_task("Planned", planned_date_start=datetime(2026, 3, 1, 9), planned_date_end=EVENING_UTC)

        task.date_deadline = date(2026, 4, 1)

        self.assertEqual(task.date_deadline, date(2026, 4, 1))
        self.assertEqual((task.planned_date_start, task.planned_date_end), (datetime(2026, 3, 1, 9), EVENING_UTC))

    def test_the_deadline_outside_ai_driven_projects_does_not_follow(self):
        undated = self._create_task("Undated", project=self.project_plain, planned_date_end=EVENING_UTC)
        dated = self._create_task(
            "Dated", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=date(2026, 4, 1)
        )

        dated.planned_date_end = datetime(2026, 3, 9, 9)

        self.assertFalse(undated.date_deadline)
        self.assertEqual(dated.date_deadline, date(2026, 4, 1))

    def test_a_rolled_up_parent_follows_its_planned_end_at_every_level(self):
        grandparent = self._create_task("Grandparent", date_deadline=date(2026, 6, 30))
        parent = self._create_task("Parent", parent_id=grandparent.id, date_deadline=date(2026, 6, 30))
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 9),
        )
        self.assertEqual((parent | grandparent).mapped("date_deadline"), [date(2026, 3, 6)] * 2)

        subtask.with_user(self.planner_utc_plus_8).planned_date_end = datetime(2026, 3, 9, 17, 30)

        self.assertEqual(grandparent.planned_date_end, subtask.planned_date_end)
        self.assertEqual(
            (parent | grandparent).with_user(self.planner_utc_plus_8).mapped("date_deadline"),
            [date(2026, 3, 10)] * 2,
        )

    def test_clearing_the_planned_end_keeps_the_deadline(self):
        task = self._create_task("Planned", planned_date_end=EVENING_UTC)
        parent = self._create_task("Parent")
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 9),
        )
        self.assertEqual((task | parent).mapped("date_deadline"), [date(2026, 3, 2), date(2026, 3, 6)])

        task.planned_date_end = False
        subtask.write({"planned_date_start": False, "planned_date_end": False})

        self.assertFalse(parent.planned_date_end)
        self.assertEqual((task | parent).mapped("date_deadline"), [date(2026, 3, 2), date(2026, 3, 6)])

    def test_a_task_moved_into_an_ai_driven_project_aligns_its_deadline(self):
        task = self._create_task(
            "Moving", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=date(2026, 4, 1)
        )

        task.with_user(self.planner_utc_plus_8).project_id = self.project

        self.assertEqual(task.with_user(self.planner_utc_plus_8).date_deadline, date(2026, 3, 3))

        task.date_deadline = date(2026, 4, 1)
        task.project_id = self.project_plain

        self.assertEqual(task.date_deadline, date(2026, 4, 1))

    def test_flagging_a_project_leaves_the_deadlines_of_its_tasks(self):
        task = self._create_task(
            "Planned", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=date(2026, 4, 1)
        )

        self.project_plain.is_ai_driven = True

        self.assertEqual(task.date_deadline, date(2026, 4, 1))

    def _write_in_sql(self, tasks, **values):
        self.env.flush_all()
        assignments = ", ".join("%s = %%s" % column for column in values)
        self.env.cr.execute(
            "UPDATE project_task SET %s WHERE id IN %%s" % assignments, (*values.values(), tuple(tasks.ids))
        )
        self.env.invalidate_all()

    def _write_dates(self, tasks):
        self.env.cr.execute("SELECT id, write_date FROM project_task WHERE id IN %s", (tuple(tasks.ids),))
        return dict(self.env.cr.fetchall())

    def test_install_and_update_align_the_open_tasks_of_ai_driven_projects(self):
        stale_deadline = date(2026, 6, 30)
        for name, align in (
            ("install", lambda: post_init_hook(self.env.cr, self.env.registry)),
            ("update", lambda: _run_update_migration(self.env.cr)),
        ):
            with self.subTest(name):
                open_task = self._create_task("Open", planned_date_end=EVENING_UTC)
                undated_task = self._create_task("Without Deadline", planned_date_end=EVENING_UTC)
                closed_task = self._create_task("Closed", planned_date_end=EVENING_UTC, stage_id=self.stage_done.id)
                plain_task = self._create_task("Plain", project=self.project_plain, planned_date_end=EVENING_UTC)
                unplanned_task = self._create_task("Unplanned")
                tasks = open_task | undated_task | closed_task | plain_task | unplanned_task
                self._write_in_sql(tasks, date_deadline=stale_deadline, write_date=datetime(2026, 1, 5, 8))
                self._write_in_sql(open_task, write_uid=self.planner_utc_plus_8.id)
                self._write_in_sql(undated_task, write_uid=self.planner_without_tz.id, date_deadline=None)
                write_dates = self._write_dates(tasks)

                align()

                self.assertEqual(
                    tasks.mapped("date_deadline"),
                    [date(2026, 3, 3), date(2026, 3, 2), stale_deadline, stale_deadline, stale_deadline],
                )
                self.assertEqual(self._write_dates(tasks), write_dates)
