from datetime import datetime, timedelta

from odoo import Command
from odoo.tests.common import tagged

from .common import TimesheetSystrayCase

RUNNING_TIMER_FIELDS = (
    "id",
    "task_id",
    "task_name",
    "task_key",
    "is_task_readable",
    "project_id",
    "project_name",
    "description",
    "date_time",
    "unit_amount",
    "elapsed_seconds",
)


@tagged("post_install", "-at_install")
class TestTimesheetSystrayData(TimesheetSystrayCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.task_in_progress = cls._create_task("Active Task", cls.stage_in_progress, cls.user_dev1)
        cls.task_backlog = cls._create_task("Backlog Task", cls.stage_backlog, cls.user_dev1)
        cls.task_done = cls._create_task("Done Task", cls.stage_done, cls.user_dev1)
        cls.task_dev2 = cls._create_task("Dev2 Task", cls.stage_in_progress, cls.user_dev2)

    def _running_lines(self, user):
        return self.env["account.analytic.line"].with_user(user)._get_own_running_lines()

    def _listed_tasks(self, user):
        result = self._call_route(user, "get_systray_timesheet", limit=0)
        return self.env["project.task"].browse([task_data["task_id"] for task_data in result["tasks"]])

    def test_running_timer_detected(self):
        line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now() - timedelta(minutes=30),
        )
        self.assertEqual(self._running_lines(self.user_dev1), line, "a line of no hours with a start time runs")

    def test_completed_timer_not_running(self):
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.5,
            date_time=datetime.now() - timedelta(hours=2),
        )
        self.assertFalse(self._running_lines(self.user_dev1), "a line with hours logged no longer runs")

    def test_running_timer_user_isolation(self):
        line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now(),
        )
        self.assertEqual(self._running_lines(self.user_dev1), line)
        self.assertFalse(self._running_lines(self.user_dev2), "Dev2 should not see dev1's running timer")

    def test_a_zero_hour_line_of_a_project_with_timesheets_off_runs_only_as_the_latest_line_of_the_user(self):
        project_off = self.env["project.project"].create({"name": "Project Turning Timesheets Off"})
        task_off = self.env["project.task"].create(
            {"name": "Task Turning Timesheets Off", "project_id": project_off.id}
        )
        line_off = self._create_timesheet(
            task_off,
            self.user_dev1,
            self.employee_dev1,
            date_time=datetime.now() - timedelta(days=2),
            logged_by=self.user_dev1,
        )
        line_on = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            date_time=datetime.now() - timedelta(days=3),
            logged_by=self.user_dev1,
        )
        project_off.allow_timesheets = False
        lines_dev1 = self.env["account.analytic.line"].with_user(self.user_dev1)

        self.assertEqual(
            lines_dev1._get_own_running_lines(), line_off + line_on, "the timer ran when Timesheets went off"
        )
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1,
            date_time=datetime.now() - timedelta(days=1),
            logged_by=self.user_dev1,
        )
        self.assertEqual(
            lines_dev1._get_own_running_lines(),
            line_on,
            "a line the user logged time after was forgotten; with Timesheets on, OCA's running timer stays",
        )

    def test_a_timer_of_a_project_with_timesheets_off_runs_on_after_lines_the_user_did_not_log(self):
        project_off = self.env["project.project"].create({"name": "Project Turning Timesheets Off"})
        task_off = self.env["project.task"].create(
            {"name": "Task Turning Timesheets Off", "project_id": project_off.id}
        )
        line_off = self._create_timesheet(
            task_off,
            self.user_dev1,
            self.employee_dev1,
            date_time=datetime.now() - timedelta(days=2),
            logged_by=self.user_dev1,
        )
        line_off_of_another = self._create_timesheet(
            task_off, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(hours=12)
        )
        project_off.allow_timesheets = False
        # An approver logs a line for the user yesterday; the user puts a time off on next week.
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=8,
            date_time=datetime.now() - timedelta(days=1),
        )
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=8,
            date_time=datetime.now() + timedelta(days=7),
            logged_by=self.user_dev1,
        )

        running_lines = self.env["account.analytic.line"].with_user(self.user_dev1)._get_own_running_lines()

        self.assertEqual(running_lines, line_off, "only the user's own timer runs on")
        self.assertNotIn(line_off_of_another, running_lines)

    def test_of_timers_started_in_the_same_second_the_one_created_last_comes_first(self):
        started = datetime.now().replace(microsecond=0) - timedelta(minutes=10)
        task_line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1, date_time=started)
        line_without_task = self._create_running_line_without_task(
            self.project, self.user_dev1, self.employee_dev1, date_time=started
        )

        running_lines = self.env["account.analytic.line"].with_user(self.user_dev1)._get_own_running_lines()

        self.assertEqual(
            running_lines.ids,
            [line_without_task.id, task_line.id],
            "a tie on the start time is broken the same way on every refresh of the Timelog",
        )

    def test_in_progress_tasks_exclude_folded(self):
        self.assertEqual(
            self._listed_tasks(self.user_dev1),
            self.task_in_progress + self.task_backlog,
            "the user's tasks in unfolded stages; the Done (folded) one must not appear",
        )

    def test_in_progress_tasks_user_specific(self):
        self.assertEqual(self._listed_tasks(self.user_dev2), self.task_dev2, "only the tasks the user is assigned to")

    def test_task_sorting_by_last_timelog(self):
        task_never_logged = self._create_task("Never Logged Task", self.stage_in_progress, self.user_dev1)
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.0,
            date_time=datetime.now() - timedelta(days=10),
        )
        self._create_timesheet(
            self.task_backlog,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.0,
            date_time=datetime.now() - timedelta(days=5),
        )
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=2.0,
            date_time=datetime.now() - timedelta(hours=1),
        )

        result = self._call_route(self.user_dev1, "get_systray_timesheet", limit=0)

        self.assertEqual(
            [task_data["task_id"] for task_data in result["tasks"]],
            [self.task_in_progress.id, self.task_backlog.id, task_never_logged.id],
            "the task logged last comes first, by its latest line; a task never logged after",
        )

    def test_task_limit(self):
        extra_tasks = self.env["project.task"]
        for index in range(6):
            extra_tasks += self._create_task("Extra Task %s" % index, self.stage_in_progress, self.user_dev1)
        open_task_count = len(self.task_in_progress + self.task_backlog + extra_tasks)
        # The oldest task comes last in project.task's order: a limit in the search would leave it out.
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.0,
            date_time=datetime.now() - timedelta(days=1),
        )

        first_tasks = self._call_route(self.user_dev1, "get_systray_timesheet")
        all_tasks = self._call_route(self.user_dev1, "get_systray_timesheet", limit=0)

        self.assertEqual(
            (len(first_tasks["tasks"]), first_tasks["total_tasks"]),
            (5, open_task_count),
            "five tasks by default, all counted",
        )
        self.assertEqual(
            first_tasks["tasks"][0]["task_id"], self.task_in_progress.id, "the limit cuts the list after its sort"
        )
        self.assertEqual(
            (len(all_tasks["tasks"]), all_tasks["total_tasks"]),
            (open_task_count, open_task_count),
            "limit 0 returns them all",
        )
        self.assertEqual(first_tasks["tasks"], all_tasks["tasks"][:5])

    def test_start_timer_creates_running_line(self):
        started = self._call_route(self.user_dev1, "start_timer", task_id=self.task_in_progress.id)

        new_line = self.env["account.analytic.line"].browse(started["timesheet"]["id"])
        self.assertTrue(started["running"])
        self.assertEqual(
            (
                new_line.task_id,
                new_line.project_id,
                new_line.employee_id,
                new_line.user_id,
                new_line.name,
                new_line.unit_amount,
            ),
            (
                self.task_in_progress,
                self.project,
                self.employee_dev1,
                self.user_dev1,
                self.task_in_progress.name,
                0,
            ),
        )
        self.assertAlmostEqual(new_line.date_time, datetime.now(), delta=timedelta(minutes=1))
        self.assertEqual(self._running_lines(self.user_dev1), new_line)

    def test_start_timer_nonexistent_task_returns_error(self):
        running_line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1)
        deleted_task = self._create_task("Deleted Task", self.stage_in_progress, self.user_dev1)
        deleted_task_id = deleted_task.id
        deleted_task.unlink()

        answer = self._call_route(self.user_dev1, "start_timer", task_id=deleted_task_id)

        self.assertEqual(answer, {"error": "Task not found"})
        self.assertEqual(self._running_lines(self.user_dev1), running_line, "a refused start leaves the timer running")

    def test_start_timer_without_an_employee_in_the_task_company_returns_error(self):
        other_company = self.env["res.company"].create({"name": "Timelog Other Company"})
        user_without_employee = self.env["res.users"].create(
            self._project_user_vals("No Employee", "timesheet_systray_no_emp")
        )
        user_employed_elsewhere = self.env["res.users"].create(
            {
                **self._project_user_vals("Employed Elsewhere", "timesheet_systray_elsewhere"),
                "company_id": self.env.company.id,
                "company_ids": [Command.set((self.env.company + other_company).ids)],
            }
        )
        self.env["hr.employee"].create(
            {"name": "Employed Elsewhere", "user_id": user_employed_elsewhere.id, "company_id": other_company.id}
        )
        for user in (user_without_employee, user_employed_elsewhere):
            with self.subTest(user=user.login):
                answer = self._call_route(user, "start_timer", task_id=self.task_in_progress.id)

                self.assertEqual(answer, {"error": "No employee linked to your user"})
                self.assertFalse(self.env["account.analytic.line"].search([("user_id", "=", user.id)]))

    def test_stop_timer_ends_the_timer_and_answers_with_the_task_list(self):
        started = datetime.now().replace(microsecond=0) - timedelta(hours=2)
        line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1, date_time=started)

        stopped = self._call_route(self.user_dev1, "stop_timer", timesheet_id=line.id)

        self.assertAlmostEqual(line.unit_amount, 2.0, delta=0.05)
        self.assertEqual(
            (
                stopped["running"],
                stopped["total_tasks"],
                [(task_data["task_id"], task_data["last_timelog"]) for task_data in stopped["tasks"]],
            ),
            (
                False,
                len(self.task_in_progress + self.task_backlog),
                [(self.task_in_progress.id, str(started)), (self.task_backlog.id, "")],
            ),
            "no timer runs on, so the task list, the task just logged first by the start of its line",
        )

    def test_stop_timer_of_a_deleted_or_stopped_line_returns_error(self):
        running_line = self._create_timesheet(
            self.task_in_progress, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(hours=1)
        )
        logged_line = self._create_timesheet(
            self.task_backlog,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.5,
            date_time=datetime.now() - timedelta(hours=3),
        )
        deleted_line = self._create_timesheet(self.task_backlog, self.user_dev1, self.employee_dev1, unit_amount=1)
        deleted_line_id = deleted_line.id
        deleted_line.unlink()
        for timesheet_id in (deleted_line_id, logged_line.id):
            with self.subTest(timesheet_id=timesheet_id):
                answer = self._call_route(self.user_dev1, "stop_timer", timesheet_id=timesheet_id)

                self.assertEqual(answer, {"error": "Timer is not running"})
        self.assertEqual(
            (running_line.unit_amount, logged_line.unit_amount), (0, 1.5), "only the timer named is stopped"
        )

    def test_format_running_timesheet_structure(self):
        line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now() - timedelta(minutes=45),
            name="Pairing on the parser",
        )

        result = self._call_route(self.env.user, "_format_running_timesheet", line=line)

        self.assertEqual(set(result.keys()), set(RUNNING_TIMER_FIELDS))
        self.assertEqual(result["id"], line.id)
        self.assertEqual(result["task_id"], self.task_in_progress.id)
        self.assertEqual(result["task_name"], self.task_in_progress.name)
        self.assertEqual(result["project_id"], self.project.id)
        self.assertEqual(result["description"], "Pairing on the parser")
        self.assertEqual(result["unit_amount"], 0)
        self.assertGreater(result["elapsed_seconds"], 2600)
        self.assertLess(result["elapsed_seconds"], 2800)

    def test_the_card_description_is_cut_past_200_characters_with_an_ellipsis(self):
        line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1)
        long_text = "Revue de l'analyseur avec l'équipe, étape par étape. " * 20
        expected_descriptions = {
            "é" * 199: "é" * 199,
            "é" * 200: "é" * 200,
            "é" * 201: "é" * 199 + "…",
            "é" * 198 + " suite": "é" * 198 + "…",
            long_text: long_text[:199] + "…",
            "/": "",
        }
        for name, expected_description in expected_descriptions.items():
            with self.subTest(length=len(name)):
                line.name = name
                result = self._call_route(self.env.user, "_format_running_timesheet", line=line)
                self.assertEqual(result["description"], expected_description)

    def test_format_running_timesheet_project_label_with_key(self):
        self.project.key = "TST"

        line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1)
        result = self._call_route(self.env.user, "_format_running_timesheet", line=line)
        self.assertEqual(result["project_name"], "TST - %s" % self.project.name)

    def test_format_running_timesheet_project_label_without_key(self):
        self.project.key = False

        line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1)
        result = self._call_route(self.env.user, "_format_running_timesheet", line=line)
        self.assertEqual(result["project_name"], self.project.name)


