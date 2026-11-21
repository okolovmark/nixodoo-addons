from odoo import api, fields, models
from odoo.exceptions import AccessError


class AccountAnalyticLine(models.Model):
    _inherit = "account.analytic.line"

    @api.model
    def _running_domain(self):
        return self._resolve_project_terms_in_sudo(super()._running_domain())

    @api.model
    def _resolve_project_terms_in_sudo(self, domain):
        """`domain`, on time lines or tasks, with its conditions on `project_id.*` resolved in
        sudo: the user's rules on projects hide a followers-only project from a user who follows
        only the task, whose time lines they still read and write.
        """
        projects_sudo = self.env["project.project"].sudo().with_context(active_test=False)
        resolved_domain = []
        for term in domain:
            if isinstance(term, (list, tuple)) and str(term[0]).startswith("project_id."):
                path, operator, value = term
                project_query = projects_sudo._search([(path.split(".", 1)[1], operator, value)])
                term = ("project_id", "in", project_query)
            resolved_domain.append(term)
        return resolved_domain

    @api.model
    def _get_own_running_lines(self):
        """The user's running timers, the most recent first, searched in sudo: the rules on time
        lines hide them from a timesheet user who unfollowed their task in a followers-only
        project. Unlike `_running_domain`, also a timer whose project had Timesheets turned off
        after it started: it runs on until ended. No stored field tells such a timer from a line
        of no hours left in that project, so one counts only if the user created it (a timer is
        always started by its user) and no later line they logged themselves, started by now,
        follows it: a start ends the running timers before its own line begins, so a line the
        user logged after was forgotten, not left running. A line someone else logged for them,
        or one the user put in the future (time off), says nothing of that.
        The read access right is checked; callers that end these lines write them in sudo, the
        user's own lines only.
        """
        self.check_access_rights("read")
        own_time_line_domain = [
            ("user_id", "=", self.env.uid),
            # A project marks a time line: OCA gives every analytic line a start time.
            ("project_id", "!=", False),
            ("date_time", "!=", False),
        ]
        own_lines_sudo = self.sudo()
        running_lines = own_lines_sudo.search(
            own_time_line_domain + [("unit_amount", "=", 0)], order="date_time desc, id desc"
        )
        lines_off = running_lines.filtered(lambda line: not line.project_id.allow_timesheets)
        if not lines_off:
            return running_lines
        own_past_lines_domain = [("create_uid", "=", self.env.uid), ("date_time", "<=", fields.Datetime.now())]
        latest_start = own_lines_sudo.search(
            own_time_line_domain + own_past_lines_domain, order="date_time desc", limit=1
        ).date_time
        timers_off = lines_off.filtered(
            lambda line: line.create_uid.id == self.env.uid and (not latest_start or line.date_time >= latest_start)
        )
        return running_lines.filtered(lambda line: line not in lines_off or line in timers_off)

    @api.model
    def _create_with_task_follow(self, task, create_lines):
        """Return `create_lines()`, which creates the user's time lines on `task`. Where the rules
        on time lines refuse them and the user does not follow the task, make them follow it and
        create once more: in a followers-only project, an assignee who unfollowed the task still
        reads it but logs no time on it.
        """
        partner = self.env.user.partner_id
        try:
            with self.env.cr.savepoint():
                return create_lines()
        except AccessError:
            if not task or partner in task.sudo().message_partner_ids:
                raise
        task.message_subscribe(partner.ids)
        return create_lines()
