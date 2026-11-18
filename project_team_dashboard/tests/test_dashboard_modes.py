from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestDashboardManagerMode(TeamDashboardCommon):
    """Tests for get_team_dashboard_by_manager and _get_subordinate_employees."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Create a department
        cls.department = cls.env["hr.department"].create({"name": "Engineering"})

        # Create a manager user
        cls.user_manager = cls.env["res.users"].create(
            {
                "name": "Manager User",
                "login": "manager_test_sw",
                "email": "manager@test.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("project.group_project_user").id,
                        ],
                    ),
                ],
            },
        )
        cls.employee_manager = cls.env["hr.employee"].create(
            {
                "name": "Manager User",
                "user_id": cls.user_manager.id,
                "department_id": cls.department.id,
                "company_id": cls.env.company.id,
            },
        )

        # Set dev1 and dev2 as subordinates of manager
        cls.employee_dev1.write(
            {
                "parent_id": cls.employee_manager.id,
                "department_id": cls.department.id,
            },
        )
        cls.employee_dev2.write(
            {
                "parent_id": cls.employee_manager.id,
                "department_id": cls.department.id,
            },
        )

        # Create a sub-subordinate user (level 2 under dev1)
        cls.user_dev3 = cls.env["res.users"].create(
            {
                "name": "Dev Three",
                "login": "dev3_test_sw",
                "email": "dev3@test.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("project.group_project_user").id,
                        ],
                    ),
                ],
            },
        )
        cls.employee_dev3 = cls.env["hr.employee"].create(
            {
                "name": "Dev Three",
                "user_id": cls.user_dev3.id,
                "parent_id": cls.employee_dev1.id,
                "department_id": cls.department.id,
                "company_id": cls.env.company.id,
            },
        )

        # Create a second AI-driven project
        cls.project2 = cls.env["project.project"].create(
            {
                "name": "Second AI-driven Project",
                "is_ai_driven": True,
                "type_ids": [
                    (
                        6,
                        0,
                        [
                            cls.stage_backlog.id,
                            cls.stage_in_progress.id,
                            cls.stage_done.id,
                            cls.stage_other.id,
                        ],
                    ),
                ],
            },
        )

        # Create tasks: dev1 in project (from common), dev3 in project2
        cls.task_dev1 = cls.env["project.task"].create(
            {
                "name": "Dev1 Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "allocated_hours": 10.0,
            },
        )
        cls.task_dev2 = cls.env["project.task"].create(
            {
                "name": "Dev2 Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev2.id])],
                "allocated_hours": 8.0,
            },
        )
        cls.task_dev3_proj2 = cls.env["project.task"].create(
            {
                "name": "Dev3 Task in Project2",
                "project_id": cls.project2.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev3.id])],
                "allocated_hours": 12.0,
            },
        )

    # ── _get_subordinate_employees ──

    def test_subordinate_all_levels(self):
        """Test _get_subordinate_employees with max_depth=0 returns all levels."""
        Project = self.env["project.project"]
        subs = Project._get_subordinate_employees(self.employee_manager, max_depth=0)
        self.assertIn(self.employee_dev1, subs)
        self.assertIn(self.employee_dev2, subs)
        self.assertIn(self.employee_dev3, subs)
        self.assertNotIn(self.employee_manager, subs, "Manager should not be in own subordinates")

    def test_subordinate_depth_1(self):
        """Test _get_subordinate_employees with max_depth=1 returns only direct reports."""
        Project = self.env["project.project"]
        subs = Project._get_subordinate_employees(self.employee_manager, max_depth=1)
        self.assertIn(self.employee_dev1, subs)
        self.assertIn(self.employee_dev2, subs)
        self.assertNotIn(self.employee_dev3, subs, "Depth-2 subordinate excluded at depth=1")

    def test_subordinate_depth_2(self):
        """Test _get_subordinate_employees with max_depth=2 returns 2 levels."""
        Project = self.env["project.project"]
        subs = Project._get_subordinate_employees(self.employee_manager, max_depth=2)
        self.assertIn(self.employee_dev1, subs)
        self.assertIn(self.employee_dev2, subs)
        self.assertIn(self.employee_dev3, subs, "Depth-2 subordinate included at max_depth=2")

    def test_subordinate_no_children(self):
        """Test _get_subordinate_employees for leaf employee returns empty recordset."""
        Project = self.env["project.project"]
        subs = Project._get_subordinate_employees(self.employee_dev3, max_depth=0)
        self.assertFalse(subs)

    # ── get_team_dashboard_by_manager ──

    def test_manager_dashboard_returns_members(self):
        """Test that manager dashboard lists all subordinates with open tasks."""
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
        )
        self.assertNotIn("error", data)
        self.assertEqual(data["manager_id"], self.user_manager.id)
        self.assertEqual(data["manager_name"], "Manager User")
        member_user_ids = [m["user_id"] for m in data["members"]]
        self.assertIn(self.user_dev1.id, member_user_ids)
        self.assertIn(self.user_dev2.id, member_user_ids)
        self.assertIn(self.user_dev3.id, member_user_ids)

    def test_manager_dashboard_cross_project(self):
        """Test that manager dashboard spans multiple projects."""
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
        )
        self.assertGreater(data.get("project_count", 0), 1)
        # Dev3 should show Project2 in their project_names
        dev3_data = next(m for m in data["members"] if m["user_id"] == self.user_dev3.id)
        self.assertTrue(
            any("Second" in pn for pn in dev3_data["project_names"]),
            "Dev3 should show Second AI-driven Project",
        )

    def test_manager_dashboard_project_names_with_key(self):
        """A keyed project is listed in a member's project_names as KEY - name."""
        self.project2.key = "SSP"
        data = self.env["project.project"].get_team_dashboard_by_manager(self.user_manager.id)
        dev3_data = next(m for m in data["members"] if m["user_id"] == self.user_dev3.id)
        self.assertEqual(dev3_data["project_names"], ["SSP - %s" % self.project2.name])

    def test_manager_dashboard_counts_the_tasks_open_by_their_state(self):
        Task = self.env["project.task"]
        task_values = {"project_id": self.project2.id, "user_ids": [Command.set(self.user_dev2.ids)]}
        folded_open_task = Task.create(dict(task_values, name="Folded Open", stage_id=self.stage_done.id))
        Task.create(dict(task_values, name="Done", stage_id=self.stage_done.id, state="1_done"))
        Task.create(dict(task_values, name="Canceled", stage_id=self.stage_backlog.id, state="1_canceled"))
        to_do = Task.create(dict(task_values, name="Personal To-do", project_id=False))
        self.assertFalse(to_do.is_closed)

        data = self.env["project.project"].get_team_dashboard_by_manager(self.user_manager.id)

        dev2_data = next(m for m in data["members"] if m["user_id"] == self.user_dev2.id)
        self.assertEqual(dev2_data["total_open_tasks"], 2)
        self.assertEqual([t["id"] for t in dev2_data["other"]], [folded_open_task.id])
        self.assertEqual(dev2_data["project_names"], sorted([self.project.name, self.project2.name]))

    def test_manager_dashboard_with_depth(self):
        """Test that max_depth limits subordinate traversal."""
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
            max_depth=1,
        )
        member_user_ids = [m["user_id"] for m in data["members"]]
        self.assertIn(self.user_dev1.id, member_user_ids)
        self.assertIn(self.user_dev2.id, member_user_ids)
        # Dev3 only has tasks, but since depth=1, dev3 should be excluded
        self.assertNotIn(
            self.user_dev3.id,
            member_user_ids,
            "Dev3 (depth-2) should be excluded with max_depth=1",
        )

    def test_manager_dashboard_custom_dates(self):
        """Test custom date range is reflected in result."""
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
            date_start="2026-03-01",
            date_end="2026-03-31",
        )
        self.assertEqual(data["period_start"], "2026-03-01")
        self.assertEqual(data["period_end"], "2026-03-31")

    def test_manager_dashboard_default_period(self):
        """Test that default period is current week."""
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
        )
        today = fields.Date.today()
        expected_start = today - timedelta(days=today.weekday())
        expected_end = expected_start + timedelta(days=6)
        self.assertEqual(data["period_start"], fields.Date.to_string(expected_start))
        self.assertEqual(data["period_end"], fields.Date.to_string(expected_end))

    def test_manager_dashboard_no_employee_record(self):
        """Test error when user has no employee record."""
        user_no_emp = self.env["res.users"].create(
            {
                "name": "No Employee",
                "login": "no_emp_dash",
                "email": "noemp@test.com",
            },
        )
        # Ensure no employee record
        self.env["hr.employee"].search([("user_id", "=", user_no_emp.id)]).unlink()
        data = self.env["project.project"].get_team_dashboard_by_manager(user_no_emp.id)
        self.assertIn("error", data)
        self.assertIn("No employee record", data["error"])

    def test_manager_dashboard_nonexistent_user(self):
        """Test error for non-existent user ID."""
        data = self.env["project.project"].get_team_dashboard_by_manager(999999)
        self.assertIn("error", data)

    def test_manager_dashboard_no_subordinates(self):
        """Test result when manager has no subordinates."""
        # dev3 is a leaf node with no children
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_dev3.id,
        )
        # No subordinates → no users → empty members
        self.assertEqual(data["members"], [])

    def test_manager_dashboard_with_timesheets(self):
        """Test that timesheets are aggregated across projects."""
        today = fields.Date.today()
        self.env["account.analytic.line"].create(
            {
                "name": "Dev1 work",
                "project_id": self.project.id,
                "task_id": self.task_dev1.id,
                "user_id": self.user_dev1.id,
                "unit_amount": 4.0,
                "date": today,
            },
        )
        self.env["account.analytic.line"].create(
            {
                "name": "Dev3 work on project2",
                "project_id": self.project2.id,
                "task_id": self.task_dev3_proj2.id,
                "user_id": self.user_dev3.id,
                "unit_amount": 2.5,
                "date": today,
            },
        )
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
        )
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        dev3_data = next(m for m in data["members"] if m["user_id"] == self.user_dev3.id)
        self.assertAlmostEqual(dev1_data["hours_logged_today"], 4.0)
        self.assertAlmostEqual(dev3_data["hours_logged_today"], 2.5)

    def test_manager_dashboard_skips_users_without_tasks(self):
        """Test that subordinates without open tasks are excluded from members."""
        # Create a subordinate with no tasks
        user_idle = self.env["res.users"].create(
            {
                "name": "Idle Dev",
                "login": "idle_test_sw",
                "email": "idle@test.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("project.group_project_user").id,
                        ],
                    ),
                ],
            },
        )
        self.env["hr.employee"].create(
            {
                "name": "Idle Dev",
                "user_id": user_idle.id,
                "parent_id": self.employee_manager.id,
                "company_id": self.env.company.id,
            },
        )
        data = self.env["project.project"].get_team_dashboard_by_manager(
            self.user_manager.id,
        )
        member_user_ids = [m["user_id"] for m in data["members"]]
        self.assertNotIn(
            user_idle.id,
            member_user_ids,
            "Users without open tasks should not appear in members",
        )


