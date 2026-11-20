from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import TimesheetSystrayCase
from odoo.addons.project_timesheet_systray.controllers import project_timesheet_systray as controller
from odoo.addons.project_timesheet_systray.controllers.project_timesheet_systray import TimesheetSystrayController

RUNNING_TIMER_FIELDS = (
    "id",
    "task_id",
    "task_name",
    "task_key",
    "is_task_readable",
    "project_id",
    "project_name",
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

    def _running_lines(self, user, **search_kwargs):
        return (
            self.env["account.analytic.line"]
            .with_user(user)
            .search(
                [
                    ("date_time", "!=", False),
                    ("user_id", "=", user.id),
                    ("project_id.allow_timesheets", "=", True),
                    ("unit_amount", "=", 0),
                    ("task_id", "!=", False),
                ],
                **search_kwargs,
            )
        )

    def _open_tasks(self, user):
        return self.env["project.task"].search([("user_ids", "in", [user.id]), ("stage_id.fold", "=", False)])

    def test_running_timer_detected(self):
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now() - timedelta(minutes=30),
        )
        running = self._running_lines(self.user_dev1, limit=1, order="date_time desc")
        self.assertTrue(running, "Running timer should be detected")
        self.assertEqual(running.task_id, self.task_in_progress)
        self.assertEqual(running.unit_amount, 0)

    def test_completed_timer_not_running(self):
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.5,
            date_time=datetime.now() - timedelta(hours=2),
        )
        running = self._running_lines(self.user_dev1, limit=1)
        self.assertFalse(running, "Completed timesheet line should not be detected as running")

    def test_running_timer_user_isolation(self):
        self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now(),
        )
        running = self._running_lines(self.user_dev2, limit=1)
        self.assertFalse(running, "Dev2 should not see dev1's running timer")

    def test_in_progress_tasks_exclude_folded(self):
        tasks = self._open_tasks(self.user_dev1)
        self.assertIn(self.task_in_progress, tasks)
        self.assertIn(self.task_backlog, tasks)
        self.assertNotIn(self.task_done, tasks, "Done (folded) task must not appear")

    def test_in_progress_tasks_user_specific(self):
        self.assertNotIn(self.task_dev2, self._open_tasks(self.user_dev1))
        self.assertNotIn(self.task_in_progress, self._open_tasks(self.user_dev2))

    def test_task_sorting_by_last_timelog(self):
        TimesheetLine = self.env["account.analytic.line"]
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

        in_progress_tasks = self._open_tasks(self.user_dev1)
        timelog_groups = TimesheetLine.read_group(
            [
                ("user_id", "=", self.user_dev1.id),
                ("task_id", "in", in_progress_tasks.ids),
                ("unit_amount", ">", 0),
            ],
            ["task_id", "date_time:max"],
            ["task_id"],
        )
        last_timelog_map = {group["task_id"][0]: group["date_time"] for group in timelog_groups if group["task_id"]}

        def sort_key(task):
            last_timelog = last_timelog_map.get(task.id)
            return (1 if last_timelog else 0, last_timelog or "", task.id)

        sorted_tasks = sorted(in_progress_tasks, key=sort_key, reverse=True)

        tasks_with_logs = [task for task in sorted_tasks if task.id in last_timelog_map]
        self.assertTrue(len(tasks_with_logs) >= 2)
        self.assertEqual(tasks_with_logs[0], self.task_in_progress, "Task with most recent timelog should be first")
        self.assertEqual(tasks_with_logs[1], self.task_backlog, "Task with older timelog should be second")

        tasks_without_logs = [task for task in sorted_tasks if task.id not in last_timelog_map]
        if tasks_with_logs and tasks_without_logs:
            self.assertLess(
                sorted_tasks.index(tasks_with_logs[-1]),
                sorted_tasks.index(tasks_without_logs[0]),
                "Tasks with timelogs should sort before tasks without",
            )

    def test_task_limit(self):
        for index in range(6):
            self._create_task("Extra Task %s" % index, self.stage_in_progress, self.user_dev1)

        tasks = self._open_tasks(self.user_dev1)
        self.assertGreater(len(tasks), 5, "Should have more than 5 tasks for this test")
        self.assertEqual(len(list(tasks)[:5]), 5)

    def test_start_timer_creates_running_line(self):
        task = self.task_in_progress
        new_line = (
            self.env["account.analytic.line"]
            .with_user(self.user_dev1)
            .create(
                {
                    "task_id": task.id,
                    "project_id": task.project_id.id,
                    "employee_id": self.employee_dev1.id,
                    "user_id": self.user_dev1.id,
                    "name": task.name,
                    "date_time": datetime.now(),
                    "unit_amount": 0,
                }
            )
        )
        self.assertTrue(new_line.exists())
        self.assertEqual(new_line.unit_amount, 0)
        self.assertEqual(new_line.task_id, task)
        self.assertTrue(new_line.date_time)

    def test_start_timer_stops_existing_running(self):
        old_line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now() - timedelta(hours=1),
        )
        self.assertEqual(old_line.unit_amount, 0, "Old timer should be running")

        dev1_lines = self.env["account.analytic.line"].with_user(self.user_dev1)
        running = dev1_lines.search(dev1_lines._running_domain(), limit=10)
        self.assertTrue(running, "Should find the running timer")
        running.button_end_work()

        self.assertGreater(old_line.unit_amount, 0, "Stopped timer should have computed duration")

    def test_start_timer_nonexistent_task_returns_error(self):
        self.assertFalse(self.env["project.task"].browse(999999).exists(), "Non-existent task should fail exists()")

    def test_start_timer_no_employee_detectable(self):
        user_no_employee = self.env["res.users"].create(
            self._project_user_vals("No Employee", "timesheet_systray_no_emp")
        )
        task_company = self.task_in_progress.company_id or self.task_in_progress.project_id.company_id
        employee = (
            self.env["hr.employee"]
            .sudo()
            .search([("user_id", "=", user_no_employee.id), ("company_id", "=", task_company.id)], limit=1)
        )
        self.assertFalse(employee, "User without employee should have no match")

    def test_stop_timer_computes_duration(self):
        line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now() - timedelta(hours=2),
        )
        self.assertEqual(line.unit_amount, 0)

        line.button_end_work()

        self.assertGreater(line.unit_amount, 0, "Duration should be computed after stop")
        self.assertAlmostEqual(line.unit_amount, 2.0, delta=0.1)

    def test_stop_timer_already_stopped_raises(self):
        line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=1.5,
            date_time=datetime.now() - timedelta(hours=2),
        )
        with self.assertRaises(UserError):
            line.button_end_work()

    def test_stop_timer_nonexistent_line(self):
        self.assertFalse(self.env["account.analytic.line"].browse(999999).exists())

    def test_format_running_timesheet_structure(self):
        line = self._create_timesheet(
            self.task_in_progress,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=0,
            date_time=datetime.now() - timedelta(minutes=45),
        )

        result = self._call_route(self.env.user, "_format_running_timesheet", line=line)

        self.assertEqual(set(result.keys()), set(RUNNING_TIMER_FIELDS))
        self.assertEqual(result["id"], line.id)
        self.assertEqual(result["task_id"], self.task_in_progress.id)
        self.assertEqual(result["task_name"], "Active Task")
        self.assertEqual(result["project_id"], self.project.id)
        self.assertEqual(result["unit_amount"], 0)
        self.assertGreater(result["elapsed_seconds"], 2600)
        self.assertLess(result["elapsed_seconds"], 2800)

    def test_format_running_timesheet_project_label_with_key(self):
        self._skip_without_keys()
        self.project.write({"key": "TST"})

        line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1)
        result = self._call_route(self.env.user, "_format_running_timesheet", line=line)
        self.assertEqual(result["project_name"], "TST - %s" % self.project.name)

    def test_format_running_timesheet_project_label_without_key(self):
        if "key" in self.project._fields:
            self.project.write({"key": ""})

        line = self._create_timesheet(self.task_in_progress, self.user_dev1, self.employee_dev1)
        result = self._call_route(self.env.user, "_format_running_timesheet", line=line)
        self.assertEqual(result["project_name"], self.project.name)


