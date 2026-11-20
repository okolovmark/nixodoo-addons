from datetime import date
from unittest.mock import patch

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from ..models.project_team_board import PERIODS
from .common import TeamDashboardCommon

# Late on Sunday 31 May 2026 in UTC it is Monday 1 June in Manila: every period differs between the two.
NOW = "2026-05-31 23:30:00"
PERIOD_DATES = {
    "Asia/Manila": {
        "this_week": ("2026-06-01", "2026-06-07"),
        "last_week": ("2026-05-25", "2026-05-31"),
        "this_month": ("2026-06-01", "2026-06-30"),
    },
    "UTC": {
        "this_week": ("2026-05-25", "2026-05-31"),
        "last_week": ("2026-05-18", "2026-05-24"),
        "this_month": ("2026-05-01", "2026-05-31"),
    },
}
SUBJECT_FIELDS = {"project": "project_id", "manager": "manager_user_id", "department": "department_id"}
PROJECT_GROUPS = ["project.group_project_user", "hr_timesheet.group_hr_timesheet_user"]
COMPANY_RULES = ("hr.hr_employee_comp_rule", "hr.hr_employee_public_comp_rule", "hr.hr_dept_comp_rule")


class TeamBoardCase(TeamDashboardCommon):
    @classmethod
    def _create_open_tasks(cls, users):
        cls.env["project.task"].create(
            [
                {
                    "name": "Board task of %s" % user.name,
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_in_progress.id,
                    "user_ids": [Command.set(user.ids)],
                    "planned_hours": 8.0,
                }
                for user in users
            ]
        )

    def _forbid_reading(self, model_name, user):
        # a database may grant the read to every user, e.g. analytic lines with Analytic Accounting on
        self.env["ir.model.access"].search(
            [
                ("model_id.model", "=", model_name),
                ("perm_read", "=", True),
                "|",
                ("group_id", "=", False),
                ("group_id", "in", user.groups_id.ids),
            ]
        ).active = False
        self.assertFalse(self.env[model_name].with_user(user).check_access_rights("read", raise_exception=False))

    def _boards(self, user, domain=()):
        return self.env["project.team.board"].with_user(user).search(list(domain))

    def _board(self, user, subject, mode, period="this_week"):
        return self._boards(
            user, [("mode", "=", mode), (SUBJECT_FIELDS[mode], "=", subject.id), ("period", "=", period)]
        )

    def _subjects(self, boards):
        return {(board.mode, board[SUBJECT_FIELDS[board.mode]].id) for board in boards}

    def _screen_data(self, user, mode, subject_id, date_start, date_end):
        Project = self.env["project.project"].with_user(user)
        screen_calls = {
            "project": Project.get_team_dashboard_data,
            "manager": Project.get_team_dashboard_by_manager,
            "department": Project.get_team_dashboard_by_department,
        }
        return screen_calls[mode](subject_id, date_start, date_end)

    def _screen_opens(self, user, board):
        subject_id = board[SUBJECT_FIELDS[board.mode]].id
        try:
            data = self._screen_data(user, board.mode, subject_id, "2026-05-25", "2026-05-31")
        except AccessError:
            return False
        return "error" not in data

    def _assert_rows_follow_the_screen(self, users, domain):
        boards = self.env["project.team.board"].search(domain + [("period", "=", "this_week")])
        self.assertTrue(boards)
        for user in users:
            found_boards = self._boards(user, domain)
            for board in boards:
                with self.subTest(user=user.name, row=board.name):
                    self.assertEqual(board in found_boards, self._screen_opens(user, board))