@tagged("post_install", "-at_install")
class TestDashboardDepartmentMode(TeamDashboardCommon):
    """Tests for get_team_dashboard_by_department."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.department = cls.env["hr.department"].create({"name": "QA Team"})

        # Put dev1 and dev2 in the department
        cls.employee_dev1.write({"department_id": cls.department.id})
        cls.employee_dev2.write({"department_id": cls.department.id})

        # Create tasks for dev1 and dev2
        cls.task_dev1 = cls.env["project.task"].create(
            {
                "name": "Dev1 QA Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "allocated_hours": 6.0,
            },
        )
        cls.task_dev2 = cls.env["project.task"].create(
            {
                "name": "Dev2 QA Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "user_ids": [(6, 0, [cls.user_dev2.id])],
                "allocated_hours": 4.0,
            },
        )

    def test_department_dashboard_returns_members(self):
        """Test that department dashboard lists all department members with tasks."""
        data = self.env["project.project"].get_team_dashboard_by_department(
            self.department.id,
        )
        self.assertNotIn("error", data)
        self.assertEqual(data["department_id"], self.department.id)
        self.assertIn("department_name", data)
        member_user_ids = [m["user_id"] for m in data["members"]]
        self.assertIn(self.user_dev1.id, member_user_ids)
        self.assertIn(self.user_dev2.id, member_user_ids)

    def test_department_dashboard_member_task_data(self):
        """Test that member entries have correct task categorization."""
        data = self.env["project.project"].get_team_dashboard_by_department(
            self.department.id,
        )
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        # dev1 has an in-progress task → should be current_task
        self.assertTrue(dev1_data["current_task"])
        self.assertEqual(dev1_data["current_task"]["id"], self.task_dev1.id)

        dev2_data = next(m for m in data["members"] if m["user_id"] == self.user_dev2.id)
        # dev2 has a backlog task → no current_task
        self.assertFalse(dev2_data["current_task"])
        backlog_ids = [t["id"] for t in dev2_data["backlog"]]
        self.assertIn(self.task_dev2.id, backlog_ids)

    def test_department_dashboard_custom_dates(self):
        """Test custom date range."""
        data = self.env["project.project"].get_team_dashboard_by_department(
            self.department.id,
            date_start="2026-06-01",
            date_end="2026-06-30",
        )
        self.assertEqual(data["period_start"], "2026-06-01")
        self.assertEqual(data["period_end"], "2026-06-30")

    def test_department_dashboard_nonexistent(self):
        """Test error for non-existent department."""
        data = self.env["project.project"].get_team_dashboard_by_department(999999)
        self.assertIn("error", data)

    def test_department_dashboard_empty(self):
        """Test empty result for department with no employees."""
        empty_dept = self.env["hr.department"].create({"name": "Empty Dept"})
        data = self.env["project.project"].get_team_dashboard_by_department(
            empty_dept.id,
        )
        self.assertEqual(data["members"], [])

    def test_department_dashboard_with_timesheets(self):
        """Test timesheet aggregation in department mode."""
        today = fields.Date.today()
        self.env["account.analytic.line"].create(
            {
                "name": "Dev1 QA work",
                "project_id": self.project.id,
                "task_id": self.task_dev1.id,
                "user_id": self.user_dev1.id,
                "unit_amount": 3.0,
                "date": today,
            },
        )
        data = self.env["project.project"].get_team_dashboard_by_department(
            self.department.id,
        )
        dev1_data = next(m for m in data["members"] if m["user_id"] == self.user_dev1.id)
        self.assertAlmostEqual(dev1_data["hours_logged_today"], 3.0)


@tagged("post_install", "-at_install")
class TestTaskToDictProjectFields(TeamDashboardCommon):
    """Test that _task_to_dict includes project_id and project_name."""

    def test_task_to_dict_project_fields(self):
        """Test _task_to_dict returns project_id and project_name."""
        task = self.env["project.task"].create(
            {
                "name": "Project Fields Test",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [(6, 0, [self.user_dev1.id])],
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertEqual(result["project_id"], self.project.id)
        self.assertIn(self.project.name, result["project_name"])

    def test_task_to_dict_project_name_with_key(self):
        """Test _task_to_dict formats project_name with key when present."""
        self.project.key = "TST"
        task = self.env["project.task"].create(
            {
                "name": "Keyed Project Task",
                "project_id": self.project.id,
                "stage_id": self.stage_in_progress.id,
            },
        )
        today = fields.Date.today()
        result = self.env["project.project"]._task_to_dict(task, today)
        self.assertEqual(result["project_name"], "TST - %s" % self.project.name)


@tagged("post_install", "-at_install")
class TestDashboardModeAccessControl(TeamDashboardCommon):
    """Test that dashboard modes work for project users without HR admin groups.

    Regular project users (project.group_project_user) do NOT have read access
    to hr.employee (requires hr.group_hr_user). The dashboard methods must use
    sudo() for HR model reads to avoid AccessError.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.department = cls.env["hr.department"].create({"name": "Access Test Dept"})

        # Create a project-only user (no HR groups)
        cls.user_project_only = cls.env["res.users"].create(
            {
                "name": "Project Only User",
                "login": "proj_only_test",
                "email": "projonly@test.com",
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("project.group_project_user").id,
                            cls.env.ref("hr_timesheet.group_hr_timesheet_user").id,
                        ],
                    ),
                ],
            },
        )
        cls.employee_project_only = cls.env["hr.employee"].create(
            {
                "name": "Project Only User",
                "user_id": cls.user_project_only.id,
                "department_id": cls.department.id,
                "company_id": cls.env.company.id,
            },
        )

        # dev1 is subordinate of project_only user
        cls.employee_dev1.write(
            {
                "parent_id": cls.employee_project_only.id,
                "department_id": cls.department.id,
            },
        )

        # Create a task so dev1 appears in results
        cls.env["project.task"].create(
            {
                "name": "Access Test Task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [(6, 0, [cls.user_dev1.id])],
                "allocated_hours": 5.0,
            },
        )

    def test_project_user_lacks_hr_employee_access_rights(self):
        """Confirm that project-only users lack direct model-level access to hr.employee.

        hr.employee._search() has a built-in fallback to hr.employee.public,
        so search() never raises AccessError. But has_access() is false.
        The dashboard methods use sudo() for HR reads to avoid the fallback overhead
        and ensure full field access.
        """
        HrEmployee = self.env["hr.employee"].with_user(self.user_project_only)
        has_access = HrEmployee.has_access("read")
        self.assertFalse(
            has_access,
            "Project user should not have direct hr.employee model access",
        )

    def test_manager_mode_no_access_error_for_project_user(self):
        """Test get_team_dashboard_by_manager does not raise AccessError.

        The method should use sudo() internally for HR model reads so
        regular project users can call it without HR admin privileges.
        """
        Project = self.env["project.project"].with_user(self.user_project_only)
        data = Project.get_team_dashboard_by_manager(self.user_project_only.id)
        self.assertNotIn("error", data)
        member_user_ids = [m["user_id"] for m in data["members"]]
        self.assertIn(self.user_dev1.id, member_user_ids)

    def test_department_mode_no_access_error_for_project_user(self):
        """Test get_team_dashboard_by_department does not raise AccessError.

        The method should use sudo() internally for HR model reads so
        regular project users can call it without HR admin privileges.
        """
        Project = self.env["project.project"].with_user(self.user_project_only)
        data = Project.get_team_dashboard_by_department(self.department.id)
        self.assertNotIn("error", data)
        member_user_ids = [m["user_id"] for m in data["members"]]
        self.assertIn(self.user_dev1.id, member_user_ids)

    def test_manager_mode_defaults_to_current_user(self):
        """Test that passing user_id=False defaults to current user."""
        Project = self.env["project.project"].with_user(self.user_project_only)
        data = Project.get_team_dashboard_by_manager(False)
        # Should default to current user and return their subordinates
        self.assertEqual(data.get("manager_id"), self.user_project_only.id)


