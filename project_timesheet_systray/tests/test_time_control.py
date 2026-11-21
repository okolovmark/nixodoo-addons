from ast import literal_eval
from datetime import datetime, timedelta

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import Form, tagged

from .common import TimesheetSystrayCase


@tagged("post_install", "-at_install")
class TestTimeControlOnTasksNotFollowed(TimesheetSystrayCase):
    def _start_work_form(self, task):
        action = task.with_user(self.user_dev1).button_start_work()
        switch_model = (
            self.env[action["res_model"]]
            .with_user(self.user_dev1)
            .with_context(action["context"], active_model="project.task", active_id=task.id)
        )
        return Form(switch_model)

    def _start_work_from_the_task_form(self, task):
        with self._start_work_form(task) as switch_form:
            switch_form.name = "Work on the task"
        switch_form.save().action_switch()
        return self.env["account.analytic.line"].search(
            [("task_id", "=", task.id), ("user_id", "=", self.user_dev1.id), ("unit_amount", "=", 0)]
        )

    def _create_hidden_running_line(self, task):
        """A timer of Dev One started half an hour ago on `task`, which they then unfollowed: in a
        followers-only project the rules on time lines hide the line from them."""
        task.message_subscribe(self.user_dev1.partner_id.ids)
        line = self._create_timesheet(
            task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(minutes=30)
        )
        task.message_unsubscribe(self.user_dev1.partner_id.ids)
        self.env.invalidate_all()
        self.assertFalse(self.env["account.analytic.line"].with_user(self.user_dev1).search([("id", "=", line.id)]))
        return line

    def _grant_read_access(self, project):
        """Dev One reads `project` without following it, as a module granting it to members does."""
        readers = self.env["res.groups"].create({"name": "Readers", "users": [Command.link(self.user_dev1.id)]})
        self.env["ir.rule"].create(
            {
                "name": "Readers read the project",
                "model_id": self.env["ir.model"]._get_id("project.project"),
                "groups": [Command.link(readers.id)],
                "domain_force": "[('id', '=', %d)]" % project.id,
                "perm_write": False,
                "perm_create": False,
                "perm_unlink": False,
            }
        )
        self.assertTrue(self.env["project.project"].with_user(self.user_dev1).search([("id", "=", project.id)]))

    def test_the_dialog_does_not_link_the_previous_timer(self):
        arch = self.env["hr.timesheet.switch"].with_user(self.user_dev1).get_view(view_type="form")["arch"]
        (field,) = etree.fromstring(arch).xpath("//field[@name='running_timer_id']")

        self.assertTrue(
            literal_eval(field.get("options", "{}")).get("no_open"), "a timer the rules hide cannot be opened"
        )

    def test_start_work_on_the_task_form_makes_an_assignee_who_unfollowed_follow_again(self):
        task = self._create_unfollowed_task("followers")

        line = self._start_work_from_the_task_form(task)

        self.assertEqual((line.task_id, line.employee_id, bool(line.date_time)), (task, self.employee_dev1, True))
        self.assertIn(self.user_dev1.partner_id, task.message_partner_ids)

    def test_start_work_on_the_task_form_makes_no_follower_where_the_rules_allow_the_line(self):
        task = self._create_unfollowed_task("employees")

        line = self._start_work_from_the_task_form(task)

        self.assertEqual(line.task_id, task)
        self.assertNotIn(self.user_dev1.partner_id, task.message_partner_ids)

    def test_start_work_ends_a_running_timer_the_rules_hid_after_its_task_was_unfollowed(self):
        task = self._create_unfollowed_task("followers")
        task.message_subscribe(self.user_dev1.partner_id.ids)
        hidden_line = self._create_timesheet(
            task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(minutes=30)
        )
        task.message_unsubscribe(self.user_dev1.partner_id.ids)
        self.env.invalidate_all()
        lines_dev1 = self.env["account.analytic.line"].with_user(self.user_dev1)
        self.assertFalse(lines_dev1.search([("id", "=", hidden_line.id)]))

        new_line = self._start_work_from_the_task_form(task) - hidden_line

        self.env.invalidate_all()
        self.assertEqual(len(new_line), 1)
        self.assertGreater(hidden_line.unit_amount, 0)
        self.assertEqual(
            self.env["account.analytic.line"].search(
                [("user_id", "=", self.user_dev1.id), ("unit_amount", "=", 0), ("date_time", "!=", False)]
            ),
            new_line,
        )

    def test_the_task_form_shows_and_stops_a_running_timer_on_a_project_the_user_does_not_follow(self):
        task = self._create_unfollowed_task("followers")
        task.message_subscribe(self.user_dev1.partner_id.ids)
        line = self._create_timesheet(
            task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(minutes=30)
        )
        dev1_task = task.with_user(self.user_dev1)
        self.assertFalse(
            self.env["project.project"].with_user(self.user_dev1).search([("id", "=", task.project_id.id)])
        )

        self.assertEqual(dev1_task.show_time_control, "stop")
        dev1_task.button_end_work()

        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_the_task_form_shows_and_stops_a_running_timer_the_rules_hid_after_its_task_was_unfollowed(self):
        task = self._create_unfollowed_task("followers")
        task.message_subscribe(self.user_dev1.partner_id.ids)
        line = self._create_timesheet(
            task, self.user_dev1, self.employee_dev1, date_time=datetime.now() - timedelta(minutes=30)
        )
        task.message_unsubscribe(self.user_dev1.partner_id.ids)
        self.assertFalse(self.env["account.analytic.line"].with_user(self.user_dev1).search([("id", "=", line.id)]))
        dev1_task = task.with_user(self.user_dev1)

        self.assertEqual(dev1_task.show_time_control, "stop")
        dev1_task.button_end_work()

        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_the_task_form_neither_counts_nor_stops_a_running_timer_of_another_user(self):
        task = self._create_task("Task of Two", self.stage_in_progress, self.user_dev1 + self.user_dev2)
        self._create_timesheet(task, self.user_dev2, self.employee_dev2)
        self.assertEqual(task.with_user(self.user_dev2).show_time_control, "stop")
        # One cached value serves every user: each user here gets their own computation.
        task.invalidate_recordset(["show_time_control"])
        dev1_task = task.with_user(self.user_dev1)

        self.assertEqual(dev1_task.show_time_control, "start")
        with self.assertRaises(UserError):
            dev1_task.button_end_work()

    def test_start_work_shows_a_running_timer_the_rules_hid_after_its_task_was_unfollowed_as_the_previous_timer(self):
        task = self._create_unfollowed_task("followers")
        hidden_line = self._create_hidden_running_line(task)

        switch_form = self._start_work_form(task)

        self.assertEqual(switch_form.running_timer_id, hidden_line)
        self.assertAlmostEqual(switch_form.running_timer_duration, 0.5, delta=0.05)
        switch_form.name = "Work on the task"
        switch = switch_form.save()
        self.env.invalidate_all()
        self.assertAlmostEqual(
            switch.running_timer_duration, 0.5, delta=0.05, msg="read afresh, as the dialog reloads it after saving"
        )

    def test_start_work_with_a_running_timer_besides_one_the_rules_hide_asks_to_stop_them_first(self):
        self._create_hidden_running_line(self._create_unfollowed_task("followers"))
        task = self._create_task("Task Followed", self.stage_in_progress, self.user_dev1)
        self._create_timesheet(task, self.user_dev1, self.employee_dev1)

        with self.assertRaisesRegex(UserError, "2 running timers found"):
            self._start_work_form(task)

    def test_the_project_form_shows_and_stops_a_running_timer_the_rules_hid_after_its_task_was_unfollowed(self):
        task = self._create_unfollowed_task("followers")
        line = self._create_hidden_running_line(task)
        self._grant_read_access(task.project_id)
        dev1_project = task.project_id.with_user(self.user_dev1)

        self.assertEqual(dev1_project.show_time_control, "stop")
        dev1_project.button_end_work()

        self.assertAlmostEqual(line.unit_amount, 0.5, delta=0.05)

    def test_the_project_form_neither_counts_nor_stops_a_running_timer_of_another_user(self):
        task = self._create_task("Task of Two", self.stage_in_progress, self.user_dev1 + self.user_dev2)
        self._create_timesheet(task, self.user_dev2, self.employee_dev2)
        self.assertEqual(self.project.with_user(self.user_dev2).show_time_control, "stop")
        # One cached value serves every user: each user here gets their own computation.
        self.project.invalidate_recordset(["show_time_control"])
        dev1_project = self.project.with_user(self.user_dev1)

        self.assertEqual(dev1_project.show_time_control, "start")
        with self.assertRaises(UserError):
            dev1_project.button_end_work()
