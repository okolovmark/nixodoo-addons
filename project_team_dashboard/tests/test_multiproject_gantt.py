from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestMultiProjectGantt(TeamDashboardCommon):
    """Tests for multi-project Gantt chart data (get_gantt_chart_data)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Project = cls.env["project.project"]
        cls.Task = cls.env["project.task"]
        cls.today = fields.Date.today()
        cls.now = fields.Datetime.now()

        # Create a second AI-driven project with a key
        cls.project2 = cls.Project.create(
            {
                "name": "Second Project",
                "is_ai_driven": True,
                "key": "TSPG",
                "type_ids": [
                    (
                        6,
                        0,
                        [
                            cls.stage_backlog.id,
                            cls.stage_in_progress.id,
                            cls.stage_done.id,
                        ],
                    ),
                ],
            },
        )

        # Create tasks in both projects
        cls.task_p1 = cls.Task.create(
            {
                "name": "Task in Project 1",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "planned_date_start": cls.now,
                "planned_date_end": cls.now + timedelta(days=5),
                "user_ids": [(6, 0, [cls.user_dev1.id])],
            },
        )
        cls.task_p2 = cls.Task.create(
            {
                "name": "Task in Project 2",
                "project_id": cls.project2.id,
                "stage_id": cls.stage_backlog.id,
                "planned_date_start": cls.now + timedelta(days=2),
                "planned_date_end": cls.now + timedelta(days=8),
                "user_ids": [(6, 0, [cls.user_dev2.id])],
            },
        )

    # ------------------------------------------------------------------
    # Single project_id (backward compat)
    # ------------------------------------------------------------------

    def test_single_project_id_backward_compat(self):
        """get_gantt_chart_data with single project_id returns correct tasks."""
        result = self.Project.get_gantt_chart_data(project_id=self.project.id)
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(self.task_p1.id, task_ids)
        self.assertNotIn(self.task_p2.id, task_ids)

    def test_single_project_id_structure(self):
        """Single project_id response has expected keys."""
        result = self.Project.get_gantt_chart_data(project_id=self.project.id)
        self.assertIn("tasks", result)
        self.assertIn("today", result)
        self.assertIn("tasks_without_dates", result)
        self.assertIn("user_tz", result)

    def test_single_project_no_project_name_on_tasks(self):
        """Single project mode does not include project_name on task dicts."""
        result = self.Project.get_gantt_chart_data(project_id=self.project.id)
        for task_data in result["tasks"]:
            self.assertNotIn(
                "project_name",
                task_data,
                "Single-project mode should not include project_name",
            )

    # ------------------------------------------------------------------
    # Multi project_ids
    # ------------------------------------------------------------------

    def test_multi_project_ids_returns_tasks_from_both(self):
        """get_gantt_chart_data with project_ids returns tasks from all projects."""
        result = self.Project.get_gantt_chart_data(
            project_ids=[self.project.id, self.project2.id],
        )
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(self.task_p1.id, task_ids)
        self.assertIn(self.task_p2.id, task_ids)

    def test_multi_project_response_includes_project_name(self):
        """Multi-project response includes project_name on each task dict."""
        result = self.Project.get_gantt_chart_data(
            project_ids=[self.project.id, self.project2.id],
        )
        for task_data in result["tasks"]:
            self.assertIn(
                "project_name",
                task_data,
                "Multi-project mode must include project_name on tasks",
            )

    def test_multi_project_task_project_name_values(self):
        """project_name on task dicts matches the owning project name."""
        result = self.Project.get_gantt_chart_data(
            project_ids=[self.project.id, self.project2.id],
        )
        p2_task = next(t for t in result["tasks"] if t["id"] == self.task_p2.id)
        # project2 has key "TSPG", so project_name should include it
        self.assertIn("TSPG", p2_task["project_name"])
        self.assertIn("Second Project", p2_task["project_name"])

    def test_multi_project_cross_isolation(self):
        """Tasks from projects not in project_ids are excluded."""
        # Only ask for project2
        result = self.Project.get_gantt_chart_data(
            project_ids=[self.project2.id],
        )
        task_ids = [t["id"] for t in result["tasks"]]
        self.assertIn(self.task_p2.id, task_ids)
        self.assertNotIn(self.task_p1.id, task_ids)

    # ------------------------------------------------------------------
    # user_tz in response
    # ------------------------------------------------------------------

    def test_response_includes_user_tz(self):
        """Response includes user_tz key."""
        result = self.Project.get_gantt_chart_data(project_id=self.project.id)
        self.assertIn("user_tz", result)

    def test_user_tz_reflects_user_timezone(self):
        """user_tz reflects the current user's timezone setting."""
        self.env.user.tz = "Asia/Tokyo"
        result = self.Project.get_gantt_chart_data(project_id=self.project.id)
        self.assertEqual(result["user_tz"], "Asia/Tokyo")

    def test_user_tz_defaults_to_utc(self):
        """user_tz defaults to 'UTC' when user has no timezone set."""
        self.env.user.tz = False
        result = self.Project.get_gantt_chart_data(project_id=self.project.id)
        self.assertEqual(result["user_tz"], "UTC")

    def test_user_tz_is_the_timezone_of_the_deadline_dates(self):
        self.env.user.tz = "Europe/Brussels"
        task = self.env["project.task"].create(
            {"name": "Late Deadline", "project_id": self.project.id, "date_deadline": "2026-03-20 20:00:00"}
        )

        result = self.Project.with_context(tz="Asia/Tokyo").get_gantt_chart_data(project_id=self.project.id)

        self.assertEqual(result["user_tz"], "Asia/Tokyo")
        task_data = next(task_data for task_data in result["tasks"] if task_data["id"] == task.id)
        self.assertEqual(task_data["date_deadline"], "2026-03-21")

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_no_project_id_or_project_ids(self):
        """Calling with no project_id or project_ids returns empty result."""
        result = self.Project.get_gantt_chart_data()
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["tasks_without_dates"], 0)
        self.assertIn("user_tz", result)

    def test_nonexistent_project_ids(self):
        """Non-existent project IDs return empty tasks list."""
        result = self.Project.get_gantt_chart_data(project_ids=[999998, 999999])
        self.assertEqual(result["tasks"], [])

    def test_project_ids_takes_precedence_over_project_id(self):
        """When both project_id and project_ids are given, project_ids wins."""
        result = self.Project.get_gantt_chart_data(
            project_id=self.project.id,
            project_ids=[self.project2.id],
        )
        task_ids = [t["id"] for t in result["tasks"]]
        # project_ids should take precedence
        self.assertIn(self.task_p2.id, task_ids)
        self.assertNotIn(self.task_p1.id, task_ids)

    def test_the_followed_tasks_of_a_project_the_user_cannot_read_stay_out(self):
        project_hidden = self.Project.create({"name": "Team Members Only", "privacy_visibility": "followers"})
        followed_tasks = self.Task.create(
            [
                {
                    "name": "Followed Dated",
                    "project_id": project_hidden.id,
                    "planned_date_start": self.now,
                    "planned_date_end": self.now + timedelta(days=2),
                },
                {"name": "Followed Undated", "project_id": project_hidden.id},
            ]
        )
        followed_tasks.message_subscribe(partner_ids=self.user_dev1.partner_id.ids)
        Project = self.Project.with_user(self.user_dev1)
        self.assertFalse(project_hidden.with_user(self.user_dev1).has_access("read"))
        self.assertEqual(self.Task.with_user(self.user_dev1).search([("id", "in", followed_tasks.ids)]), followed_tasks)

        result = Project.get_gantt_chart_data(project_ids=[self.project.id, project_hidden.id])

        self.assertEqual([task_data["id"] for task_data in result["tasks"]], self.task_p1.ids)
        self.assertEqual(result["unscheduled"], [])
        self.assertEqual(result["tasks_without_dates"], 0)

    def test_a_parent_followed_in_a_project_the_user_cannot_read_stays_off_the_chart(self):
        project_hidden = self.Project.create({"name": "Team Members Only", "privacy_visibility": "followers"})
        parent_followed, parent_readable = self.Task.create(
            [
                {"name": "Followed Parent", "project_id": project_hidden.id},
                {"name": "Parent in a Readable Project", "project_id": self.project_plain.id},
            ]
        )
        parent_followed.message_subscribe(partner_ids=self.user_dev1.partner_id.ids)
        child_of_followed, child_of_readable = self.Task.create(
            [
                {
                    "name": "Child of %s" % parent.name,
                    "project_id": self.project.id,
                    "parent_id": parent.id,
                    "planned_date_start": self.now,
                    "planned_date_end": self.now + timedelta(days=2),
                }
                for parent in parent_followed | parent_readable
            ]
        )
        Project = self.Project.with_user(self.user_dev1)
        self.assertTrue(parent_followed.with_user(self.user_dev1).has_access("read"))
        self.assertFalse(project_hidden.with_user(self.user_dev1).has_access("read"))
        self.assertTrue(self.project_plain.with_user(self.user_dev1).has_access("read"))

        result = Project.get_gantt_chart_data(project_ids=[self.project.id, self.project2.id])

        parent_by_task = {task_data["id"]: task_data["parent_id"] for task_data in result["tasks"]}
        self.assertNotIn(parent_followed.id, parent_by_task)
        self.assertIs(parent_by_task[child_of_followed.id], False)
        self.assertEqual(parent_by_task[child_of_readable.id], parent_readable.id)
        self.assertIn(parent_readable.id, parent_by_task)

    def test_a_parent_kept_from_a_project_not_selected_is_labelled_by_its_own_project(self):
        project_unselected = self.Project.create({"name": "Unselected Project", "key": "UNSL"})
        parent = self.Task.create({"name": "Parent Elsewhere", "project_id": project_unselected.id})
        child = self.Task.create(
            {
                "name": "Child Here",
                "project_id": self.project.id,
                "parent_id": parent.id,
                "planned_date_start": self.now,
                "planned_date_end": self.now + timedelta(days=2),
            }
        )

        result = self.Project.get_gantt_chart_data(project_ids=[self.project.id, self.project2.id])

        label_by_task = {task_data["id"]: task_data["project_name"] for task_data in result["tasks"]}
        self.assertEqual(label_by_task[parent.id], "UNSL - Unselected Project")
        self.assertEqual(label_by_task[child.id], "Test AI-driven Project")

    def test_multi_project_tasks_without_dates_count(self):
        """tasks_without_dates counts across all requested projects."""
        # Create dateless tasks in both projects
        self.Task.create(
            {
                "name": "No Dates P1",
                "project_id": self.project.id,
                "stage_id": self.stage_backlog.id,
            },
        )
        self.Task.create(
            {
                "name": "No Dates P2",
                "project_id": self.project2.id,
                "stage_id": self.stage_backlog.id,
            },
        )
        result = self.Project.get_gantt_chart_data(
            project_ids=[self.project.id, self.project2.id],
        )
        self.assertGreaterEqual(result["tasks_without_dates"], 2)