@tagged("post_install", "-at_install")
class TestDashboardModeAccessRule(TeamDashboardCommon):
    """A user opens the Manager and Department modes on their own team only, unless they read
    every timesheet or every employee anyway."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_internal, cls.user_boss, cls.user_peer, cls.user_approver, cls.user_hr_officer = cls.env[
            "res.users"
        ].create(
            [
                cls._user_vals("Internal", ["base.group_user"]),
                cls._user_vals("Boss", ["base.group_user"]),
                cls._user_vals("Peer", ["base.group_user"]),
                cls._user_vals("Approver", ["base.group_user", "hr_timesheet.group_hr_timesheet_approver"]),
                cls._user_vals("HR Officer", ["base.group_user", "hr.group_hr_user"]),
            ]
        )
        cls.department_own, cls.department_managed, cls.department_other = cls.env["hr.department"].create(
            [{"name": "Own Department"}, {"name": "Managed Department"}, {"name": "Other Department"}]
        )
        Employee = cls.env["hr.employee"]
        cls.employee_boss = Employee.create(
            {"name": "Boss", "user_id": cls.user_boss.id, "department_id": cls.department_other.id}
        )
        cls.employee_internal, cls.employee_peer = Employee.create(
            [
                {
                    "name": "Internal",
                    "user_id": cls.user_internal.id,
                    "parent_id": cls.employee_boss.id,
                    "department_id": cls.department_own.id,
                },
                {
                    "name": "Peer",
                    "user_id": cls.user_peer.id,
                    "parent_id": cls.employee_boss.id,
                    "department_id": cls.department_other.id,
                },
            ]
        )
        cls.department_managed.manager_id = cls.employee_internal
        cls.employee_dev1.write({"parent_id": cls.employee_internal.id, "department_id": cls.department_managed.id})
        cls.employee_dev2.write({"parent_id": cls.employee_dev1.id, "department_id": cls.department_own.id})
        cls.env["project.task"].create(
            [
                {
                    "name": "Task of %s" % user.name,
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_in_progress.id,
                    "user_ids": [Command.set(user.ids)],
                }
                for user in cls.user_dev1 | cls.user_dev2
            ]
        )

    def _members_by_manager(self, user, manager_user):
        data = self.env["project.project"].with_user(user).get_team_dashboard_by_manager(manager_user.id)
        return {member["user_id"] for member in data["members"]}

    def _members_by_department(self, user, department):
        data = self.env["project.project"].with_user(user).get_team_dashboard_by_department(department.id)
        return {member["user_id"] for member in data["members"]}

    def test_a_user_opens_the_manager_mode_for_themselves_and_the_managers_below(self):
        self.assertEqual(
            self._members_by_manager(self.user_internal, self.user_internal), set((self.user_dev1 | self.user_dev2).ids)
        )
        self.assertEqual(self._members_by_manager(self.user_internal, self.user_dev1), set(self.user_dev2.ids))

    def test_a_plain_internal_user_cannot_open_the_manager_mode_for_another_manager(self):
        for manager_user in (self.user_boss, self.user_peer):
            with self.subTest(manager=manager_user.name), self.assertRaisesRegex(AccessError, "by manager"):
                self._members_by_manager(self.user_internal, manager_user)

    def test_a_user_opens_the_department_mode_for_their_own_and_managed_departments(self):
        self.assertEqual(self._members_by_department(self.user_internal, self.department_own), set(self.user_dev2.ids))
        self.assertEqual(
            self._members_by_department(self.user_internal, self.department_managed), set(self.user_dev1.ids)
        )

    def test_a_plain_internal_user_cannot_open_the_department_mode_for_another_department(self):
        with self.assertRaisesRegex(AccessError, "by department"):
            self._members_by_department(self.user_internal, self.department_other)

    def test_a_plain_internal_user_is_refused_before_learning_whether_the_team_exists(self):
        Users = self.env["res.users"].with_context(active_test=False)
        user_without_employee = Users.create(self._user_vals("No Employee", ["base.group_user"]))
        missing_user_id = Users.search([], order="id desc", limit=1).id + 1000
        self.assertFalse(Users.browse(missing_user_id).exists())
        Departments = self.env["hr.department"].with_context(active_test=False)
        missing_department_id = Departments.search([], order="id desc", limit=1).id + 1
        Project = self.env["project.project"].with_user(self.user_internal)
        for manager_user_id in (missing_user_id, user_without_employee.id):
            with self.subTest(manager_user_id=manager_user_id), self.assertRaisesRegex(AccessError, "by manager"):
                Project.get_team_dashboard_by_manager(manager_user_id)
        with self.assertRaisesRegex(AccessError, "by department"):
            Project.get_team_dashboard_by_department(missing_department_id)

    def test_timesheet_approvers_and_hr_officers_open_every_manager_and_department(self):
        for user in self.user_approver | self.user_hr_officer:
            with self.subTest(user=user.name):
                self.assertEqual(
                    self._members_by_manager(user, self.user_boss), set((self.user_dev1 | self.user_dev2).ids)
                )
                self.assertEqual(self._members_by_department(user, self.department_managed), set(self.user_dev1.ids))
                self.assertEqual(self._members_by_department(user, self.department_other), set())


class ManagerModeCase(TeamDashboardCommon):
    @classmethod
    def _create_open_tasks(cls, users):
        cls.env["project.task"].create(
            [
                {
                    "name": "Task of %s" % user.name,
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_in_progress.id,
                    "user_ids": [Command.set(user.ids)],
                }
                for user in users
            ]
        )

    def _members_by_manager(self, user, manager_user, max_depth=0):
        Project = self.env["project.project"].with_user(user)
        data = Project.get_team_dashboard_by_manager(manager_user.id, max_depth=max_depth)
        return {member["user_id"] for member in data["members"]}


@tagged("post_install", "-at_install")
class TestDashboardHierarchyCycles(ManagerModeCase):
    """Odoo lets an employee manage their own manager, and core gives the manager of the department
    they belong to themselves as manager."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_chief, cls.user_left, cls.user_right, cls.user_below = cls.env["res.users"].create(
            [cls._user_vals(name, ["base.group_user"]) for name in ("Chief", "Left", "Right", "Below")]
        )
        Employee = cls.env["hr.employee"]
        cls.employee_chief = Employee.create({"name": "Chief", "user_id": cls.user_chief.id})
        cls.employee_chief.parent_id = cls.employee_chief
        cls.employee_dev1.parent_id = cls.employee_chief
        cls.employee_dev2.parent_id = cls.employee_dev1
        employee_left, employee_right = Employee.create(
            [{"name": user.name, "user_id": user.id} for user in cls.user_left | cls.user_right]
        )
        employee_left.parent_id = employee_right
        employee_right.parent_id = employee_left
        Employee.create({"name": "Below", "user_id": cls.user_below.id, "parent_id": employee_left.id})
        cls._create_open_tasks(
            cls.user_dev1 | cls.user_dev2 | cls.user_chief | cls.user_left | cls.user_right | cls.user_below
        )

    def test_a_self_managed_manager_opens_their_team_without_themselves(self):
        self.assertEqual(
            self._members_by_manager(self.user_chief, self.user_chief), set((self.user_dev1 | self.user_dev2).ids)
        )
        self.assertEqual(
            self._members_by_manager(self.user_chief, self.user_chief, max_depth=1), set(self.user_dev1.ids)
        )

    def test_each_side_of_a_two_employee_cycle_opens_the_team_below_them(self):
        self.assertEqual(
            self._members_by_manager(self.user_left, self.user_left), set((self.user_right | self.user_below).ids)
        )
        self.assertEqual(
            self._members_by_manager(self.user_right, self.user_right), set((self.user_left | self.user_below).ids)
        )

    def test_a_plain_user_is_refused_a_manager_whose_chain_loops_above_them(self):
        for user, manager_user in (
            (self.user_dev1, self.user_chief),
            (self.user_dev2, self.user_chief),
            (self.user_below, self.user_left),
        ):
            with (
                self.subTest(user=user.name, manager=manager_user.name),
                self.assertRaisesRegex(AccessError, "by manager"),
            ):
                self._members_by_manager(user, manager_user)

    def test_a_manager_in_a_cycle_opens_the_managers_below_them(self):
        for user, manager_user, members in (
            (self.user_chief, self.user_dev1, self.user_dev2),
            (self.user_dev1, self.user_dev2, self.env["res.users"]),
            (self.user_left, self.user_right, self.user_left | self.user_below),
            (self.user_right, self.user_left, self.user_right | self.user_below),
        ):
            with self.subTest(user=user.name, manager=manager_user.name):
                self.assertEqual(self._members_by_manager(user, manager_user), set(members.ids))


