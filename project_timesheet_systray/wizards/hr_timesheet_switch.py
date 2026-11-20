from odoo import models


class HrTimesheetSwitch(models.TransientModel):
    _inherit = "hr.timesheet.switch"

    def action_switch(self):
        """Start work and resume work, as the Timelog starts a timer: the user follows the task
        again where the time line needs it, and every running timer of theirs ends, also one the
        rules hide from `running_timer_id` (a task they unfollowed while it ran).
        """
        self.ensure_one()
        time_lines = self.env["account.analytic.line"]
        hidden_running_lines = time_lines._get_own_running_lines() - self.running_timer_id
        switch = super().action_switch

        def end_hidden_and_switch():
            hidden_running_lines.with_context(stop_dt=self.date_time).button_end_work()
            return switch()

        return time_lines._create_with_task_follow(self.task_id, end_hidden_and_switch)
