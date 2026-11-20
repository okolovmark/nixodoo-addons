from odoo import api, models
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
        """The user's running lines, the most recent first, searched in sudo: the rules on time
        lines hide them from a timesheet user who unfollowed their task in a followers-only
        project. The read access right is checked; callers that end these lines write them in
        sudo, the user's own lines only.
        """
        self.check_access_rights("read")
        # Not left to `_running_domain`, which an override may widen: the sudo search sees own lines only.
        own_running_domain = self._running_domain() + [("user_id", "=", self.env.uid)]
        return self.sudo().search(own_running_domain, order="date_time desc")

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
