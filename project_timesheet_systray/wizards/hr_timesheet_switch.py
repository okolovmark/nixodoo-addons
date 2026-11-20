from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HrTimesheetSwitch(models.TransientModel):
    _inherit = "hr.timesheet.switch"

    @api.model
    def _default_running_timer_id(self, employee=None):
        """The user's running timer as the Timelog finds it, also one the rules on time lines
        hide from OCA's search (a task they unfollowed while it ran). More than one is refused,
        as OCA refuses it.
        """
        if employee:
            return super()._default_running_timer_id(employee)
        resuming_line_ids = self.env.context.get("resuming_lines", [])
        running_lines = (
            self.env["account.analytic.line"]
            ._get_own_running_lines()
            .filtered(lambda line: line.id not in resuming_line_ids)
        )
        if len(running_lines) > 1:
            raise UserError(
                _(
                    "%d running timers found. Cannot know which one to stop: stop them first, from the"
                    " Timelog item in the header or with Stop on each running line of your timesheets."
                )
                % len(running_lines)
            )
        return running_lines.sudo(False)

    # Sudo: the previous timer may be a line the rules on time lines hide from the user.
    running_timer_duration = fields.Float(compute_sudo=True)

    def action_switch(self):
        """Start work and resume work, as the Timelog starts a timer: the user follows the task
        again where the time line needs it, and every running timer of theirs ends, also one the
        rules on time lines hide, which OCA would end as the user.
        """
        self.ensure_one()
        time_lines = self.env["account.analytic.line"]
        own_running_lines = time_lines._get_own_running_lines()
        switch = super().action_switch

        def end_running_and_switch():
            own_running_lines.with_context(stop_dt=self.date_time).button_end_work()
            self.running_timer_id -= own_running_lines
            return switch()

        return time_lines._create_with_task_follow(self.task_id, end_running_and_switch)
