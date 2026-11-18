import json
from datetime import datetime, timedelta

from odoo.tests.common import HttpCase, tagged

from .common import TimesheetSystrayCase

DEV1_PASSWORD = "timesheet_systray_dev1"
START_URL = "/project_timesheet_systray/start"
STOP_URL = "/project_timesheet_systray/stop"
SYSTRAY_URL = "/project_timesheet_systray/systray"


@tagged("post_install", "-at_install")
class TestTimesheetSystrayRoutesOverHttp(HttpCase, TimesheetSystrayCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_dev1.password = DEV1_PASSWORD
        cls.task = cls._create_task("Task Over HTTP", cls.stage_in_progress, cls.user_dev1)

    def _call(self, url, **params):
        payload = {"id": 0, "jsonrpc": "2.0", "method": "call", "params": params}
        response = self.url_open(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200, url)
        return response.json()

    def _result(self, url, **params):
        body = self._call(url, **params)
        self.assertNotIn("error", body, url)
        return body["result"]

    def test_the_routes_answer_a_signed_in_user_only(self):
        self.authenticate(None, None)
        for url in (START_URL, STOP_URL, SYSTRAY_URL):
            self.assertEqual(self._call(url).get("error", {}).get("code"), 100, "%s must ask for a session" % url)

    def test_a_user_starts_and_stops_a_timer_on_a_task(self):
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        task_list = self._result(SYSTRAY_URL, limit=0)
        self.assertFalse(task_list["running"])
        self.assertEqual([task_data["task_id"] for task_data in task_list["tasks"]], [self.task.id])

        started = self._result(START_URL, task_id=self.task.id)
        self.assertTrue(started["running"])
        line = self.env["account.analytic.line"].browse(started["timesheet"]["id"])
        self.assertEqual((line.task_id, line.employee_id, line.unit_amount), (self.task, self.employee_dev1, 0))
        self.assertEqual(self._result(SYSTRAY_URL)["timesheet"]["id"], line.id)

        line.date_time = datetime.now() - timedelta(minutes=30)
        self.env.flush_all()
        stopped = self._result(STOP_URL, timesheet_id=line.id)

        self.assertFalse(stopped["running"])
        line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)
