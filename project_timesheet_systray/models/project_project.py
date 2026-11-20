from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    # Sudo: the rules hide a running line of the user in a followers-only project they follow no
    # longer; `_timesheet_running_domain` keeps the count to the user's own lines.
    show_time_control = fields.Selection(compute_sudo=True)

    def _timesheet_running_domain(self):
        # Own lines only, whatever an override of `_running_domain` holds: the count runs in sudo.
        return super()._timesheet_running_domain() + [("user_id", "=", self.env.uid)]

    def button_end_work(self):
        """End the user's running lines in these projects, also one the rules on time lines hide
        from them, which OCA's search under those rules misses.
        """
        project_ids = self.ids
        running_lines = (
            self.env["account.analytic.line"]
            ._get_own_running_lines()
            .filtered(lambda line: line.project_id.id in project_ids)
        )
        if not running_lines:
            return super().button_end_work()
        return running_lines.button_end_work()