@tagged("post_install", "-at_install")
class TestTeamBoardData(TeamBoardCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_lead, cls.user_lead_utc = cls.env["res.users"].create(
            [
                dict(cls._user_vals("Board Lead", ["project.group_project_manager"]), tz="Asia/Manila"),
                dict(cls._user_vals("Board Lead UTC", ["project.group_project_manager"]), tz="UTC"),
            ]
        )
        employee_lead = cls.env["hr.employee"].create({"name": "Board Lead", "user_id": cls.user_lead.id})
        cls.department = cls.env["hr.department"].create({"name": "Board Department"})
        cls.employee_dev1.write({"parent_id": employee_lead.id, "department_id": cls.department.id})
        cls.employee_dev2.write({"parent_id": cls.employee_dev1.id, "department_id": cls.department.id})
        task_dev1, task_dev2 = cls.env["project.task"].create(
            [
                {
                    "name": "Board task of Dev One",
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_in_progress.id,
                    "user_ids": [Command.set(cls.user_dev1.ids)],
                    "planned_hours": 30.0,
                },
                {
                    "name": "Board task of Dev Two",
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_backlog.id,
                    "user_ids": [Command.set(cls.user_dev2.ids)],
                    "planned_hours": 12.0,
                },
            ]
        )
        cls.env["account.analytic.line"].create(
            [
                {
                    "name": "Work",
                    "project_id": cls.project.id,
                    "task_id": task.id,
                    "user_id": task.user_ids.id,
                    "unit_amount": hours,
                    "date": day,
                }
                for task, hours, day in (
                    (task_dev1, 3.0, date(2026, 5, 31)),
                    (task_dev1, 2.0, date(2026, 6, 2)),
                    (task_dev2, 4.0, date(2026, 5, 20)),
                )
            ]
        )
        cls.env["resource.calendar.leaves"].create(
            {
                "name": "Public holiday",
                "calendar_id": cls.env.company.resource_calendar_id.id,
                "date_from": "2026-06-03 00:00:00",
                "date_to": "2026-06-03 23:59:59",
            }
        )

    @freeze_time(NOW)
    def test_a_row_holds_what_the_screen_shows_for_its_subject_and_period(self):
        subjects = {"project": self.project, "manager": self.user_lead, "department": self.department}
        for mode, subject in subjects.items():
            for period, (date_start, date_end) in PERIOD_DATES["Asia/Manila"].items():
                with self.subTest(mode=mode, period=period):
                    board = self._board(self.user_lead, subject, mode, period)
                    self.assertEqual(len(board), 1)
                    self.assertEqual(
                        board.data, self._screen_data(self.user_lead, mode, subject.id, date_start, date_end)
                    )
        last_week = self._board(self.user_lead, self.project, "project", "last_week").data
        card_dev1 = next(member for member in last_week["members"] if member["user_id"] == self.user_dev1.id)
        self.assertEqual(card_dev1["hours_logged_period"], 3.0)
        self.assertTrue(card_dev1["estimated_completion"])

    @freeze_time(NOW)
    def test_the_periods_fall_on_the_days_of_the_user_time_zone(self):
        for user in self.user_lead | self.user_lead_utc:
            for period, dates in PERIOD_DATES[user.tz].items():
                with self.subTest(tz=user.tz, period=period):
                    data = self._board(user, self.project, "project", period).data
                    self.assertEqual((data["period_start"], data["period_end"]), dates)

    def test_a_row_is_named_after_its_subject_and_period(self):
        for subject, mode, period, name in (
            (self.project, "project", "last_week", "Test AI-driven Project · Last Week"),
            (self.user_lead, "manager", "this_week", "Board Lead · This Week"),
            (self.department, "department", "this_month", "Board Department · This Month"),
        ):
            with self.subTest(name=name):
                self.assertEqual(self._board(self.user_lead, subject, mode, period).name, name)


@tagged("post_install", "-at_install")
class TestTeamBoardAccess(TeamBoardCase):
    """A row is found and read by whoever opens its dashboard on the screen, and by nobody else."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (
            cls.user_member,
            cls.user_boss,
            cls.user_peer,
            cls.user_approver,
            cls.user_hr_officer,
            cls.user_project_manager,
            cls.user_no_timesheets,
        ) = cls.env["res.users"].create(
            [
                cls._user_vals("Member", PROJECT_GROUPS),
                cls._user_vals("Boss", PROJECT_GROUPS),
                cls._user_vals("Peer", PROJECT_GROUPS),
                cls._user_vals("Approver", PROJECT_GROUPS + ["hr_timesheet.group_hr_timesheet_approver"]),
                cls._user_vals("HR Officer", PROJECT_GROUPS + ["hr.group_hr_user"]),
                cls._user_vals("Project Manager", ["project.group_project_manager"]),
                cls._user_vals("No Timesheets", ["project.group_project_user"]),
            ]
        )
        cls.department_own, cls.department_managed, cls.department_other = cls.env["hr.department"].create(
            [{"name": "Own Department"}, {"name": "Managed Department"}, {"name": "Other Department"}]
        )
        Employee = cls.env["hr.employee"]
        employee_boss = Employee.create(
            {"name": "Boss", "user_id": cls.user_boss.id, "department_id": cls.department_other.id}
        )
        employee_member, employee_peer = Employee.create(
            [
                {
                    "name": "Member",
                    "user_id": cls.user_member.id,
                    "parent_id": employee_boss.id,
                    "department_id": cls.department_own.id,
                },
                {
                    "name": "Peer",
                    "user_id": cls.user_peer.id,
                    "parent_id": employee_boss.id,
                    "department_id": cls.department_other.id,
                },
            ]
        )
        Employee.create(
            [
                {"name": "Report of Peer", "parent_id": employee_peer.id},
                {
                    "name": "No Timesheets",
                    "user_id": cls.user_no_timesheets.id,
                    "parent_id": employee_member.id,
                    "department_id": cls.department_own.id,
                },
            ]
        )
        cls.department_managed.manager_id = employee_member
        cls.employee_dev1.write({"parent_id": employee_member.id, "department_id": cls.department_managed.id})
        cls.employee_dev2.write({"parent_id": cls.employee_dev1.id, "department_id": cls.department_own.id})
        cls.project_private = cls.env["project.project"].create(
            {"name": "Private Project", "privacy_visibility": "followers"}
        )
        cls._create_open_tasks(cls.user_dev1 | cls.user_dev2 | cls.user_no_timesheets)
        cls.env["project.task"].create(
            {
                "name": "Private task of Dev One",
                "project_id": cls.project_private.id,
                "stage_id": cls.stage_in_progress.id,
                "user_ids": [Command.set(cls.user_dev1.ids)],
                "planned_hours": 5.0,
            }
        )
        project_archived = cls.env["project.project"].create({"name": "Archived Project", "active": False})
        department_archived = cls.env["hr.department"].create({"name": "Archived Department", "active": False})
        cls.projects = cls.project | cls.project_plain | cls.project_private
        cls.managers = cls.user_boss | cls.user_member | cls.user_peer | cls.user_dev1
        cls.departments = cls.department_own | cls.department_managed | cls.department_other
        cls.fixture_domain = [
            "|",
            "|",
            ("project_id", "in", (cls.projects | project_archived).ids),
            ("manager_user_id", "in", cls.managers.ids),
            ("department_id", "in", (cls.departments | department_archived).ids),
        ]

    def _found_subjects(self, user):
        return self._subjects(self._boards(user, self.fixture_domain))

    def test_the_rows_are_the_projects_the_managers_and_the_departments(self):
        self.assertEqual(
            self._subjects(self.env["project.team.board"].search(self.fixture_domain)),
            {("project", project.id) for project in self.projects}
            | {("manager", user.id) for user in self.managers}
            | {("department", department.id) for department in self.departments},
        )

    def test_a_plain_user_finds_the_rows_of_their_own_teams_and_projects(self):
        self.assertEqual(
            self._found_subjects(self.user_member),
            {
                ("project", self.project.id),
                ("project", self.project_plain.id),
                ("manager", self.user_member.id),
                ("manager", self.user_dev1.id),
                ("department", self.department_own.id),
                ("department", self.department_managed.id),
            },
        )

    def test_approvers_and_hr_officers_find_every_team(self):
        every_team = {("manager", user.id) for user in self.managers} | {
            ("department", department.id) for department in self.departments
        }
        open_projects = {("project", self.project.id), ("project", self.project_plain.id)}
        for user in self.user_approver | self.user_hr_officer:
            with self.subTest(user=user.name):
                self.assertEqual(self._found_subjects(user), every_team | open_projects)

    def test_a_project_row_follows_the_visibility_of_its_project(self):
        self.assertIn(("project", self.project_private.id), self._found_subjects(self.user_project_manager))
        self.assertNotIn(("project", self.project_private.id), self._found_subjects(self.user_member))
        self.project_private.message_subscribe(partner_ids=self.user_member.partner_id.ids)
        self.assertIn(("project", self.project_private.id), self._found_subjects(self.user_member))

    def test_a_card_names_the_project_of_a_task_whose_project_its_viewer_cannot_read(self):
        self.assertFalse(self.project_private.with_user(self.user_dev1)._filter_access_rules("read"))
        # end to end: the board and the screen show the project's name to the viewer
        self.env.invalidate_all()
        board = self._board(self.user_dev1, self.department_managed, "department")
        card_dev1 = next(member for member in board.data["members"] if member["user_id"] == self.user_dev1.id)
        self.assertIn("Private Project", card_dev1["project_names"])
        tasks_dev1 = [card_dev1["current_task"], *card_dev1["queue"]]
        self.assertIn("Private Project", [task["project_name"] for task in tasks_dev1])
        # above, Odoo's check of the task rules fetched the project as superuser first: a card
        # built from a task alone shows whether the viewer reads the project's name
        task_private = self.env["project.task"].search([("project_id", "=", self.project_private.id)])
        self.env.invalidate_all()
        card_task = (
            self.env["project.project"]
            .with_user(self.user_dev1)
            ._task_to_dict(task_private.with_user(self.user_dev1), date(2026, 6, 1))
        )
        self.assertEqual(card_task["project_name"], "Private Project")

    def test_a_card_keys_the_parent_of_a_sub_task_its_viewer_cannot_read(self):
        parent_task = self.env["project.task"].create(
            {
                "name": "Private parent",
                "project_id": self.project_private.id,
                "user_ids": [Command.set(self.user_dev1.ids)],
            }
        )
        parent_task.key = "PRIV-1"
        self.env["project.task"].create(
            {
                "name": "Private sub-task of Member",
                "project_id": self.project_private.id,
                "parent_id": parent_task.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [Command.set(self.user_member.ids)],
            }
        )
        self.assertFalse(parent_task.with_user(self.user_member)._filter_access_rules("read"))
        self.env.invalidate_all()
        screen_data = self._screen_data(self.user_member, "department", self.department_own.id, None, None)
        card_member = next(member for member in screen_data["members"] if member["user_id"] == self.user_member.id)
        self.assertEqual(card_member["current_task"]["parent_key"], "PRIV-1")
        rows = self.env["project.team.board"].with_user(self.user_member).search_read(self.fixture_domain, ["data"])
        self.assertEqual(len(rows), len(PERIODS) * len(self._found_subjects(self.user_member)))

    def test_a_row_is_found_exactly_when_the_screen_opens_it(self):
        users = (
            self.user_member
            | self.user_boss
            | self.user_peer
            | self.user_dev1
            | self.user_dev2
            | self.user_approver
            | self.user_hr_officer
            | self.user_project_manager
            | self.user_no_timesheets
        )
        self._assert_rows_follow_the_screen(users, self.fixture_domain)

    def test_hidden_rows_stay_out_of_counts_and_groups(self):
        Board = self.env["project.team.board"].with_user(self.user_member)
        self.assertEqual(Board.search_count(self.fixture_domain), 18)
        groups = Board.read_group(self.fixture_domain, ["mode"], ["mode"])
        self.assertEqual({group["mode"]: group["mode_count"] for group in groups}, dict.fromkeys(SUBJECT_FIELDS, 6))
        self.assertEqual(Board.name_search("Boss", self.fixture_domain), [])

    def test_reading_a_hidden_row_raises(self):
        hidden_boards = self.env["project.team.board"].search(
            [
                "|",
                "|",
                ("project_id", "=", self.project_private.id),
                ("manager_user_id", "=", self.user_boss.id),
                ("department_id", "=", self.department_other.id),
            ]
        )
        self.assertEqual(len(hidden_boards), 9)
        for board in hidden_boards.with_user(self.user_member):
            with self.subTest(row=board.sudo().name):
                with self.assertRaises(AccessError):
                    board.read(["name"])
                with self.assertRaises(AccessError):
                    board.check_access_rule("read")

    def test_a_user_who_cannot_read_timesheets_finds_no_project_row(self):
        self._forbid_reading("account.analytic.line", self.user_no_timesheets)
        with self.assertRaises(AccessError):
            self.env["project.project"].with_user(self.user_no_timesheets).get_team_dashboard_data(self.project.id)
        self.assertEqual(self._found_subjects(self.user_no_timesheets), {("department", self.department_own.id)})

    @freeze_time(NOW)
    def test_a_project_row_reads_for_a_user_who_cannot_read_employees(self):
        for model_name in ("hr.employee", "hr.employee.public"):
            self._forbid_reading(model_name, self.user_member)
        board = self._board(self.user_member, self.project, "project")
        card_dev1 = next(member for member in board.data["members"] if member["user_id"] == self.user_dev1.id)
        self.assertTrue(card_dev1["estimated_completion"])

    @freeze_time(NOW)
    def test_a_row_holds_what_the_screen_shows_its_reader(self):
        board = self._board(self.user_member, self.user_member, "manager")
        screen_data = self._screen_data(
            self.user_member, "manager", self.user_member.id, *PERIOD_DATES["UTC"]["this_week"]
        )
        self.assertEqual(board.data, screen_data)
        card_dev1 = next(member for member in board.data["members"] if member["user_id"] == self.user_dev1.id)
        tasks_dev1 = [card_dev1["current_task"], *card_dev1["queue"]]
        self.assertEqual({task["project_id"] for task in tasks_dev1}, {self.project.id})

    @freeze_time(NOW)
    def test_every_row_a_user_finds_reads_with_its_data(self):
        for user in self.user_member | self.user_no_timesheets | self.user_approver | self.user_project_manager:
            with self.subTest(user=user.name):
                rows = self.env["project.team.board"].with_user(user).search_read(self.fixture_domain, ["name", "data"])
                self.assertGreaterEqual(len(rows), 3)
                self.assertEqual([row["name"] for row in rows if "error" in row["data"]], [])


@tagged("post_install", "-at_install")
class TestTeamBoardHierarchy(TeamBoardCase):
    """Odoo lets an employee manage themselves or their own manager, and archives an employee
    who still has subordinates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (
            cls.user_chief,
            cls.user_solo,
            cls.user_left,
            cls.user_right,
            cls.user_below,
            cls.user_top,
            cls.user_lower,
        ) = cls.env["res.users"].create(
            [
                cls._user_vals(name, PROJECT_GROUPS)
                for name in ("Chief", "Solo", "Left", "Right", "Below", "Top", "Lower")
            ]
        )
        Employee = cls.env["hr.employee"]
        employee_chief, employee_solo, employee_left, employee_right, employee_top = Employee.create(
            [
                {"name": user.name, "user_id": user.id}
                for user in cls.user_chief | cls.user_solo | cls.user_left | cls.user_right | cls.user_top
            ]
        )
        cls.employee_solo, cls.employee_top = employee_solo, employee_top
        employee_chief.parent_id = employee_chief
        employee_solo.parent_id = employee_solo
        cls.employee_dev1.parent_id = employee_chief
        cls.employee_dev2.parent_id = cls.employee_dev1
        employee_left.parent_id = employee_right
        employee_right.parent_id = employee_left
        employee_middle = Employee.create({"name": "Middle", "parent_id": employee_top.id})
        employee_lower = Employee.create(
            {"name": "Lower", "user_id": cls.user_lower.id, "parent_id": employee_middle.id}
        )
        Employee.create(
            [
                {"name": "Below", "user_id": cls.user_below.id, "parent_id": employee_left.id},
                {"name": "Lowest", "parent_id": employee_lower.id},
            ]
        )
        employee_middle.active = False
        cls._create_open_tasks(
            cls.user_dev1 | cls.user_dev2 | cls.user_chief | cls.user_left | cls.user_right | cls.user_below
        )
        cls.users = (
            cls.user_chief
            | cls.user_solo
            | cls.user_left
            | cls.user_right
            | cls.user_below
            | cls.user_top
            | cls.user_lower
            | cls.user_dev1
            | cls.user_dev2
        )
        cls.manager_domain = [("manager_user_id", "in", cls.users.ids)]

    def test_a_self_managed_employee_is_a_manager_only_with_another_subordinate(self):
        managers = self.env["project.team.board"].search(self.manager_domain).manager_user_id
        self.assertEqual(
            managers, self.user_chief | self.user_dev1 | self.user_left | self.user_right | self.user_lower
        )

    def test_a_change_of_the_hierarchy_shows_in_the_next_search(self):
        Board = self.env["project.team.board"]
        self.assertFalse(Board.search([("manager_user_id", "=", self.user_solo.id)]))
        self.employee_dev2.parent_id = self.employee_solo
        self.assertEqual(len(Board.search([("manager_user_id", "=", self.user_solo.id)])), 3)
        self.assertEqual(Board.with_user(self.user_solo).search(self.manager_domain).manager_user_id, self.user_solo)

    def test_each_user_finds_the_managers_below_them_once_around_a_loop(self):
        for user, managers in (
            (self.user_chief, self.user_chief | self.user_dev1),
            (self.user_dev1, self.user_dev1),
            (self.user_left, self.user_left | self.user_right),
            (self.user_right, self.user_right | self.user_left),
            (self.user_below, self.env["res.users"]),
            (self.user_top, self.user_lower),
        ):
            with self.subTest(user=user.name):
                self.assertEqual(self._boards(user, self.manager_domain).manager_user_id, managers)

    def test_a_manager_row_is_found_exactly_when_the_screen_opens_it(self):
        self._assert_rows_follow_the_screen(self.users, self.manager_domain)

    @freeze_time(NOW)
    def test_the_rows_around_a_loop_read_with_their_data(self):
        for user in self.user_chief | self.user_left:
            with self.subTest(user=user.name):
                rows = self.env["project.team.board"].with_user(user).search_read(self.manager_domain, ["data"])
                self.assertEqual(len(rows), 6)
                self.assertTrue(all(row["data"]["members"] for row in rows))

    def test_an_archived_employee_of_the_user_opens_no_row_whatever_the_active_test_of_the_search(self):
        department = self.env["hr.department"].create({"name": "Department of Top"})
        self.employee_top.write({"department_id": department.id, "active": False})
        Board = self.env["project.team.board"]
        for domain in ([("manager_user_id", "=", self.user_lower.id)], [("department_id", "=", department.id)]):
            self.assertEqual(len(Board.search(domain)), len(PERIODS))
            for active_test in (True, False):
                with self.subTest(domain=domain, active_test=active_test):
                    TopBoard = Board.with_user(self.user_top).with_context(active_test=active_test)
                    self.assertEqual(TopBoard.search_read(domain, ["data"]), [])


@tagged("post_install", "-at_install")
class TestTeamBoardCompanies(TeamBoardCase):
    """A user finds the teams of the companies of the context only, whatever the employees' and
    departments' company rules: the departments by their company, the managers by the company of
    their employee."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Board Company B"})
        approver_groups = PROJECT_GROUPS + ["hr_timesheet.group_hr_timesheet_approver"]
        cls.user_approver, cls.user_hr_officer, cls.user_approver_both, cls.user_manager_b = cls.env[
            "res.users"
        ].create(
            [
                cls._user_vals("Approver in A", approver_groups),
                cls._user_vals("HR Officer in A", PROJECT_GROUPS + ["hr.group_hr_user"]),
                dict(
                    cls._user_vals("Approver in A and B", approver_groups),
                    company_ids=[Command.set((company_a | cls.company_b).ids)],
                ),
                dict(
                    cls._user_vals("Manager in B", PROJECT_GROUPS),
                    company_id=cls.company_b.id,
                    company_ids=[Command.set(cls.company_b.ids)],
                ),
            ]
        )
        cls.department_a, cls.department_b = cls.env["hr.department"].create(
            [
                {"name": "Department in A", "company_id": company_a.id},
                {"name": "Department in B", "company_id": cls.company_b.id},
            ]
        )
        cls.employee_dev1.department_id = cls.department_a
        cls.employee_dev2.parent_id = cls.employee_dev1
        employee_manager_b = cls.env["hr.employee"].create(
            {
                "name": "Manager in B",
                "user_id": cls.user_manager_b.id,
                "company_id": cls.company_b.id,
                "department_id": cls.department_b.id,
            }
        )
        cls.env["hr.employee"].create(
            {"name": "Report in B", "company_id": cls.company_b.id, "parent_id": employee_manager_b.id}
        )
        cls.company_rules = cls.env["ir.rule"].union(*(cls.env.ref(xmlid) for xmlid in COMPANY_RULES))
        # a database may switch them off: the board follows them either way (the row-follows-screen test)
        cls.company_rules.active = True
        cls.teams_a = {("manager", cls.user_dev1.id), ("department", cls.department_a.id)}
        cls.teams_b = {("manager", cls.user_manager_b.id), ("department", cls.department_b.id)}
        cls.team_domain = [
            "|",
            ("manager_user_id", "in", (cls.user_dev1 | cls.user_manager_b).ids),
            ("department_id", "in", (cls.department_a | cls.department_b).ids),
        ]

    def test_approvers_and_hr_officers_find_the_teams_of_their_companies_only(self):
        for user in self.user_approver | self.user_hr_officer:
            with self.subTest(user=user.name):
                self.assertEqual(self._subjects(self._boards(user, self.team_domain)), self.teams_a)
        Board = self.env["project.team.board"].with_user(self.user_approver_both)
        self.assertEqual(self._subjects(Board.search(self.team_domain)), self.teams_a | self.teams_b)
        boards_in_a = Board.with_context(allowed_company_ids=self.env.company.ids).search(self.team_domain)
        self.assertEqual(self._subjects(boards_in_a), self.teams_a)

    def test_the_screen_does_not_open_the_teams_of_another_company_whatever_the_company_rules(self):
        project_models = [
            self.env["project.project"].with_user(user) for user in self.user_approver | self.user_hr_officer
        ]
        project_models.append(
            self.env["project.project"]
            .with_user(self.user_approver_both)
            .with_context(allowed_company_ids=self.env.company.ids)
        )
        self.assertEqual(
            [self.company_b in Project.env.user.company_ids for Project in project_models], [False] * 2 + [True]
        )
        for is_active in (True, False):
            self.company_rules.active = is_active
            for Project in project_models:
                # the team of a company the user cannot select is answered as if it did not exist
                may_select_b = self.company_b in Project.env.user.company_ids
                with self.subTest(company_rules_active=is_active, user=Project.env.user.name):
                    self.assertEqual(
                        Project.get_team_dashboard_by_manager(self.user_manager_b.id),
                        {
                            "error": "No employee record found for this user in the selected companies"
                            if may_select_b
                            else "No employee record found for this user",
                            "manager_name": "Manager in B",
                        },
                    )
                    self.assertEqual(
                        Project.get_team_dashboard_by_department(self.department_b.id),
                        {
                            "error": "Department not in the selected companies"
                            if may_select_b
                            else "Department not found"
                        },
                    )
                    Board = self.env["project.team.board"].with_env(Project.env)
                    self.assertEqual(self._subjects(Board.search(self.team_domain)), self.teams_a)

    def test_the_departments_of_the_selected_companies_open_whatever_the_company_rules(self):
        department_without_company = self.env["hr.department"].create({"name": "Shared", "company_id": False})
        departments = self.department_a | self.department_b | department_without_company
        for user, error_b in (
            (self.user_approver, "Department not found"),
            (self.user_approver_both, "Department not in the selected companies"),
        ):
            Project = self.env["project.project"].with_user(user).with_context(allowed_company_ids=self.env.company.ids)
            Board = self.env["project.team.board"].with_env(Project.env)
            for is_active in (True, False):
                self.company_rules.active = is_active
                with self.subTest(user=user.name, company_rules_active=is_active):
                    self.assertEqual(Project.get_team_dashboard_by_department(self.department_b.id), {"error": error_b})
                    self.assertNotIn("error", Project.get_team_dashboard_by_department(department_without_company.id))
                    boards = Board.search(
                        [
                            ("mode", "=", "department"),
                            ("department_id", "in", departments.ids),
                            ("period", "=", "this_week"),
                        ]
                    )
                    self.assertEqual(boards.department_id, self.department_a | department_without_company)

    def test_a_plain_user_opens_their_own_teams_in_the_selected_companies_only_whatever_the_company_rules(self):
        companies = self.env.company | self.company_b
        user_lead, user_below = self.env["res.users"].create(
            [
                dict(self._user_vals(name, PROJECT_GROUPS), company_ids=[Command.set(companies.ids)])
                for name in ("Lead in A and B", "Below the Lead in B")
            ]
        )
        Employee = self.env["hr.employee"]
        Employee.create({"name": "Lead in A", "user_id": user_lead.id})
        employee_lead_b = Employee.create(
            {
                "name": "Lead in B",
                "user_id": user_lead.id,
                "company_id": self.company_b.id,
                "department_id": self.department_b.id,
            }
        )
        Employee.create(
            {
                "name": "Below the Lead in B",
                "user_id": user_below.id,
                "company_id": self.company_b.id,
                "parent_id": employee_lead_b.id,
            }
        )
        Project = self.env["project.project"].with_user(user_lead)
        department_mode = [("mode", "=", "department"), ("department_id", "=", self.department_b.id)]
        for is_active in (True, False):
            self.company_rules.active = is_active
            with self.subTest(company_rules_active=is_active, companies="A and B"):
                ProjectInAAndB = Project.with_context(allowed_company_ids=companies.ids)
                self.assertNotIn("error", ProjectInAAndB.get_team_dashboard_by_manager(user_below.id))
                self.assertNotIn("error", ProjectInAAndB.get_team_dashboard_by_department(self.department_b.id))
                self.assertTrue(self.env["project.team.board"].with_env(ProjectInAAndB.env).search(department_mode))
            with self.subTest(company_rules_active=is_active, companies="A"):
                ProjectInA = Project.with_context(allowed_company_ids=self.env.company.ids)
                self.assertNotIn("error", ProjectInA.get_team_dashboard_by_manager(user_lead.id))
                self.assertEqual(
                    ProjectInA.get_team_dashboard_by_manager(user_below.id),
                    {
                        "error": "No employee record found for this user in the selected companies",
                        "manager_name": "Below the Lead in B",
                    },
                )
                with self.assertRaisesRegex(AccessError, "by manager"):
                    ProjectInA.get_team_dashboard_by_manager(self.user_dev1.id)
                self.assertEqual(
                    ProjectInA.get_team_dashboard_by_department(self.department_b.id),
                    {"error": "Department not in the selected companies"},
                )
                self.assertFalse(self.env["project.team.board"].with_env(ProjectInA.env).search(department_mode))

    def test_a_plain_user_opens_their_department_of_no_company_whatever_the_company_rules(self):
        department_without_company = self.env["hr.department"].create({"name": "Shared", "company_id": False})
        user_member = self.env["res.users"].create(self._user_vals("Member of Shared", PROJECT_GROUPS))
        self.env["hr.employee"].create(
            {"name": "Member of Shared", "user_id": user_member.id, "department_id": department_without_company.id}
        )
        Project = self.env["project.project"].with_user(user_member)
        for is_active in (True, False):
            self.company_rules.active = is_active
            with self.subTest(company_rules_active=is_active):
                self.assertNotIn("error", Project.get_team_dashboard_by_department(department_without_company.id))
                self.assertTrue(self._board(user_member, department_without_company, "department"))

    def test_a_plain_user_opens_a_department_they_manage_in_the_selected_companies_only(self):
        companies = self.env.company | self.company_b
        user_head = self.env["res.users"].create(
            dict(self._user_vals("Head of Department in B", PROJECT_GROUPS), company_ids=[Command.set(companies.ids)])
        )
        Employee = self.env["hr.employee"]
        Employee.create({"name": "Head in A", "user_id": user_head.id})
        self.department_b.manager_id = Employee.create(
            {"name": "Head in B", "user_id": user_head.id, "company_id": self.company_b.id}
        )
        Project = self.env["project.project"].with_user(user_head)
        department_mode = [("mode", "=", "department"), ("department_id", "=", self.department_b.id)]
        for is_active in (True, False):
            self.company_rules.active = is_active
            with self.subTest(company_rules_active=is_active):
                ProjectInAAndB = Project.with_context(allowed_company_ids=companies.ids)
                self.assertNotIn("error", ProjectInAAndB.get_team_dashboard_by_department(self.department_b.id))
                ProjectInA = Project.with_context(allowed_company_ids=self.env.company.ids)
                self.assertEqual(
                    ProjectInA.get_team_dashboard_by_department(self.department_b.id),
                    {"error": "Department not in the selected companies"},
                )
                self.assertFalse(self.env["project.team.board"].with_env(ProjectInA.env).search(department_mode))

    def test_a_plain_user_is_told_their_own_department_of_a_company_they_cannot_select_is_not_found(self):
        user_member = self.env["res.users"].create(self._user_vals("Member in B, user in A", PROJECT_GROUPS))
        self.env["hr.employee"].create(
            {
                "name": "Member in B",
                "user_id": user_member.id,
                "company_id": self.company_b.id,
                "department_id": self.department_b.id,
            }
        )
        self.assertNotIn(self.company_b, user_member.company_ids)
        department_other_b = self.env["hr.department"].create(
            {"name": "Other Department in B", "company_id": self.company_b.id}
        )
        Project = self.env["project.project"].with_user(user_member)
        for is_active in (True, False):
            self.company_rules.active = is_active
            with self.subTest(company_rules_active=is_active):
                self.assertEqual(
                    Project.get_team_dashboard_by_department(self.department_b.id), {"error": "Department not found"}
                )
                self.assertFalse(self._board(user_member, self.department_b, "department"))
                with self.assertRaisesRegex(AccessError, "by department"):
                    Project.get_team_dashboard_by_department(department_other_b.id)

    def test_a_plain_user_is_told_a_manager_reporting_to_them_has_no_employee_in_the_selected_companies(self):
        user_lead_a_b, user_lead_a, user_report_a_b, user_report_a = self.env["res.users"].create(
            [
                dict(
                    self._user_vals("Lead in A and B", PROJECT_GROUPS),
                    company_ids=[Command.set((self.env.company | self.company_b).ids)],
                ),
                self._user_vals("Lead in A", PROJECT_GROUPS),
                *(
                    dict(
                        self._user_vals(name, PROJECT_GROUPS),
                        company_id=self.company_b.id,
                        company_ids=[Command.set(self.company_b.ids)],
                    )
                    for name in ("Report of the Lead in A and B", "Report of the Lead in A")
                ),
            ]
        )
        Employee = self.env["hr.employee"]
        for user_lead, user_report in ((user_lead_a_b, user_report_a_b), (user_lead_a, user_report_a)):
            employee_lead = Employee.create(
                {"name": user_lead.name, "user_id": user_lead.id, "company_id": self.company_b.id}
            )
            employee_report = Employee.create(
                {
                    "name": user_report.name,
                    "user_id": user_report.id,
                    "company_id": self.company_b.id,
                    "parent_id": employee_lead.id,
                }
            )
            Employee.create(
                {
                    "name": "Team of %s" % user_report.name,
                    "company_id": self.company_b.id,
                    "parent_id": employee_report.id,
                }
            )
        report_boards = [("mode", "=", "manager"), ("manager_user_id", "in", (user_report_a_b | user_report_a).ids)]
        self.assertEqual(len(self.env["project.team.board"].search(report_boards)), 2 * len(PERIODS))
        for is_active in (True, False):
            self.company_rules.active = is_active
            for user_lead, user_report, error in (
                (user_lead_a_b, user_report_a_b, "No employee record found for this user in the selected companies"),
                (user_lead_a, user_report_a, "No employee record found for this user"),
            ):
                with self.subTest(company_rules_active=is_active, user=user_lead.name):
                    ProjectInA = (
                        self.env["project.project"]
                        .with_user(user_lead)
                        .with_context(allowed_company_ids=self.env.company.ids)
                    )
                    self.assertEqual(
                        ProjectInA.get_team_dashboard_by_manager(user_report.id),
                        {"error": error, "manager_name": user_report.name},
                    )
                    self.assertFalse(self.env["project.team.board"].with_env(ProjectInA.env).search(report_boards))
                    with self.assertRaisesRegex(AccessError, "by manager"):
                        ProjectInA.get_team_dashboard_by_manager(self.user_manager_b.id)

    def test_a_user_opens_their_own_team_whatever_the_company_of_their_employees(self):
        companies = self.env.company | self.company_b
        user_lead = self.env["res.users"].create(
            dict(
                self._user_vals("Lead with an employee in B", PROJECT_GROUPS), company_ids=[Command.set(companies.ids)]
            )
        )
        employee_lead = self.env["hr.employee"].create(
            {"name": "Lead in B", "user_id": user_lead.id, "company_id": self.company_b.id}
        )
        self.user_dev1.company_ids = [Command.link(self.company_b.id)]
        self.env["hr.employee"].create(
            {
                "name": "Dev One in B",
                "user_id": self.user_dev1.id,
                "company_id": self.company_b.id,
                "parent_id": employee_lead.id,
            }
        )
        self._create_open_tasks(self.user_dev1)
        ProjectInA = (
            self.env["project.project"].with_user(user_lead).with_context(allowed_company_ids=self.env.company.ids)
        )
        for is_active in (True, False):
            self.company_rules.active = is_active
            with self.subTest(company_rules_active=is_active):
                data = ProjectInA.get_team_dashboard_by_manager(user_lead.id)
                self.assertEqual([member["user_id"] for member in data["members"]], self.user_dev1.ids)
                self.assertTrue(
                    self.env["project.team.board"]
                    .with_env(ProjectInA.env)
                    .search([("mode", "=", "manager"), ("manager_user_id", "=", user_lead.id)])
                )

    def test_an_archived_employee_opens_no_team_of_another_company_whatever_the_active_test(self):
        self.env["hr.employee"].create(
            {"name": "Manager in B, archived in A", "user_id": self.user_manager_b.id, "active": False}
        )
        for is_active in (True, False):
            self.company_rules.active = is_active
            for user in self.user_approver | self.user_hr_officer:
                for active_test in (True, False):
                    with self.subTest(company_rules_active=is_active, user=user.name, active_test=active_test):
                        Board = self.env["project.team.board"].with_user(user).with_context(active_test=active_test)
                        self.assertEqual(self._subjects(Board.search(self.team_domain)), self.teams_a)
                        Project = self.env["project.project"].with_user(user).with_context(active_test=active_test)
                        self.assertEqual(
                            Project.get_team_dashboard_by_manager(self.user_manager_b.id),
                            {"error": "No employee record found for this user", "manager_name": "Manager in B"},
                        )

    def test_a_row_is_found_exactly_when_the_screen_opens_it(self):
        users = self.user_approver | self.user_hr_officer | self.user_approver_both | self.user_manager_b
        for is_active in (True, False):
            self.company_rules.active = is_active
            with self.subTest(company_rules_active=is_active):
                self._assert_rows_follow_the_screen(users, self.team_domain)


@tagged("post_install", "-at_install")
class TestTeamBoardModel(TeamBoardCase):
    def test_reading_every_field_of_a_project_leaves_the_board_alone(self):
        Board = self.registry["project.team.board"]
        user_reader = self.env["res.users"].create(self._user_vals("Reader", PROJECT_GROUPS))
        with patch.object(Board, "_compute_data", autospec=True, side_effect=Board._compute_data) as compute_data:
            self.project.read()
            self.project.with_user(user_reader).read()
            self.assertFalse(compute_data.called)
            self.env["project.team.board"].search([("project_id", "=", self.project.id)]).read(["data"])
            self.assertTrue(compute_data.called)

    def test_a_read_naming_no_fields_lists_the_rows_without_their_data(self):
        Board = self.registry["project.team.board"]
        user_reader = self.env["res.users"].create(self._user_vals("Blank Reader", PROJECT_GROUPS))
        board_reader = self.env["project.team.board"].with_user(user_reader)
        domain = [("project_id", "=", self.project.id)]
        with patch.object(Board, "_compute_data", autospec=True, side_effect=Board._compute_data) as compute_data:
            for rows in (board_reader.search(domain).read(), board_reader.search_read(domain)):
                self.assertEqual(len(rows), 3)
                self.assertEqual([row["name"] for row in rows if "data" in row], [])
            self.assertFalse(compute_data.called)
            rows = board_reader.search_read(domain, ["name", "data"])
            self.assertTrue(compute_data.called)
        self.assertEqual([row["name"] for row in rows if "members" not in row["data"]], [])

    def test_the_data_follows_the_reader_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.project.with_context(lang="fr_FR").name = "Projet piloté par IA"
        board = self.env["project.team.board"].search(
            [("project_id", "=", self.project.id), ("period", "=", "this_week")]
        )
        self.assertEqual(board.with_context(lang="en_US").data["project_name"], "Test AI-driven Project")
        self.assertEqual(board.with_context(lang="fr_FR").data["project_name"], "Projet piloté par IA")

    def test_a_row_id_follows_its_subject_mode_and_period(self):
        Board = self.env["project.team.board"]
        department = self.env["hr.department"].create({"name": "Numbered Department"})
        boards = Board.search(["|", ("project_id", "=", self.project.id), ("department_id", "=", department.id)])
        self.assertEqual(
            {(board.mode, board.period): board.id for board in boards},
            {
                ("project", "this_week"): self.project.id * 9 + 1,
                ("project", "last_week"): self.project.id * 9 + 2,
                ("project", "this_month"): self.project.id * 9 + 3,
                ("department", "this_week"): department.id * 9 + 7,
                ("department", "last_week"): department.id * 9 + 8,
                ("department", "this_month"): department.id * 9 + 9,
            },
        )
        more_periods = [*PERIODS, ("last_month", "Last Month")]
        with patch("odoo.addons.project_team_dashboard.models.project_team_board.PERIODS", more_periods):
            query = Board._get_query()
        self.env.cr.execute("SELECT id, COALESCE(project_id, manager_user_id, department_id) FROM (%s) board" % query)
        rows = self.env.cr.fetchall()
        self.assertEqual(len({board_id for board_id, _subject_id in rows}), len(rows))
        rows_per_subject = 3 * len(more_periods)
        self.assertEqual(
            [board_id for board_id, subject_id in rows if (board_id - 1) // rows_per_subject != subject_id], []
        )

    def test_a_row_keeps_its_id_when_other_subjects_come(self):
        Board = self.env["project.team.board"]
        department = self.env["hr.department"].create({"name": "Zeta Department"})
        domain = ["|", ("project_id", "=", self.project.id), ("department_id", "=", department.id)]
        board_ids = Board.search(domain).ids
        self.env["project.project"].create({"name": "Alpha Project"})
        self.env["hr.department"].create({"name": "Alpha Department"})
        self.assertEqual(Board.search(domain).ids, board_ids)

    def test_the_view_is_built_again_on_update(self):
        Board = self.env["project.team.board"]
        rows = Board.search_read([], ["name", "mode", "project_id", "manager_user_id", "department_id", "period"])
        self.assertTrue(rows)
        Board.init()
        self.assertEqual(
            Board.search_read([], ["name", "mode", "project_id", "manager_user_id", "department_id", "period"]), rows
        )
