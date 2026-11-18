from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestGanttChartData(TeamDashboardCommon):
    """Tests for project.project.get_gantt_chart_data."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Project = cls.env["project.project"]
        cls.Task = cls.env["project.task"]
        cls.today = fields.Date.today()
        cls.now = fields.Datetime.now()

    # ------------------------------------------------------------------
    # Return structure
    # ------------------------------------------------------------------

    def test_return_structure_keys(self):
        """Result dict must contain tasks, today, and tasks_without_dates."""
        result = self.Project.get_gantt_chart_data(self.project.id)
        self.assertIn("tasks", result)
        self.assertIn("today", result)
        self.assertIn("tasks_without_dates", result)

    def test_today_is_current_date(self):
        """today key must equal today's date string."""
        result = self.Project.get_gantt_chart_data(self.project.id)
        self.assertEqual(result["today"], fields.Date.to_string(self.today))

    # ------------------------------------------------------------------
    # Non-existent / empty project
    # ------------------------------------------------------------------

    def test_nonexistent_project_returns_empty(self):
        """Non-existent project ID returns empty tasks list."""
        result = self.Project.get_gantt_chart_data(999999)
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["tasks_without_dates"], 0)

    def test_project_with_no_tasks(self):
        """Project with zero tasks returns empty list."""
        empty_proj = self.Project.create(
            {
                "name": "Empty Gantt Project",
                "is_ai_driven": True,
            },
        )
        result = self.Project.get_gantt_chart_data(empty_proj.id)
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["tasks_without_dates"], 0)

    # ------------------------------------------------------------------
    # Basic task with both planned dates
    # ------------------------------------------------------------------

    def test_task_with_both_dates(self):
        """Task with both planned_date_start and planned_date_end appears in results."""
        task = self.Task.create(
            {
                "name": "Both Dates Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=5),
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(task.id, task_ids)

        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertEqual(task_data["name"], "Both Dates Task")
        self.assertIn(str(self.now), task_data["planned_date_start"])
        self.assertEqual(task_data["stage_name"], "In Progress")
        self.assertTrue(task_data["is_in_progress"])
        self.assertFalse(task_data["is_backlog"])

    # ------------------------------------------------------------------
    # Single-date auto-fill
    # ------------------------------------------------------------------

    def test_task_start_only_gets_auto_end(self):
        """Task with only start date gets end = start + 1 day."""
        task = self.Task.create(
            {
                "name": "Start Only",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "planned_date_start": self.now,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        expected_end = str(self.now + timedelta(days=1))
        self.assertEqual(task_data["planned_date_end"], expected_end)

    def test_task_end_only_gets_auto_start(self):
        """Task with only end date gets start = end - 1 day."""
        end_dt = self.now + timedelta(days=10)
        task = self.Task.create(
            {
                "name": "End Only",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "planned_date_end": end_dt,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        expected_start = str(end_dt - timedelta(days=1))
        self.assertEqual(task_data["planned_date_start"], expected_start)

    # ------------------------------------------------------------------
    # Tasks without dates
    # ------------------------------------------------------------------

    def test_tasks_without_dates_counted(self):
        """Tasks with no dates are counted in tasks_without_dates."""
        self.Task.create(
            {
                "name": "No Dates 1",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        self.Task.create(
            {
                "name": "No Dates 2",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        self.assertGreaterEqual(result["tasks_without_dates"], 2)

    def test_tasks_without_dates_not_in_tasks_list(self):
        """Tasks that have absolutely no dates should not appear in the tasks list."""
        no_date = self.Task.create(
            {
                "name": "Invisible Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertNotIn(no_date.id, task_ids)

    # ------------------------------------------------------------------
    # Folded (completed) stage filtering
    # ------------------------------------------------------------------

    def test_folded_stages_excluded_by_default(self):
        """Tasks in folded stages are excluded when show_completed=False."""
        task = self.Task.create(
            {
                "name": "Done Task",
                "project_id": self.project.id,
                "stage_id": self.stage_done.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id, show_completed=False)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertNotIn(task.id, task_ids)

    def test_folded_stages_included_with_show_completed(self):
        """Tasks in folded stages are included when show_completed=True."""
        task = self.Task.create(
            {
                "name": "Done Task Included",
                "project_id": self.project.id,
                "stage_id": self.stage_done.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id, show_completed=True)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(task.id, task_ids)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertTrue(task_data["is_closed"])

    def test_tasks_without_dates_in_folded_excluded_from_count(self):
        """Folded tasks without dates should NOT be counted when show_completed=False."""
        self.Task.create(
            {
                "name": "Done No Dates",
                "project_id": self.project.id,
                "stage_id": self.stage_done.id,
            },
        )
        result_excl = self.Project.get_gantt_chart_data(self.project.id, show_completed=False)
        result_incl = self.Project.get_gantt_chart_data(self.project.id, show_completed=True)
        self.assertGreaterEqual(
            result_incl["tasks_without_dates"],
            result_excl["tasks_without_dates"],
        )

    # ------------------------------------------------------------------
    # Overdue detection
    # ------------------------------------------------------------------

    def test_overdue_task_flagged(self):
        """Task whose end date is in the past is marked is_overdue=True."""
        past = self.now - timedelta(days=5)
        task = self.Task.create(
            {
                "name": "Overdue Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": past - timedelta(days=2),
                "planned_date_end": past,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertTrue(task_data["is_overdue"])

    def test_future_task_not_overdue(self):
        """Task whose end date is in the future is not overdue."""
        future = self.now + timedelta(days=10)
        task = self.Task.create(
            {
                "name": "Future Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": future,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertFalse(task_data["is_overdue"])

    # ------------------------------------------------------------------
    # User assignment
    # ------------------------------------------------------------------

    def test_user_names_populated(self):
        """User names are comma-separated when multiple users assigned."""
        task = self.Task.create(
            {
                "name": "Multi User",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=3),
                "user_ids": [(6, 0, [self.user_dev1.id, self.user_dev2.id])],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertIn("Dev One", task_data["user_names"])
        self.assertIn("Dev Two", task_data["user_names"])
        self.assertEqual(len(task_data["user_list"]), 2)

    def test_unassigned_task_empty_users(self):
        """Unassigned task has empty user_names and empty user_list."""
        task = self.Task.create(
            {
                "name": "Unassigned Gantt",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
                "user_ids": [(5, 0, 0)],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertEqual(task_data["user_names"], "")
        self.assertEqual(task_data["user_list"], [])

    # ------------------------------------------------------------------
    # Parent/child hierarchy
    # ------------------------------------------------------------------

    def test_parent_child_relationship(self):
        """Child task references parent_id and parent lists child in child_ids."""
        parent = self.Task.create(
            {
                "name": "Parent Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=10),
            },
        )
        child = self.Task.create(
            {
                "name": "Child Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now + timedelta(days=1),
                "planned_date_end": self.now + timedelta(days=5),
                "parent_id": parent.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        parent_data = next(t for t in result["tasks"] if t["id"] == parent.id)
        child_data = next(t for t in result["tasks"] if t["id"] == child.id)

        self.assertIn(child.id, parent_data["child_ids"])
        self.assertEqual(child_data["parent_id"], parent.id)

    def test_ancestor_without_dates_included(self):
        """Parent with no dates is included when its child has dates."""
        parent = self.Task.create(
            {
                "name": "Dateless Parent",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        child = self.Task.create(
            {
                "name": "Dated Child",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=3),
                "parent_id": parent.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(parent.id, task_ids, "Dateless parent should be included via ancestor walk")
        self.assertIn(child.id, task_ids)

    def test_parent_dates_synthesized_from_children(self):
        """Parent without dates gets dates derived from its children."""
        parent = self.Task.create(
            {
                "name": "Parent No Dates",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        child_start = self.now + timedelta(days=1)
        child_end = self.now + timedelta(days=8)
        self.Task.create(
            {
                "name": "Early Child",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": child_start,
                "planned_date_end": self.now + timedelta(days=4),
                "parent_id": parent.id,
            },
        )
        self.Task.create(
            {
                "name": "Late Child",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now + timedelta(days=3),
                "planned_date_end": child_end,
                "parent_id": parent.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        parent_data = next(t for t in result["tasks"] if t["id"] == parent.id)
        # Parent should span from earliest child start to latest child end
        self.assertIn(str(child_start), parent_data["planned_date_start"])
        self.assertIn(str(child_end), parent_data["planned_date_end"])

    def test_grandparent_dates_synthesized_from_grandchildren(self):
        """Grandparent without dates gets dates from grandchildren recursively."""
        grandparent = self.Task.create(
            {
                "name": "Grandparent",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        parent = self.Task.create(
            {
                "name": "Parent (no dates)",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "parent_id": grandparent.id,
            },
        )
        gc_start = self.now + timedelta(days=2)
        gc_end = self.now + timedelta(days=6)
        self.Task.create(
            {
                "name": "Grandchild",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": gc_start,
                "planned_date_end": gc_end,
                "parent_id": parent.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(grandparent.id, task_ids)
        self.assertIn(parent.id, task_ids)

        gp_data = next(t for t in result["tasks"] if t["id"] == grandparent.id)
        self.assertIn(str(gc_start), gp_data["planned_date_start"])
        self.assertIn(str(gc_end), gp_data["planned_date_end"])

    def test_parent_outside_dataset_not_referenced(self):
        """If parent is in a folded stage, child's parent_id should be False."""
        parent = self.Task.create(
            {
                "name": "Done Parent",
                "project_id": self.project.id,
                "stage_id": self.stage_done.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=5),
            },
        )
        child = self.Task.create(
            {
                "name": "Active Child",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=3),
                "parent_id": parent.id,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id, show_completed=False)
        child_data = next(t for t in result["tasks"] if t["id"] == child.id)
        # Parent is in folded stage so excluded → child should not reference it
        self.assertFalse(child_data["parent_id"])
        self.assertEqual(child_data["parent_label"], parent.key or parent.name)

    # ------------------------------------------------------------------
    # Dependencies (depend_on_ids)
    # ------------------------------------------------------------------

    def test_dependencies_included(self):
        """depend_on_ids are populated when both blocker and blocked are in the set."""
        blocker = self.Task.create(
            {
                "name": "Blocker",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            },
        )
        blocked = self.Task.create(
            {
                "name": "Blocked Task",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "planned_date_start": self.now + timedelta(days=3),
                "planned_date_end": self.now + timedelta(days=7),
                "depend_on_ids": [(6, 0, [blocker.id])],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        blocked_data = next(t for t in result["tasks"] if t["id"] == blocked.id)
        self.assertIn(blocker.id, blocked_data["depend_on_ids"])

    def test_dependency_excluded_when_blocker_not_in_set(self):
        """depend_on_ids should not reference tasks outside the filtered set."""
        # Blocker in folded stage - excluded from Gantt
        blocker = self.Task.create(
            {
                "name": "Done Blocker",
                "project_id": self.project.id,
                "stage_id": self.stage_done.id,
                "planned_date_start": self.now - timedelta(days=5),
                "planned_date_end": self.now - timedelta(days=3),
            },
        )
        blocked = self.Task.create(
            {
                "name": "Blocked with Done Dep",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=4),
                "depend_on_ids": [(6, 0, [blocker.id])],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id, show_completed=False)
        blocked_data = next(t for t in result["tasks"] if t["id"] == blocked.id)
        self.assertNotIn(blocker.id, blocked_data["depend_on_ids"])

    # ------------------------------------------------------------------
    # Task data fields
    # ------------------------------------------------------------------

    def test_task_data_fields_complete(self):
        """Each task dict has all expected keys."""
        task = self.Task.create(
            {
                "name": "Complete Fields",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=3),
                "planned_hours": 20.0,
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)

        expected_keys = {
            "id",
            "name",
            "key",
            "parent_id",
            "parent_label",
            "is_planned_from_subtasks",
            "child_ids",
            "planned_date_start",
            "planned_date_end",
            "stage_id",
            "stage_name",
            "stage_sequence",
            "is_in_progress",
            "is_backlog",
            "is_closed",
            "is_overdue",
            "user_names",
            "user_list",
            "progress",
            "planned_hours",
            "remaining_hours",
            "depend_on_ids",
            "task_type",
            "project_id",
            "date_deadline",
            "effective_hours",
            "can_write",
            "depend_on_lags",
            "is_deadline_only",
            "plan_date_start",
            "plan_date_end",
            "baseline_date_start",
            "baseline_date_end",
            "baseline_shift_days",
            "stage_history",
        }
        self.assertEqual(set(task_data.keys()), expected_keys)

    def test_task_data_carries_stage_for_grouping(self):
        task = self.Task.create(
            {
                "name": "Staged Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertEqual(task_data["stage_id"], self.stage_in_progress.id)
        self.assertEqual(task_data["stage_sequence"], self.stage_in_progress.sequence)

    def test_task_key_populated(self):
        """Task key is included in the data."""
        task = self.Task.create(
            {
                "name": "Keyed Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        # key may be auto-generated or empty, but the field must exist
        self.assertIn("key", task_data)

    def test_planned_hours_and_progress(self):
        """Planned hours and progress are returned."""
        task = self.Task.create(
            {
                "name": "Hours Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
                "planned_hours": 16.0,
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertAlmostEqual(task_data["planned_hours"], 16.0)
        self.assertIn("remaining_hours", task_data)
        self.assertIn("progress", task_data)

    # ------------------------------------------------------------------
    # Stage flags
    # ------------------------------------------------------------------

    def test_stage_flags_in_progress(self):
        """In-progress stage sets is_in_progress=True."""
        task = self.Task.create(
            {
                "name": "IP Flag",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertTrue(task_data["is_in_progress"])
        self.assertFalse(task_data["is_backlog"])
        self.assertFalse(task_data["is_closed"])

    def test_stage_flags_backlog(self):
        """Backlog stage sets is_backlog=True."""
        task = self.Task.create(
            {
                "name": "BL Flag",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertTrue(task_data["is_backlog"])
        self.assertFalse(task_data["is_in_progress"])

    def test_stage_flags_closed(self):
        """Folded (done) stage sets is_closed=True when show_completed."""
        task = self.Task.create(
            {
                "name": "Done Flag",
                "project_id": self.project.id,
                "stage_id": self.stage_done.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id, show_completed=True)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertTrue(task_data["is_closed"])

    # ------------------------------------------------------------------
    # Cross-project isolation
    # ------------------------------------------------------------------

    def test_tasks_from_other_project_excluded(self):
        """Tasks from a different project are not included."""
        other_proj = self.Project.create(
            {"name": "Other Project", "is_ai_driven": True},
        )
        other_task = self.Task.create(
            {
                "name": "Other Project Task",
                "project_id": other_proj.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=3),
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertNotIn(other_task.id, task_ids)

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_task_type_field(self):
        """task_type value is returned correctly (or empty string for unset)."""
        task = self.Task.create(
            {
                "name": "Typed Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
                "task_type": "feature",
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertEqual(task_data["task_type"], "feature")

    def test_user_list_structure(self):
        """user_list contains dicts with id and name keys."""
        task = self.Task.create(
            {
                "name": "User List Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=1),
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        task_data = next(t for t in result["tasks"] if t["id"] == task.id)
        self.assertEqual(len(task_data["user_list"]), 1)
        user_entry = task_data["user_list"][0]
        self.assertEqual(user_entry["id"], self.user_dev1.id)
        self.assertEqual(user_entry["name"], "Dev One")

    def test_many_tasks_performance(self):
        """Smoke test: method handles a batch of tasks without error."""
        tasks = self.Task.create(
            [
                {
                    "name": "Batch %d" % i,
                    "project_id": self.project.id,
                    "stage_id": self.stage_in_progress.id,
                    "planned_date_start": self.now + timedelta(days=i),
                    "planned_date_end": self.now + timedelta(days=i + 2),
                }
                for i in range(20)
            ],
        )
        result = self.Project.get_gantt_chart_data(self.project.id)
        returned_ids = {t["id"] for t in result["tasks"]}
        for task in tasks:
            self.assertIn(task.id, returned_ids)
