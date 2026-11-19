from unittest.mock import patch

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

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
                    (task_dev1, 3.0, "2026-05-31"),
                    (task_dev1, 2.0, "2026-06-02"),
                    (task_dev2, 4.0, "2026-05-20"),
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
        self.assertEqual((card_dev1["hours_logged_period"], card_dev1["hours_logged_today"]), (3.0, 3.0))
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
        self.assertEqual(Board.name_search("Boss"), [])

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
                    board.read(["data"])
                with self.assertRaises(AccessError):
                    board.check_access_rule("read")

    def test_a_user_who_cannot_read_timesheets_finds_no_project_row(self):
        with self.assertRaises(AccessError):
            self.env["project.project"].with_user(self.user_no_timesheets).get_team_dashboard_data(self.project.id)
        self.assertEqual(self._found_subjects(self.user_no_timesheets), {("department", self.department_own.id)})

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
                rows = self.env["project.team.board"].with_user(user).search_read([], ["name", "data"])
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
        cls.employee_solo = employee_solo
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