@tagged("post_install", "-at_install")
class TestDashboardManagerInSeveralCompanies(ManagerModeCase):
    """A user has an employee in each of their companies, each with its own manager and team."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company_other = cls.env["res.company"].create({"name": "Other Company"})
        cls.user_manager, cls.user_viewer = cls.env["res.users"].create(
            [cls._user_vals(name, ["base.group_user"]) for name in ("Manager", "Viewer")]
        )
        (cls.user_manager | cls.user_viewer | cls.user_dev2).write({"company_ids": [Command.link(company_other.id)]})
        Employee = cls.env["hr.employee"]
        employee_viewer = Employee.create(
            {"name": "Viewer", "user_id": cls.user_viewer.id, "company_id": company_other.id}
        )
        # By name, the employee of this company comes first: the one a single-employee lookup would pick.
        employee_manager, employee_manager_other = Employee.create(
            [
                {"name": "Manager", "user_id": cls.user_manager.id},
                {
                    "name": "Manager in Other Company",
                    "user_id": cls.user_manager.id,
                    "company_id": company_other.id,
                    "parent_id": employee_viewer.id,
                },
            ]
        )
        cls.employee_dev1.parent_id = employee_manager
        Employee.create(
            {
                "name": "Dev Two in Other Company",
                "user_id": cls.user_dev2.id,
                "company_id": company_other.id,
                "parent_id": employee_manager_other.id,
            }
        )
        cls._create_open_tasks(cls.user_dev1 | cls.user_dev2)

    def test_the_team_gathers_the_employees_below_the_manager_in_every_company(self):
        self.assertEqual(
            self._members_by_manager(self.user_manager, self.user_manager), set((self.user_dev1 | self.user_dev2).ids)
        )

    def test_a_user_above_one_employee_of_the_manager_opens_the_whole_team(self):
        self.assertEqual(
            self._members_by_manager(self.user_viewer, self.user_manager), set((self.user_dev1 | self.user_dev2).ids)
        )
