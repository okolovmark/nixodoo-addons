import json
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools import translate

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

    def _start_timer_on(self, task):
        started = self._result(START_URL, task_id=task.id)
        self.env.invalidate_all()
        line = self.env["account.analytic.line"].browse(started["timesheet"]["id"])
        self.assertEqual(
            (line.task_id, line.employee_id, line.unit_amount, bool(line.date_time)),
            (task, self.employee_dev1, 0, True),
        )
        return line

    def _start_half_an_hour_ago(self, line):
        line.date_time = datetime.now() - timedelta(minutes=30)
        self.env.flush_all()

    def _create_task_of_a_new_project(self, **project_vals):
        project = self.env["project.project"].create(
            {
                "name": "Project of Its Own",
                "allow_timesheets": True,
                "type_ids": [Command.set(self.stage_in_progress.ids)],
                **project_vals,
            }
        )
        return self.env["project.task"].create(
            {
                "name": "Task of a Project of Its Own",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [Command.set(self.user_dev1.ids)],
            }
        )

    def _turn_timesheets_off(self, project):
        """Off after the project got its analytic account: the start route can still log time on it."""
        project.allow_timesheets = False
        self.assertTrue(project.analytic_account_id)
        self.env.flush_all()

    def test_the_routes_answer_a_signed_in_user_only(self):
        self.authenticate(None, None)
        for url in (START_URL, STOP_URL, SYSTRAY_URL):
            self.assertEqual(self._call(url).get("error", {}).get("code"), 100, "%s must ask for a session" % url)

    def test_a_user_without_timesheet_rights_gets_no_timelog(self):
        """The header loads the item for every internal user: one without Timesheets rights
        gets it unavailable, not an access error on each page load, even where Analytic
        Accounting lets them read timesheet lines."""
        unavailable = {
            "available": False,
            "running": False,
            "timesheet": False,
            "tasks": [],
            "total_tasks": 0,
            "message": "",
        }
        for login, groups in (
            ("timesheet_systray_internal", "base.group_user"),
            ("timesheet_systray_analytic", "base.group_user,analytic.group_analytic_accounting"),
        ):
            with self.subTest(login=login):
                user = new_test_user(self.env, login, groups=groups, password=login)
                self.task.user_ids = [Command.link(user.id)]
                self.authenticate(login, login)
                self.assertEqual(self._result(SYSTRAY_URL), unavailable)

    def test_a_user_starts_and_stops_a_timer_on_a_task(self):
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        self.assertNotIn("available", self._result(SYSTRAY_URL))

        task_list = self._result(SYSTRAY_URL, limit=0)
        self.assertFalse(task_list["running"])
        self.assertEqual([task_data["task_id"] for task_data in task_list["tasks"]], [self.task.id])

        started = self._result(START_URL, task_id=self.task.id)
        self.assertEqual((started["running"], started["timesheet"]["is_task_readable"]), (True, True))
        line = self.env["account.analytic.line"].browse(started["timesheet"]["id"])
        self.assertEqual((line.task_id, line.employee_id, line.unit_amount), (self.task, self.employee_dev1, 0))
        self.assertEqual(self._result(SYSTRAY_URL)["timesheet"]["id"], line.id)

        line.date_time = datetime.now() - timedelta(minutes=30)
        self.env.flush_all()
        stopped = self._result(STOP_URL, timesheet_id=line.id)

        self.assertFalse(stopped["running"])
        line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_a_task_of_a_project_the_user_cannot_read_is_listed_with_its_project(self):
        private_project = self.env["project.project"].create(
            {
                "name": "Followers Only Project",
                "key": "TSFOLLOW",
                "privacy_visibility": "followers",
                "allow_timesheets": True,
                "type_ids": [Command.set(self.stage_in_progress.ids)],
            }
        )
        task = self.env["project.task"].create(
            {
                "name": "Task Followed in a Project Not Followed",
                "project_id": private_project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [Command.set(self.user_dev1.ids)],
            }
        )
        self.assertFalse(
            self.env["project.project"].with_user(self.user_dev1).search([("id", "=", private_project.id)])
        )
        self.assertTrue(self.env["project.task"].with_user(self.user_dev1).search([("id", "=", task.id)]))
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        task_list = self._result(SYSTRAY_URL, limit=0)

        self.assertIn(
            (task.id, private_project.id, "TSFOLLOW - Followers Only Project"),
            [
                (task_data["task_id"], task_data["project_id"], task_data["project_name"])
                for task_data in task_list["tasks"]
            ],
        )

    def test_a_task_of_a_project_with_timesheets_off_is_not_listed(self):
        project = self.env["project.project"].create(
            {
                "name": "Project Without Timesheets",
                "allow_timesheets": False,
                "type_ids": [Command.set(self.stage_in_progress.ids)],
            }
        )
        task = self.env["project.task"].create(
            {
                "name": "Task Without Timesheets",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [Command.set(self.user_dev1.ids)],
            }
        )
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        task_list = self._result(SYSTRAY_URL, limit=0)

        self.assertEqual(
            ([task_data["task_id"] for task_data in task_list["tasks"]], task_list["total_tasks"]),
            ([self.task.id], 1),
            "%s must not be listed" % task.name,
        )

    def test_an_assignee_who_unfollowed_a_task_of_a_followers_only_project_follows_it_again_on_start(self):
        task = self._create_unfollowed_task("followers")
        self.assertFalse(
            self.env["project.project"].with_user(self.user_dev1).search([("id", "=", task.project_id.id)])
        )
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        self.assertIn(task.id, [task_data["task_id"] for task_data in self._result(SYSTRAY_URL, limit=0)["tasks"]])

        self._start_timer_on(task)

        self.assertIn(self.user_dev1.partner_id, task.message_partner_ids)

    def test_an_assignee_who_does_not_follow_a_task_visible_to_all_internal_users_is_not_made_a_follower(self):
        task = self._create_unfollowed_task("employees")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        self._start_timer_on(task)

        self.assertNotIn(self.user_dev1.partner_id, task.message_partner_ids)

    def test_an_assignee_following_a_followers_only_project_is_not_made_a_follower_of_its_task(self):
        task = self._create_unfollowed_task("followers")
        task.project_id.message_subscribe(self.user_dev1.partner_id.ids)
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        self._start_timer_on(task)

        self.assertNotIn(self.user_dev1.partner_id, task.message_partner_ids)

    def test_a_timesheet_administrator_who_unfollowed_a_task_of_a_followers_only_project_is_not_made_a_follower(self):
        self.user_dev1.groups_id = [Command.link(self.env.ref("hr_timesheet.group_timesheet_manager").id)]
        task = self._create_unfollowed_task("followers")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        self._start_timer_on(task)

        self.assertNotIn(self.user_dev1.partner_id, task.message_partner_ids)

    def test_a_timesheet_administrator_taken_off_the_task_of_a_running_timer_sees_and_stops_it(self):
        self.user_dev1.groups_id = [Command.link(self.env.ref("hr_timesheet.group_timesheet_manager").id)]
        task = self._create_unfollowed_task("followers")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        line = self._start_timer_on(task)
        task.user_ids = [Command.unlink(self.user_dev1.id)]
        self.env.flush_all()
        self.assertFalse(self.env["project.task"].with_user(self.user_dev1).search([("id", "=", task.id)]))

        systray = self._result(SYSTRAY_URL)

        timesheet = systray["timesheet"] or {}
        self.assertEqual(
            (
                systray["running"],
                timesheet.get("id"),
                timesheet.get("task_name"),
                timesheet.get("project_id"),
                timesheet.get("is_task_readable"),
            ),
            (True, line.id, task.name, task.project_id.id, False),
            "the running card names the task, with no link to a task the user no longer reads",
        )
        self._start_half_an_hour_ago(line)
        self.assertFalse(self._result(STOP_URL, timesheet_id=line.id)["running"])
        line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_a_timer_on_a_task_followed_again_shows_in_the_systray_until_stopped(self):
        task = self._create_unfollowed_task("followers")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        line = self._start_timer_on(task)

        systray = self._result(SYSTRAY_URL)

        self.assertEqual((systray["running"], (systray["timesheet"] or {}).get("id")), (True, line.id))
        self._start_half_an_hour_ago(line)
        self.assertFalse(self._result(STOP_URL, timesheet_id=line.id)["running"])
        line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_a_second_start_ends_the_timer_on_a_task_followed_again(self):
        task = self._create_unfollowed_task("followers")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        first_line = self._start_timer_on(task)
        self._start_half_an_hour_ago(first_line)

        self._start_timer_on(self.task)

        first_line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(first_line.unit_amount, 0.5, delta=0.05)

    def test_a_start_failing_after_the_follow_takes_the_follow_and_the_stopped_timer_back(self):
        running_line = self._create_timesheet(
            self.task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(minutes=30)
        )
        task = self._create_unfollowed_task("followers")
        TaskModel = type(self.env["project.task"])
        follow = TaskModel.message_subscribe

        def follow_then_archive_the_analytic_account(task_to_follow, *args, **kwargs):
            follow(task_to_follow, *args, **kwargs)
            task.project_id.analytic_account_id.active = False

        with patch.object(
            TaskModel, "message_subscribe", autospec=True, side_effect=follow_then_archive_the_analytic_account
        ) as follow_mock:
            # Not assertRaises: its savepoint would roll the follow back itself and pass on any code.
            try:
                self._call_route(self.user_dev1, "start_timer", task_id=task.id)
            except UserError:
                pass
            else:
                self.fail("a time line on an archived analytic account must be refused")

        follow_mock.assert_called_once_with(task, self.user_dev1.partner_id.ids)
        self.env.invalidate_all()
        self.assertNotIn(self.user_dev1.partner_id, task.message_partner_ids)
        self.assertEqual(running_line.unit_amount, 0)

    def _start_then_unfollow(self, task):
        """A timer of Dev One on `task`, whose line the rules on time lines then hide from them."""
        line = self._start_timer_on(task)
        task.message_unsubscribe(self.user_dev1.partner_id.ids)
        self.env.flush_all()
        self.assertFalse(self.env["account.analytic.line"].with_user(self.user_dev1).search([("id", "=", line.id)]))
        return line

    def test_a_timesheet_user_who_unfollows_the_task_of_a_running_timer_sees_and_stops_it(self):
        task = self._create_unfollowed_task("followers")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        line = self._start_then_unfollow(task)

        systray = self._result(SYSTRAY_URL)

        timesheet = systray["timesheet"] or {}
        self.assertEqual(
            (systray["running"], timesheet.get("id"), timesheet.get("is_task_readable")), (True, line.id, True)
        )
        self._start_half_an_hour_ago(line)
        self.assertFalse(self._result(STOP_URL, timesheet_id=line.id)["running"])
        line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_a_start_ends_a_running_timer_whose_line_the_user_no_longer_reads(self):
        task = self._create_unfollowed_task("followers")
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        first_line = self._start_then_unfollow(task)
        self._start_half_an_hour_ago(first_line)

        self._start_timer_on(self.task)

        first_line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(first_line.unit_amount, 0.5, delta=0.05)

    def test_the_systray_neither_shows_nor_stops_a_running_timer_of_another_user(self):
        other_line = self._create_timesheet(self.task, self.user_dev2, self.employee_dev2)
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        self.assertFalse(self._result(SYSTRAY_URL)["running"])
        self.assertIn("error", self._result(STOP_URL, timesheet_id=other_line.id))
        other_line.invalidate_recordset(["unit_amount"])
        self.assertEqual(other_line.unit_amount, 0)

    def test_a_timesheet_approver_or_administrator_neither_sees_nor_stops_a_running_timer_of_another_user(self):
        other_line = self._create_timesheet(self.task, self.user_dev2, self.employee_dev2)
        for group in ("hr_timesheet.group_hr_timesheet_approver", "hr_timesheet.group_timesheet_manager"):
            with self.subTest(group=group):
                login = "timesheet_systray_%s" % group.split(".")[1]
                user = new_test_user(
                    self.env, login, groups="base.group_user,project.group_project_user,%s" % group, password=login
                )
                self.assertTrue(
                    self.env["account.analytic.line"].with_user(user).search([("id", "=", other_line.id)]),
                    "the rules on time lines let %s read the line" % login,
                )
                self.authenticate(login, login)

                self.assertFalse(self._result(SYSTRAY_URL)["running"])
                self.assertEqual(self._result(STOP_URL, timesheet_id=other_line.id), {"error": "Timer is not running"})
                other_line.invalidate_recordset(["unit_amount"])
                self.assertEqual(other_line.unit_amount, 0)

    def test_a_timer_whose_project_turned_timesheets_off_shows_in_the_systray_until_stopped(self):
        task = self._create_task_of_a_new_project()
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        line = self._start_timer_on(task)
        self._turn_timesheets_off(task.project_id)
        self._start_half_an_hour_ago(line)

        systray = self._result(SYSTRAY_URL)

        self.assertEqual((systray["running"], (systray["timesheet"] or {}).get("id")), (True, line.id))
        self.assertFalse(self._result(STOP_URL, timesheet_id=line.id)["running"])
        line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_a_start_ends_a_timer_whose_project_turned_timesheets_off(self):
        task = self._create_task_of_a_new_project()
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        first_line = self._start_timer_on(task)
        self._turn_timesheets_off(task.project_id)
        self._start_half_an_hour_ago(first_line)

        self._start_timer_on(self.task)

        first_line.invalidate_recordset(["unit_amount"])
        self.assertAlmostEqual(first_line.unit_amount, 0.5, delta=0.05)

    def test_a_zero_hour_line_left_in_a_project_with_timesheets_off_is_neither_shown_nor_ended(self):
        task = self._create_task_of_a_new_project()
        forgotten_line = self._create_timesheet(
            task,
            self.user_dev1,
            self.employee_dev1,
            date_time=datetime.now() - timedelta(days=3),
            logged_by=self.user_dev1,
        )
        self._create_timesheet(
            self.task,
            self.user_dev1,
            self.employee_dev1,
            unit_amount=2,
            date_time=datetime.now() - timedelta(days=1),
            logged_by=self.user_dev1,
        )
        self._turn_timesheets_off(task.project_id)
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        self.assertFalse(self._result(SYSTRAY_URL)["running"])
        self._start_timer_on(self.task)

        forgotten_line.invalidate_recordset(["unit_amount"])
        self.assertEqual(forgotten_line.unit_amount, 0)

    def test_a_start_on_a_task_of_a_project_with_timesheets_off_is_refused(self):
        task = self._create_task_of_a_new_project()
        self._turn_timesheets_off(task.project_id)
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)

        answer = self._result(START_URL, task_id=task.id)

        self.assertEqual(answer, {"error": "Timesheets are turned off in the project of this task"})
        self.assertFalse(self.env["account.analytic.line"].search([("task_id", "=", task.id)]))

    def _create_task_of_a_second_company(self):
        """A task of Dev One, who has an employee in its company, which they read only with that
        company selected. Returns the company and the task."""
        other_company = self.env["res.company"].create({"name": "Timelog Second Company"})
        self.user_dev1.company_ids = [Command.link(other_company.id)]
        self.env["hr.employee"].create(
            {"name": "Dev One", "user_id": self.user_dev1.id, "company_id": other_company.id}
        )
        task = self._create_task_of_a_new_project(company_id=other_company.id)
        tasks_dev1 = self.env["project.task"].with_user(self.user_dev1)
        self.assertFalse(
            tasks_dev1.with_context(allowed_company_ids=self.env.company.ids).search([("id", "=", task.id)])
        )
        return other_company, task

    def test_the_running_timer_links_its_task_where_the_web_client_opens_it_with_the_task_company(self):
        """The form opens with the companies selected in the web client, which it keeps in the
        `cids` cookie, defaulting to the user's current company."""
        company = self.env.company
        other_company, task = self._create_task_of_a_second_company()
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        self._result(START_URL, task_id=task.id)

        for cids, is_task_readable in (
            (None, False),
            (str(company.id), False),
            ("%s,%s" % (company.id, other_company.id), True),
            (str(other_company.id), True),
            ("%s,0" % other_company.id, False),
        ):
            with self.subTest(cids=cids):
                if cids:
                    self.opener.cookies["cids"] = cids
                timesheet = self._result(SYSTRAY_URL)["timesheet"] or {}
                self.assertEqual(
                    (timesheet.get("task_id"), timesheet.get("is_task_readable")), (task.id, is_task_readable)
                )

    def test_the_running_timer_links_its_task_where_the_tab_opens_it_whatever_the_cookie_holds(self):
        """Each tab keeps its companies in its URL; the cookie holds those of the tab that last
        loaded or switched them."""
        company = self.env.company
        other_company, task = self._create_task_of_a_second_company()
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        self._result(START_URL, task_id=task.id)

        for tab_company_ids, cids, is_task_readable in (
            ([other_company.id], str(company.id), True),
            ([company.id, other_company.id], str(company.id), True),
            ([company.id], str(other_company.id), False),
            ([other_company.id, 0], str(company.id), False),
            ("not a company", str(other_company.id), True),
        ):
            with self.subTest(tab_company_ids=tab_company_ids, cids=cids):
                self.opener.cookies["cids"] = cids
                timesheet = self._result(SYSTRAY_URL, company_ids=tab_company_ids)["timesheet"] or {}
                self.assertEqual(
                    (timesheet.get("task_id"), timesheet.get("is_task_readable")), (task.id, is_task_readable)
                )

    def test_the_start_and_stop_answers_link_the_running_task_where_the_tab_opens_it(self):
        other_company, task = self._create_task_of_a_second_company()
        self.authenticate(self.user_dev1.login, DEV1_PASSWORD)
        self.opener.cookies["cids"] = str(self.env.company.id)

        started = self._result(START_URL, task_id=task.id, company_ids=[other_company.id])
        older_line = self._create_timesheet(
            self.task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(hours=1)
        )
        self.env.flush_all()
        stopped = self._result(STOP_URL, timesheet_id=older_line.id, company_ids=[other_company.id])

        self.assertEqual(
            [
                (answer["timesheet"]["task_id"], answer["timesheet"]["is_task_readable"])
                for answer in (started, stopped)
            ],
            [(task.id, True), (task.id, True)],
        )

    def test_the_route_messages_follow_the_user_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        new_test_user(
            self.env,
            "timesheet_systray_fr",
            groups="base.group_user,project.group_project_user,hr_timesheet.group_hr_timesheet_user",
            password="timesheet_systray_fr",
            lang="fr_FR",
        )
        task_without_timesheets = self._create_task_of_a_new_project()
        self._turn_timesheets_off(task_without_timesheets.project_id)
        french_messages = {
            "Task not found": "Tâche introuvable",
            "Timesheets are turned off in the project of this task": "Les feuilles de temps sont désactivées",
            "No employee linked to your user": "Aucun employé lié à votre utilisateur",
            "Timer is not running": "Le minuteur ne tourne pas",
            "No tasks in progress": "Aucune tâche en cours",
        }
        self.authenticate("timesheet_systray_fr", "timesheet_systray_fr")

        with patch.object(
            translate.code_translations,
            "get_python_translations",
            lambda module, lang: french_messages if lang == "fr_FR" else {},
        ):
            answers = (
                self._result(START_URL, task_id=self.task.id + 1000000)["error"],
                self._result(START_URL, task_id=task_without_timesheets.id)["error"],
                self._result(START_URL, task_id=self.task.id)["error"],
                self._result(STOP_URL, timesheet_id=0)["error"],
                self._result(SYSTRAY_URL)["message"],
            )

        self.assertEqual(answers, tuple(french_messages.values()))
