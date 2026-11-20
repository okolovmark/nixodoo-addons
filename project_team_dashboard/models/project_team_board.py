from datetime import timedelta

from odoo import api, fields, models, tools
from odoo.osv import expression
from odoo.tools import date_utils

MODES = [("project", "Project"), ("manager", "Manager"), ("department", "Department")]
PERIODS = [("this_week", "This Week"), ("last_week", "Last Week"), ("this_month", "This Month")]
# What each mode of the screen reads with the user's own rights: without them it raises.
SCREEN_MODELS = {
    "project": ("project.project", "project.task", "account.analytic.line"),
    "manager": ("project.project", "project.task", "hr.employee.public"),
    "department": ("project.project", "project.task", "hr.department"),
}

# The id is stable from one reading to the next: subject, mode and period.
BOARD_QUERY = """
    WITH periods (period, period_number, label) AS (VALUES %(periods)s),
    subjects AS (
        SELECT 'project' AS mode, 0 AS mode_number, project.id AS subject_id,
               project.id AS project_id, NULL::integer AS manager_user_id,
               NULL::integer AS department_id, project.name->>'en_US' AS subject_name
          FROM project_project project
         WHERE project.active
         UNION ALL
        SELECT 'manager', 1, manager.id, NULL, manager.id, NULL, partner.name
          FROM res_users manager
          JOIN res_partner partner ON partner.id = manager.partner_id
         WHERE manager.active
           AND EXISTS (
                SELECT 1
                  FROM hr_employee employee
                  JOIN hr_employee subordinate ON subordinate.parent_id = employee.id
                 WHERE employee.user_id = manager.id
                   AND employee.active
                   AND subordinate.active
                   AND subordinate.user_id IS DISTINCT FROM manager.id
           )
         UNION ALL
        SELECT 'department', 2, department.id, NULL, NULL, department.id, department.complete_name
          FROM hr_department department
         WHERE department.active
    )
    SELECT subject.subject_id * %(rows_per_subject)s + subject.mode_number * %(period_count)s
           + periods.period_number + 1 AS id,
           subject.subject_name || ' · ' || periods.label AS name,
           subject.mode,
           subject.project_id,
           subject.manager_user_id,
           subject.department_id,
           periods.period
      FROM subjects subject
     CROSS JOIN periods
"""


class ProjectTeamBoard(models.Model):
    """One Team Dashboard per project, manager and department and per period, for a reader
    that can only search and read records: see ADR 0016."""

    _name = "project.team.board"
    _description = "Team Dashboard board"
    _auto = False
    _depends = {
        "hr.department": ["active", "complete_name"],
        "hr.employee": ["active", "parent_id", "user_id"],
        "project.project": ["active", "name"],
        "res.partner": ["name"],
        "res.users": ["active", "partner_id"],
    }

    name = fields.Char(readonly=True)
    mode = fields.Selection(MODES, readonly=True)
    project_id = fields.Many2one("project.project", readonly=True)
    manager_user_id = fields.Many2one("res.users", string="Manager", readonly=True)
    department_id = fields.Many2one("hr.department", readonly=True)
    period = fields.Selection(PERIODS, readonly=True)
    data = fields.Json(
        compute="_compute_data",
        help="What the Team Dashboard shows for the row's mode, subject and period, computed on reading.",
    )

    @api.depends("mode", "project_id", "manager_user_id", "department_id", "period")
    @api.depends_context("uid", "tz", "allowed_company_ids", "lang")
    def _compute_data(self):
        Project = self.env["project.project"]
        for board in self:
            date_start, date_end = (fields.Date.to_string(day) for day in board._get_period_dates())
            if board.mode == "project":
                board.data = Project.get_team_dashboard_data(board.project_id.id, date_start, date_end)
            elif board.mode == "manager":
                board.data = Project.get_team_dashboard_by_manager(board.manager_user_id.id, date_start, date_end)
            else:
                board.data = Project.get_team_dashboard_by_department(board.department_id.id, date_start, date_end)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("CREATE VIEW %s AS (%s)" % (self._table, self._get_query()))

    def check_field_access_rights(self, operation, field_names):
        readable_fnames = super().check_field_access_rights(operation, field_names)
        # a read naming no fields would compute a whole dashboard per row: data only when named
        if not field_names:
            return [fname for fname in readable_fnames if fname != "data"]
        return readable_fnames

    def _apply_ir_rules(self, query, mode="read"):
        super()._apply_ir_rules(query, mode)
        if self.env.su:
            return
        # not an ir.rule: an ir.rule traverses project_id as superuser, past the project's own rules
        expression.expression(self._get_openable_domain(), self.sudo(), self._table, query)

    def _filter_access_rules_python(self, operation):
        # check_access_rule evaluates the ir.rules in Python, which do not hold the openable rows
        return self._filter_access_rules(operation)

    def _get_query(self):
        period_values = ", ".join(
            self.env.cr.mogrify("(%s, %s, %s)", (period, number, label)).decode()
            for number, (period, label) in enumerate(PERIODS)
        )
        return BOARD_QUERY % {
            "periods": period_values,
            "period_count": len(PERIODS),
            "rows_per_subject": len(MODES) * len(PERIODS),
        }

    def _get_period_dates(self):
        """The first and last day of the period on the reader's today, as the screen's _getDates
        counts them: a week is the screen's default week (_get_dashboard_period) of its day."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.period == "this_month":
            return date_utils.start_of(today, "month"), date_utils.end_of(today, "month")
        week_day = today - timedelta(weeks=1) if self.period == "last_week" else today
        return self.env["project.project"]._get_dashboard_period(False, False, week_day)

    def _get_openable_domain(self):
        """The rows whose dashboard the screen opens for the user: a mode's rows are hidden when
        the screen raises for want of access rights, the others follow its rule for the mode."""
        subject_domain_getters = {
            "project": self._get_openable_project_domain,
            "manager": self._get_openable_manager_domain,
            "department": self._get_openable_department_domain,
        }
        return expression.OR(
            [
                expression.AND([[("mode", "=", mode)], get_subject_domain()])
                for mode, get_subject_domain in subject_domain_getters.items()
                if all(
                    self.env[model].check_access_rights("read", raise_exception=False) for model in SCREEN_MODELS[mode]
                )
            ]
        )

    def _get_openable_project_domain(self):
        return [("project_id", "in", self.env["project.project"]._search([]))]

    def _get_openable_manager_domain(self):
        return [("manager_user_id", "in", self.env["project.project"]._get_openable_manager_users().ids)]

    def _get_openable_department_domain(self):
        return [("department_id", "in", self.env["project.project"]._get_openable_departments().ids)]
