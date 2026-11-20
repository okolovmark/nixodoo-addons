from datetime import datetime, timedelta

from odoo.tests.common import Form, tagged

from .common import TimesheetSystrayCase


@tagged("post_install", "-at_install")
class TestTimeControlOnTasksNotFollowed(TimesheetSystrayCase):
    def _start_work_from_the_task_form(self, task):
        dev1_task = task.with_user(self.user_dev1)
        action = dev1_task.button_start_work()
        switch_model = (
            self.env[action["res_model"]]
            .with_user(self.user_dev1)
            .with_context(action["context"], active_model="project.task", active_id=task.id)
        )
        with Form(switch_model) as switch_form:
            switch_form.name = "Work on the task"
        switch_form.save().action_switch()
        return self.env["account.analytic.line"].search(
            [("task_id", "=", task.id), ("user_id", "=", self.user_dev1.id), ("unit_amount", "=", 0)]
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
