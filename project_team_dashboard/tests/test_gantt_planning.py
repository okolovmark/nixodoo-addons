from datetime import date, datetime

import psycopg2

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import TeamDashboardCommon


class GanttPlanningCommon(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.Lag = cls.env["project.task.dependency.lag"]
        cls.project.allow_task_dependencies = True
        cls.task_a = cls._create_task("Design", datetime(2026, 3, 2, 9), datetime(2026, 3, 6, 17))
        cls.task_b = cls._create_task("Build", datetime(2026, 3, 9, 9), datetime(2026, 3, 13, 17))
        cls.task_c = cls._create_task("Test", datetime(2026, 3, 16, 9), datetime(2026, 3, 18, 17))
        cls.user_floor = cls.env["res.users"].create(
            {
                "name": "Floor Planner",
                "login": "floor_planner_test_sw",
                "email": "floor.planner@test.com",
                "groups_id": [Command.set([cls.env.ref("base.group_user").id])],
            },
        )
        cls.user_manager = cls.env["res.users"].create(
            {
                "name": "Project Admin",
                "login": "project_admin_test_sw",
                "email": "project.admin@test.com",
                "groups_id": [Command.set([cls.env.ref("project.group_project_manager").id])],
            },
        )
        cls._set_own_task_write_policy()

    @classmethod
    def _set_own_task_write_policy(cls):
        """Let every employee write the tasks they are assigned to, and no other: the Gantt chart
        follows whatever task-write policy a database has, and core's lets a follower write."""
        task_model_id = cls.env["ir.model"]._get_id("project.task")
        cls.env["ir.model.access"].create(
            {
                "name": "project.task employee write",
                "model_id": task_model_id,
                "group_id": cls.env.ref("base.group_user").id,
                "perm_read": True,
                "perm_write": True,
            }
        )
        # A global rule: it is AND-ed with the group rules that grant a follower write.
        cls.env["ir.rule"].create(
            {
                "name": "Task: employees write only their own tasks",
                "model_id": task_model_id,
                "domain_force": "[('user_ids', 'in', user.id)]",
                "perm_read": False,
                "perm_write": True,
                "perm_create": False,
                "perm_unlink": False,
            }
        )

    @classmethod
    def _create_task(cls, name, date_start=False, date_end=False, **vals):
        task_vals = {
            "name": name,
            "project_id": cls.project.id,
            "stage_id": cls.stage_in_progress.id,
            "planned_date_start": date_start,
            "planned_date_end": date_end,
        }
        task_vals.update(vals)
        return cls.env["project.task"].create(task_vals)

    @classmethod
    def _create_followers_project(cls, name):
        project = cls.env["project.project"].create(
            {
                "name": name,
                "privacy_visibility": "followers",
                "allow_task_dependencies": True,
                "type_ids": [Command.set([cls.stage_in_progress.id, cls.stage_done.id])],
            },
        )
        project.message_unsubscribe(partner_ids=[cls.user_dev1.partner_id.id, cls.user_floor.partner_id.id])
        return project

    def _lag(self, task, predecessor):
        return self.Lag.search([("task_id", "=", task.id), ("depends_on_id", "=", predecessor.id)])


@tagged("post_install", "-at_install")
class TestDependencyLag(GanttPlanningCommon):
    def test_lag_pair_is_unique(self):
        self.task_b.depend_on_ids = [Command.link(self.task_a.id)]
        self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": 1})
        with self.assertRaises(psycopg2.IntegrityError), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": 2})

    def test_check_lag_days_refuses_negative(self):
        self.task_b.depend_on_ids = [Command.link(self.task_a.id)]
        with self.assertRaises(ValidationError):
            self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": -1})
        draft_lag = self.Lag.new({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": -1})
        with self.assertRaises(ValidationError):
            draft_lag._check_lag_days()
        draft_lag.lag_days = 0
        draft_lag._check_lag_days()

    def test_check_dependency_exists_refuses_missing_dependency(self):
        with self.assertRaises(ValidationError):
            self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": 1})
        draft_lag = self.Lag.new({"task_id": self.task_c.id, "depends_on_id": self.task_a.id, "lag_days": 1})
        with self.assertRaises(ValidationError):
            draft_lag._check_dependency_exists()
        self.task_c.depend_on_ids = [Command.link(self.task_a.id)]
        lag = self.Lag.create({"task_id": self.task_c.id, "depends_on_id": self.task_a.id, "lag_days": 1})
        lag._check_dependency_exists()

    def test_check_dependency_exists_refuses_task_itself(self):
        with self.assertRaises(ValidationError):
            self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_b.id, "lag_days": 1})
        draft_lag = self.Lag.new({"task_id": self.task_b.id, "depends_on_id": self.task_b.id, "lag_days": 1})
        with self.assertRaises(ValidationError):
            draft_lag._check_dependency_exists()

    def test_lag_to_archived_predecessor_survives_other_dependency_edits(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 3)
        self.task_a.active = False
        self.task_b.gantt_add_dependency(self.task_c.id)
        self.assertEqual(self._lag(self.task_b, self.task_a).lag_days, 3)

    def test_project_user_has_no_direct_access_to_lags(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 2)
        lag_as_dev = self.Lag.with_user(self.user_dev1)
        with self.assertRaises(AccessError):
            lag_as_dev.search([])
        with self.assertRaises(AccessError):
            lag_as_dev.create({"task_id": self.task_c.id, "depends_on_id": self.task_a.id, "lag_days": 1})

    def test_project_copy_copies_lags(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 3)
        project_copy = self.project.copy()
        copied_tasks = self.Task.search([("project_id", "=", project_copy.id)])
        copied_a = copied_tasks.filtered(lambda task: task.name == "Design")
        copied_b = copied_tasks.filtered(lambda task: task.name == "Build")
        self.assertEqual(copied_b.depend_on_ids, copied_a)
        self.assertEqual(self._lag(copied_b, copied_a).lag_days, 3)
        self.assertEqual(self._lag(self.task_b, self.task_a).lag_days, 3)

    def test_task_copy_copies_lags_on_both_sides(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 2)
        self.task_c.gantt_add_dependency(self.task_b.id)
        self.task_c.gantt_set_dependency_lag(self.task_b.id, 4)
        task_b_copy = self.task_b.copy()
        self.assertEqual(self._lag(task_b_copy, self.task_a).lag_days, 2)
        self.assertEqual(self._lag(self.task_c, task_b_copy).lag_days, 4)
        self.assertEqual(self._lag(self.task_c, self.task_b).lag_days, 4)

    def test_lag_deleted_with_predecessor(self):
        self.task_b.depend_on_ids = [Command.link(self.task_a.id)]
        lag = self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": 2})
        self.task_a.unlink()
        self.assertFalse(lag.exists())

    def test_lag_pruned_when_dependency_removed(self):
        self.task_c.depend_on_ids = [Command.set([self.task_a.id, self.task_b.id])]
        lag_a = self.Lag.create({"task_id": self.task_c.id, "depends_on_id": self.task_a.id, "lag_days": 1})
        lag_b = self.Lag.create({"task_id": self.task_c.id, "depends_on_id": self.task_b.id, "lag_days": 2})
        self.task_c.write({"depend_on_ids": [Command.unlink(self.task_a.id)]})
        self.assertFalse(lag_a.exists(), "The lag of a removed dependency must be deleted")
        self.assertTrue(lag_b.exists(), "The lag of a kept dependency must survive")

    def test_lag_pruned_when_dependency_removed_from_predecessor_side(self):
        self.task_b.depend_on_ids = [Command.link(self.task_a.id)]
        lag = self.Lag.create({"task_id": self.task_b.id, "depends_on_id": self.task_a.id, "lag_days": 1})
        self.task_a.write({"dependent_ids": [Command.clear()]})
        self.assertFalse(lag.exists())


@tagged("post_install", "-at_install")
class TestGanttDependencyHelpers(GanttPlanningCommon):
    def test_add_dependency_links_predecessor(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.assertEqual(self.task_b.depend_on_ids, self.task_a)

    def test_add_dependency_refuses_cycle(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_c.gantt_add_dependency(self.task_b.id)
        with self.assertRaises(ValidationError):
            self.task_a.gantt_add_dependency(self.task_c.id)

    def test_remove_dependency_unlinks_and_drops_lag(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 3)
        self.task_b.gantt_remove_dependency(self.task_a.id)
        self.assertFalse(self.task_b.depend_on_ids)
        self.assertFalse(self._lag(self.task_b, self.task_a))

    def test_set_lag_creates_updates_and_clears(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 2)
        self.assertEqual(self._lag(self.task_b, self.task_a).lag_days, 2)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 5)
        self.assertEqual(self._lag(self.task_b, self.task_a).lag_days, 5)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 0)
        self.assertFalse(self._lag(self.task_b, self.task_a), "A zero lag is stored as no row")

    def test_set_lag_refuses_negative(self):
        self.task_b.gantt_add_dependency(self.task_a.id)
        with self.assertRaises(ValidationError):
            self.task_b.gantt_set_dependency_lag(self.task_a.id, -2)

    def test_set_lag_requires_existing_dependency(self):
        with self.assertRaises(UserError):
            self.task_b.gantt_set_dependency_lag(self.task_a.id, 2)

    def test_helpers_denied_without_write_access(self):
        followers_project = self._create_followers_project("Followers Only")
        predecessor = self._create_task("Hidden Design", project_id=followers_project.id)
        successor = self._create_task("Hidden Build", project_id=followers_project.id)
        successor.gantt_add_dependency(predecessor.id)
        successor.message_subscribe(partner_ids=self.user_dev1.partner_id.ids)
        predecessor.message_subscribe(partner_ids=self.user_dev1.partner_id.ids)
        successor_as_dev = successor.with_user(self.user_dev1)
        self.assertEqual(successor_as_dev.depend_on_ids, predecessor, "The project user can read the dependency")
        with self.assertRaises(AccessError):
            successor_as_dev.gantt_remove_dependency(predecessor.id)
        with self.assertRaises(AccessError):
            successor_as_dev.gantt_set_dependency_lag(predecessor.id, 1)
        self.assertFalse(self._lag(successor, predecessor))
        with self.assertRaises(AccessError):
            self.task_c.with_user(self.user_floor).gantt_add_dependency(self.task_b.id)

    def test_internal_user_sets_lag_on_own_task(self):
        own_task = self._create_task("Own Lag", user_ids=[Command.set([self.user_floor.id])])
        own_task_as_floor = own_task.with_user(self.user_floor)
        own_task_as_floor.gantt_add_dependency(self.task_a.id)
        own_task_as_floor.gantt_set_dependency_lag(self.task_a.id, 2)
        self.assertEqual(self._lag(own_task, self.task_a).lag_days, 2)
        own_task_as_floor.gantt_set_dependency_lag(self.task_a.id, 0)
        self.assertFalse(self._lag(own_task, self.task_a))

    def test_add_dependency_refused_when_project_disallows_dependencies(self):
        self.project_plain.allow_task_dependencies = False
        foreign_task = self._create_task("Foreign", project_id=self.project_plain.id)
        with self.assertRaises(UserError):
            self.task_b.gantt_add_dependency(foreign_task.id)
        with self.assertRaises(UserError):
            foreign_task.gantt_add_dependency(self.task_b.id)
        self.assertFalse(self.task_b.depend_on_ids)

    def test_add_dependency_refused_without_project(self):
        orphan_task = self.Task.create({"name": "No Project"})
        with self.assertRaises(UserError):
            self.task_b.gantt_add_dependency(orphan_task.id)
        with self.assertRaises(UserError):
            orphan_task.gantt_add_dependency(self.task_b.id)
        self.assertFalse(self.task_b.depend_on_ids)
        self.assertFalse(orphan_task.depend_on_ids)

    def test_add_dependency_refused_on_unreadable_predecessor(self):
        followers_project = self._create_followers_project("Followers Hidden")
        hidden_task = self._create_task("Hidden", project_id=followers_project.id)
        own_task = self._create_task("Own Dev", user_ids=[Command.set([self.user_dev1.id])])
        with self.assertRaises(AccessError):
            own_task.with_user(self.user_dev1).gantt_add_dependency(hidden_task.id)
        self.assertFalse(own_task.depend_on_ids)

    def test_set_lag_refused_on_unreadable_predecessor(self):
        followers_project = self._create_followers_project("Followers Lag Hidden")
        hidden_task = self._create_task("Hidden Lag", project_id=followers_project.id)
        own_task = self._create_task("Own Lag Dev", user_ids=[Command.set([self.user_dev1.id])])
        own_task.write({"depend_on_ids": [Command.link(hidden_task.id)]})
        with self.assertRaises(AccessError):
            self.assertEqual(own_task.depend_on_ids, hidden_task, "The cached dependency reaches the read check")
            own_task.with_user(self.user_dev1).gantt_set_dependency_lag(hidden_task.id, 2)
        self.assertFalse(self._lag(own_task, hidden_task))

    def test_add_dependency_refused_on_missing_predecessor(self):
        missing_id = self.Task.search([], order="id desc", limit=1).id + 1000
        with self.assertRaises(UserError):
            self.task_b.gantt_add_dependency(missing_id)

    def test_helpers_allowed_on_own_task(self):
        own_task = self._create_task("Own", user_ids=[Command.set([self.user_floor.id])])
        own_task.with_user(self.user_floor).gantt_add_dependency(self.task_a.id)
        self.assertEqual(own_task.depend_on_ids, self.task_a)


@tagged("post_install", "-at_install")
class TestProjectBaseline(GanttPlanningCommon):
    def test_manager_saves_baseline_of_open_tasks(self):
        closed_task = self._create_task(
            "Closed", datetime(2026, 2, 2, 9), datetime(2026, 2, 3, 17), stage_id=self.stage_done.id
        )
        self.project.with_user(self.user_manager).save_gantt_baselines()
        baseline = self.project.baseline_id
        self.assertTrue(baseline)
        self.assertEqual(baseline.user_id, self.user_manager)
        baseline_tasks = baseline.line_ids.task_id
        self.assertIn(self.task_a, baseline_tasks)
        self.assertNotIn(closed_task, baseline_tasks, "Tasks in a folded stage are not in the baseline")
        line_a = baseline.line_ids.filtered(lambda line: line.task_id == self.task_a)
        self.assertEqual(line_a.planned_date_start, self.task_a.planned_date_start)
        self.assertEqual(line_a.planned_date_end, self.task_a.planned_date_end)

    def test_saving_again_replaces_previous_baseline(self):
        self.project.with_user(self.user_manager).save_gantt_baselines()
        first_baseline = self.project.baseline_id
        self.task_a.planned_date_end = datetime(2026, 3, 10, 17)
        self.project.with_user(self.user_manager).save_gantt_baselines()
        self.assertNotEqual(self.project.baseline_id, first_baseline)
        self.assertFalse(first_baseline.exists(), "The previous baseline is deleted")
        line_a = self.project.baseline_id.line_ids.filtered(lambda line: line.task_id == self.task_a)
        self.assertEqual(line_a.planned_date_end, datetime(2026, 3, 10, 17))

    def test_project_user_cannot_save_baseline(self):
        with self.assertRaises(AccessError):
            self.project.with_user(self.user_dev1).save_gantt_baselines()
        self.assertFalse(self.project.baseline_id)

    def test_project_user_cannot_read_baselines(self):
        self.project.with_user(self.user_manager).save_gantt_baselines()
        with self.assertRaises(AccessError):
            self.env["project.baseline.line"].with_user(self.user_dev1).search([])
        with self.assertRaises(AccessError):
            self.env["project.baseline"].with_user(self.user_dev1).search([])

    def test_save_baselines_of_several_projects_in_one_call(self):
        other_task = self._create_task(
            "Other", datetime(2026, 3, 2, 9), datetime(2026, 3, 3, 17), project_id=self.project_plain.id
        )
        projects = self.project | self.project_plain
        projects.with_user(self.user_manager).save_gantt_baselines()
        self.assertEqual(self.project.baseline_id.line_ids.task_id, self.task_a | self.task_b | self.task_c)
        self.assertEqual(self.project_plain.baseline_id.line_ids.task_id, other_task)

    def test_saving_removes_every_older_baseline_of_the_project(self):
        orphan_baseline = self.env["project.baseline"].create({"project_id": self.project.id})
        self.project.with_user(self.user_manager).save_gantt_baselines()
        self.assertFalse(orphan_baseline.exists())
        self.assertEqual(
            self.env["project.baseline"].search([("project_id", "=", self.project.id)]), self.project.baseline_id
        )

    def test_baseline_skips_open_tasks_without_any_date(self):
        undated_task = self._create_task("Undated")
        deadline_task = self._create_task("Deadline Only", date_deadline=date(2026, 3, 25))
        self.project.with_user(self.user_manager).save_gantt_baselines()
        baseline_tasks = self.project.baseline_id.line_ids.task_id
        self.assertNotIn(undated_task, baseline_tasks)
        self.assertIn(deadline_task, baseline_tasks)

    def test_save_baselines_dedupes_repeated_project(self):
        Project = self.env["project.project"].with_user(self.user_manager)
        Project.browse([self.project.id, self.project.id]).save_gantt_baselines()
        self.assertEqual(len(self.env["project.baseline"].search([("project_id", "=", self.project.id)])), 1)
        self.assertEqual(self.project.baseline_id.line_ids.task_id, self.task_a | self.task_b | self.task_c)

    def test_save_baseline_of_unknown_project_is_a_no_op(self):
        missing_id = self.env["project.project"].search([], order="id desc", limit=1).id + 1000
        Project = self.env["project.project"].with_user(self.user_manager)
        self.assertTrue(Project.browse(missing_id).save_gantt_baselines())
        self.assertFalse(self.env["project.baseline"].search([("project_id", "=", missing_id)]))

    def test_manager_cannot_save_baseline_of_another_company(self):
        other_company = self.env["res.company"].create({"name": "Other Baseline Company"})
        other_project = self.env["project.project"].create(
            {"name": "Other Company Project", "company_id": other_company.id}
        )
        self._create_task(
            "Other Company Task", datetime(2026, 3, 2, 9), datetime(2026, 3, 3, 17), project_id=other_project.id
        )
        other_baseline = self.env["project.baseline"].create({"project_id": other_project.id})
        other_project.baseline_id = other_baseline
        self.assertNotIn(other_company, self.user_manager.company_ids)
        with self.assertRaises(AccessError):
            other_project.with_user(self.user_manager).save_gantt_baselines()
        self.assertEqual(other_project.baseline_id, other_baseline)
        self.assertTrue(other_baseline.exists(), "The other company's baseline is untouched")

    def test_check_baseline_project_refuses_foreign_baseline(self):
        self.project_plain.with_user(self.user_manager).save_gantt_baselines()
        with self.assertRaises(ValidationError):
            self.project.baseline_id = self.project_plain.baseline_id
        draft_project = self.env["project.project"].new(
            {"name": "Draft Baseline Holder", "baseline_id": self.project_plain.baseline_id.id}
        )
        with self.assertRaises(ValidationError):
            draft_project._check_baseline_project()
        self.project_plain._check_baseline_project()


@tagged("post_install", "-at_install")
class TestGanttColumnsPreference(GanttPlanningCommon):
    def test_new_user_gets_default_columns(self):
        self.assertEqual(
            self.user_dev1._get_gantt_columns(),
            ["assignees", "deadline", "planned_hours", "spent_hours"],
        )

    def test_plain_user_writes_own_columns(self):
        self.user_floor.with_user(self.user_floor).write({"gantt_columns": "stage,unknown,assignees"})
        self.assertEqual(self.user_floor._get_gantt_columns(), ["assignees", "stage"])

    def test_unchecking_every_column_is_kept(self):
        self.user_floor.with_user(self.user_floor).write({"gantt_columns": ""})
        self.assertEqual(self.user_floor._get_gantt_columns(), [])


@tagged("post_install", "-at_install")
class TestGanttPlanningPayload(GanttPlanningCommon):
    def _payload(self, user=None):
        Project = self.env["project.project"]
        if user:
            Project = Project.with_user(user)
        return Project.get_gantt_chart_data(project_ids=[self.project.id])

    def _task_data(self, payload, task):
        return next(task_data for task_data in payload["tasks"] if task_data["id"] == task.id)

    def test_top_level_keys(self):
        self.user_floor.with_user(self.user_floor).write({"gantt_columns": "stage"})
        payload = self._payload(self.user_floor)
        self.assertEqual(payload["columns"], ["stage"])
        self.assertFalse(payload["is_project_manager"])
        self.assertTrue(self._payload(self.user_manager)["is_project_manager"])

    def test_task_attributes_and_lags(self):
        self.task_b.date_deadline = date(2026, 3, 20)
        self.task_b.gantt_add_dependency(self.task_a.id)
        self.task_b.gantt_set_dependency_lag(self.task_a.id, 4)
        task_b_data = self._task_data(self._payload(), self.task_b)
        self.assertEqual(task_b_data["depend_on_lags"], {self.task_a.id: 4})
        self.assertEqual(task_b_data["date_deadline"], "2026-03-20")
        self.assertEqual(task_b_data["plan_date_start"], "2026-03-09")
        self.assertEqual(task_b_data["plan_date_end"], "2026-03-13")
        self.assertIn("effective_hours", task_b_data)
        self.assertEqual(task_b_data["project_id"], self.project.id)

    def test_can_write_follows_record_rules(self):
        own_task = self._create_task(
            "Floor Own", datetime(2026, 3, 2, 9), datetime(2026, 3, 3, 17), user_ids=[Command.set([self.user_floor.id])]
        )
        payload = self._payload(self.user_floor)
        self.assertTrue(self._task_data(payload, own_task)["can_write"])
        self.assertFalse(self._task_data(payload, self.task_a)["can_write"])

    def test_baseline_dates_and_shift(self):
        self.project.with_user(self.user_manager).save_gantt_baselines()
        self.task_a.planned_date_end = datetime(2026, 3, 9, 17)
        task_a_data = self._task_data(self._payload(), self.task_a)
        self.assertEqual(task_a_data["baseline_date_start"], "2026-03-02 09:00:00")
        self.assertEqual(task_a_data["baseline_date_end"], "2026-03-06 17:00:00")
        self.assertEqual(task_a_data["baseline_shift_days"], 3)

    def test_task_moved_to_another_project_drops_the_old_baseline(self):
        projects = self.project | self.project_plain
        projects.with_user(self.user_manager).save_gantt_baselines()
        self.task_a.write({"project_id": self.project_plain.id, "stage_id": self.stage_in_progress.id})
        payload = self.env["project.project"].get_gantt_chart_data(project_ids=projects.ids, show_completed=True)
        task_a_data = self._task_data(payload, self.task_a)
        self.assertIs(task_a_data["baseline_date_start"], False)
        self.assertIs(task_a_data["baseline_shift_days"], False)

    def test_task_absent_from_baseline_has_no_baseline_dates(self):
        self.project.with_user(self.user_manager).save_gantt_baselines()
        late_task = self._create_task("Late", datetime(2026, 4, 1, 9), datetime(2026, 4, 2, 17))
        late_task_data = self._task_data(self._payload(), late_task)
        self.assertIs(late_task_data["baseline_date_start"], False)
        self.assertIs(late_task_data["baseline_shift_days"], False)

    def test_deadline_only_task_is_a_milestone_at_the_deadline(self):
        deadline_task = self._create_task("Deadline Only", date_deadline=date(2026, 3, 25))
        payload = self._payload()
        deadline_data = self._task_data(payload, deadline_task)
        self.assertTrue(deadline_data["is_deadline_only"])
        self.assertEqual(deadline_data["planned_date_start"][:10], "2026-03-25")
        self.assertFalse(deadline_data["plan_date_start"], "Deadline-only tasks stay out of the lag check")
        self.assertNotIn(deadline_task.id, [entry["id"] for entry in payload["unscheduled"]])
        self.assertFalse(self._task_data(payload, self.task_a)["is_deadline_only"])

    def test_unscheduled_lists_open_undated_tasks(self):
        undated_task = self._create_task("Undated", planned_hours=6.0)
        closed_undated_task = self._create_task("Closed Undated", stage_id=self.stage_done.id)
        payload = self._payload()
        unscheduled_by_id = {entry["id"]: entry for entry in payload["unscheduled"]}
        self.assertIn(undated_task.id, unscheduled_by_id)
        self.assertNotIn(closed_undated_task.id, unscheduled_by_id)
        self.assertNotIn(self.task_a.id, unscheduled_by_id)
        self.assertEqual(unscheduled_by_id[undated_task.id]["planned_hours"], 6.0)
        self.assertEqual(unscheduled_by_id[undated_task.id]["stage_name"], "In Progress")

    def test_no_dates_count_matches_unscheduled_and_skips_deadline_only(self):
        self._create_task("Deadline Only", date_deadline=date(2026, 3, 25))
        self._create_task("Undated")
        payload = self._payload()
        self.assertEqual(payload["tasks_without_dates"], 1)
        self.assertEqual(payload["tasks_without_dates"], len(payload["unscheduled"]))

    def test_unscheduled_can_write_follows_record_rules(self):
        own_undated = self._create_task("Own Undated", user_ids=[Command.set([self.user_floor.id])])
        other_undated = self._create_task("Other Undated")
        unscheduled_by_id = {entry["id"]: entry for entry in self._payload(self.user_floor)["unscheduled"]}
        self.assertIs(unscheduled_by_id[own_undated.id]["can_write"], True)
        self.assertIs(unscheduled_by_id[other_undated.id]["can_write"], False)

    def test_unreadable_parent_is_left_out_but_labels_its_sub_task(self):
        followers_project = self._create_followers_project("Hidden Parent Project")
        hidden_parent = self._create_task("Hidden Parent", project_id=followers_project.id)
        hidden_parent.key = "HID-1"
        visible_child = self._create_task(
            "Visible Child",
            datetime(2026, 3, 2, 9),
            datetime(2026, 3, 3, 17),
            parent_id=hidden_parent.id,
            project_id=self.project.id,
            display_project_id=self.project.id,
        )
        self.assertEqual(visible_child.project_id, self.project)
        self.assertFalse(hidden_parent.with_user(self.user_dev1)._filter_access_rules("read"))
        self.env.invalidate_all()
        payload = self._payload(self.user_dev1)
        task_ids = [task_data["id"] for task_data in payload["tasks"]]
        self.assertIn(visible_child.id, task_ids)
        self.assertNotIn(hidden_parent.id, task_ids)
        child_data = next(task_data for task_data in payload["tasks"] if task_data["id"] == visible_child.id)
        self.assertIs(child_data["parent_id"], False, "The sub-task row links no parent the user cannot open")
        self.assertEqual(child_data["parent_label"], "HID-1")
        hidden_parent.key = False
        self.env.invalidate_all()
        unkeyed_child_data = next(
            task_data for task_data in self._payload(self.user_dev1)["tasks"] if task_data["id"] == visible_child.id
        )
        self.assertEqual(unkeyed_child_data["parent_label"], "Hidden Parent")