@tagged("post_install", "-at_install")
class TestTimesheetSystrayTaskData(TimesheetSystrayCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project.key = "TST"
        # project_ai_driven hands out no task key in test mode unless the context asks for one.
        cls.task1 = cls._create_task("Task Alpha", cls.stage_in_progress, cls.user_dev1, test_project_key=True)
        cls.task2 = cls._create_task("Task Beta", cls.stage_other, cls.user_dev1, test_project_key=True)

    def test_task_data_dict_structure(self):
        result = self._call_route(self.user_dev1, "get_systray_timesheet", limit=0)

        self.assertEqual(
            {name: value for name, value in result.items() if name != "tasks"},
            {"running": False, "timesheet": False, "total_tasks": len(self.task1 + self.task2), "message": ""},
        )
        self.assertEqual(
            {task_data["task_id"]: task_data for task_data in result["tasks"]}[self.task1.id],
            {
                "task_id": self.task1.id,
                "task_name": self.task1.name,
                "task_key": self.task1.key,
                "project_id": self.project.id,
                "project_name": "TST - %s" % self.project.name,
                "stage_name": self.stage_in_progress.name,
                "last_timelog": "",
            },
        )

    def test_in_progress_task_list_project_label_with_key(self):
        result = self._call_route(self.user_dev1, "get_systray_timesheet", limit=0)

        self.assertTrue(self.task1.key and self.task2.key, "the tasks of a keyed project get keys")
        self.assertEqual(
            {task_data["task_id"]: (task_data["project_name"], task_data["task_key"]) for task_data in result["tasks"]},
            {
                self.task1.id: ("TST - %s" % self.project.name, self.task1.key),
                self.task2.id: ("TST - %s" % self.project.name, self.task2.key),
            },
        )

    def test_task_data_with_timelog(self):
        self.user_dev1.groups_id = [Command.link(self.env.ref("hr_timesheet.group_hr_timesheet_approver").id)]
        latest_start = datetime.now().replace(microsecond=0) - timedelta(hours=3)
        self._create_timesheet(
            self.task1, self.user_dev1, self.employee_dev1, unit_amount=1.0, date_time=latest_start - timedelta(hours=2)
        )
        self._create_timesheet(self.task1, self.user_dev1, self.employee_dev1, unit_amount=2.0, date_time=latest_start)
        self._create_timesheet(
            self.task1, self.user_dev2, self.employee_dev2, unit_amount=1.0, date_time=latest_start + timedelta(hours=2)
        )

        result = self._call_route(self.user_dev1, "get_systray_timesheet", limit=0)

        self.assertEqual(
            {task_data["task_id"]: task_data["last_timelog"] for task_data in result["tasks"]},
            {self.task1.id: str(latest_start), self.task2.id: ""},
            "the start of the user's latest logged line, not of a later one of another user an approver reads; "
            "none on a task they never logged",
        )

    def test_empty_tasks_message(self):
        user_empty = self.env["res.users"].create(self._project_user_vals("Empty User", "timesheet_systray_empty"))

        self.assertEqual(
            self._call_route(user_empty, "get_systray_timesheet"),
            {"running": False, "timesheet": False, "tasks": [], "total_tasks": 0, "message": "No tasks in progress"},
        )


@tagged("post_install", "-at_install")
class TestTimesheetSystrayRoutes(TimesheetSystrayCase):
    def test_start_timer_ends_the_running_timer_and_stop_timer_ends_the_new_one(self):
        task = self._create_task("Timed Task", self.stage_in_progress, self.user_dev1)
        next_task = self._create_task("Next Task", self.stage_in_progress, self.user_dev1)
        running_line = self._create_timesheet(
            task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(hours=1)
        )

        started = self._call_route(self.user_dev1, "start_timer", task_id=next_task.id)

        self.assertAlmostEqual(running_line.unit_amount, 1.0, delta=0.05, msg="the running timer must be ended")
        new_line = self.env["account.analytic.line"].browse(started["timesheet"]["id"])
        self.assertEqual(
            (new_line.task_id, new_line.employee_id, new_line.unit_amount), (next_task, self.employee_dev1, 0)
        )
        dev1_lines = self.env["account.analytic.line"].with_user(self.user_dev1)
        self.assertEqual(dev1_lines.search(dev1_lines._running_domain()), new_line)

        new_line.date_time = datetime.now() - timedelta(minutes=30)
        stopped = self._call_route(self.user_dev1, "stop_timer", timesheet_id=new_line.id)

        self.assertAlmostEqual(new_line.unit_amount, 0.5, delta=0.05)
        self.assertFalse(stopped["running"])
        self.assertFalse(dev1_lines.search(dev1_lines._running_domain()))

    def test_start_timer_ends_every_running_timer_of_the_user(self):
        task = self._create_task("Timed Task", self.stage_in_progress, self.user_dev1)
        next_task = self._create_task("Next Task", self.stage_in_progress, self.user_dev1)
        line_of_two_hours = self._create_timesheet(
            task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(hours=2)
        )
        line_of_one_hour = self._create_timesheet(
            next_task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(hours=1)
        )

        started = self._call_route(self.user_dev1, "start_timer", task_id=next_task.id)

        self.assertAlmostEqual(line_of_two_hours.unit_amount, 2.0, delta=0.05)
        self.assertAlmostEqual(line_of_one_hour.unit_amount, 1.0, delta=0.05)
        dev1_lines = self.env["account.analytic.line"].with_user(self.user_dev1)
        self.assertEqual(dev1_lines.search(dev1_lines._running_domain()).ids, [started["timesheet"]["id"]])

    def test_the_task_list_labels_a_project_without_a_key_by_its_name(self):
        self.project.key = False
        task = self._create_task("Keyless Task", self.stage_in_progress, self.user_dev1)

        result = self._call_route(self.user_dev1, "get_systray_timesheet", limit=0)

        self.assertEqual(
            [(task_data["task_id"], task_data["project_name"]) for task_data in result["tasks"]],
            [(task.id, self.project.name)],
        )