@tagged("post_install", "-at_install")
class TestTimesheetSystrayTaskData(TimesheetSystrayCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "key" in cls.project._fields:
            cls.project.key = "TST"
        # The key modules hand out no task key in test mode unless the context asks for one.
        cls.task1 = cls._create_task("Task Alpha", cls.stage_in_progress, cls.user_dev1, test_project_key=True)
        cls.task2 = cls._create_task("Task Beta", cls.stage_other, cls.user_dev1, test_project_key=True)

    def test_task_data_dict_structure(self):
        self._skip_without_keys()
        task = self.task1
        project = task.project_id
        project_label = (project.key + " - " + project.name) if project.key else project.name
        task_data = {
            "task_id": task.id,
            "task_name": task.name,
            "task_key": task.key or "",
            "project_id": project.id,
            "project_name": project_label,
            "stage_name": task.stage_id.name,
            "last_timelog": "",
        }
        self.assertEqual(task_data["task_name"], "Task Alpha")
        self.assertEqual(task_data["stage_name"], "In Progress")
        self.assertIn("TST", task_data["project_name"])

    def test_in_progress_task_list_project_label_with_key(self):
        self._skip_without_keys()
        dev1_request = SimpleNamespace(env=self.env(user=self.user_dev1))
        with patch.object(controller, "request", dev1_request):
            result = TimesheetSystrayController().get_systray_timesheet(limit=0)
        self.assertTrue(self.task1.key and self.task2.key, "the tasks of a keyed project get keys")
        self.assertEqual(
            {task_data["task_id"]: (task_data["project_name"], task_data["task_key"]) for task_data in result["tasks"]},
            {
                self.task1.id: ("TST - %s" % self.project.name, self.task1.key),
                self.task2.id: ("TST - %s" % self.project.name, self.task2.key),
            },
        )

    def test_task_data_with_timelog(self):
        TimesheetLine = self.env["account.analytic.line"]
        TimesheetLine.create(
            {
                "task_id": self.task1.id,
                "project_id": self.task1.project_id.id,
                "employee_id": self.employee_dev1.id,
                "user_id": self.user_dev1.id,
                "name": "Work session",
                "date_time": datetime.now() - timedelta(hours=3),
                "unit_amount": 2.0,
            }
        )

        timelog_groups = TimesheetLine.read_group(
            [
                ("user_id", "=", self.user_dev1.id),
                ("task_id", "in", [self.task1.id]),
                ("unit_amount", ">", 0),
            ],
            ["task_id", "date_time:max"],
            ["task_id"],
        )
        last_timelog_map = {group["task_id"][0]: group["date_time"] for group in timelog_groups if group["task_id"]}
        self.assertIn(self.task1.id, last_timelog_map)
        self.assertTrue(last_timelog_map[self.task1.id])

    def test_empty_tasks_message(self):
        user_empty = self.env["res.users"].create(self._project_user_vals("Empty User", "timesheet_systray_empty"))
        tasks = self.env["project.task"].search([("user_ids", "in", [user_empty.id]), ("stage_id.fold", "=", False)])
        self.assertFalse(tasks, "User with no tasks should have empty task list")


@tagged("post_install", "-at_install")
class TestTimesheetSystrayEndToEnd(TimesheetSystrayCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.task = cls._create_task("E2E Task", cls.stage_in_progress, cls.user_dev1)
        cls.task2 = cls._create_task("E2E Task 2", cls.stage_in_progress, cls.user_dev1)

    def _start(self, task, date_time):
        return self._create_timesheet(task, self.user_dev1, self.employee_dev1, unit_amount=0, date_time=date_time)

    def _stop_running_timers(self):
        dev1_lines = self.env["account.analytic.line"].with_user(self.user_dev1)
        running = dev1_lines.search(dev1_lines._running_domain(), limit=10)
        running.button_end_work()
        return running

    def test_full_timer_lifecycle(self):
        TimesheetLine = self.env["account.analytic.line"]
        running_domain = [
            ("date_time", "!=", False),
            ("user_id", "=", self.user_dev1.id),
            ("project_id.allow_timesheets", "=", True),
            ("unit_amount", "=", 0),
            ("task_id", "!=", False),
        ]
        self.assertFalse(TimesheetLine.search(running_domain, limit=1), "No timer should be running initially")

        new_line = self._start(self.task, datetime.now())
        self.assertEqual(new_line.unit_amount, 0)
        self.assertEqual(TimesheetLine.search(running_domain, limit=1, order="date_time desc"), new_line)

        new_line.button_end_work()
        self.assertGreater(new_line.unit_amount, 0, "Timer should have recorded duration")
        self.assertFalse(TimesheetLine.search(running_domain, limit=1), "No timer should be running after stop")

    def test_switch_timer_between_tasks(self):
        line1 = self._start(self.task, datetime.now() - timedelta(minutes=30))
        self.assertEqual(line1.unit_amount, 0)

        self._stop_running_timers()
        self.assertGreater(line1.unit_amount, 0)

        line2 = self._start(self.task2, datetime.now())
        self.assertEqual(line2.unit_amount, 0, "New timer should be running")
        self.assertGreater(line1.unit_amount, 0, "Old timer should be stopped")

    def test_multiple_running_timers_all_stopped(self):
        line1 = self._start(self.task, datetime.now() - timedelta(hours=2))
        line2 = self._start(self.task2, datetime.now() - timedelta(hours=1))

        running = self._stop_running_timers()
        self.assertEqual(len(running), 2, "Both timers should be found as running")
        self.assertGreater(line1.unit_amount, 0)
        self.assertGreater(line2.unit_amount, 0)


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

    def test_the_task_list_labels_a_project_without_a_key_by_its_name(self):
        """Also where no module declares keys at all."""
        if "key" in self.project._fields:
            self.project.write({"key": ""})
        task = self._create_task("Keyless Task", self.stage_in_progress, self.user_dev1)
        dev1_request = SimpleNamespace(env=self.env(user=self.user_dev1))
        with patch.object(controller, "request", dev1_request):
            result = TimesheetSystrayController().get_systray_timesheet(limit=0)
        self.assertEqual(
            [(task_data["task_id"], task_data["project_name"]) for task_data in result["tasks"]],
            [(task.id, self.project.name)],
        )
