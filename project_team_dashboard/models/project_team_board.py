from datetime import timedelta

from odoo import api, fields, models, tools
from odoo.fields import Domain
from odoo.tools import SQL, date_utils

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
           AND project.is_template IS NOT TRUE
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
        SELECT 'department', 2, department.id, NULL, NULL, department.id, department.name->>'en_US'
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
        "hr.department": ["active", "name"],
        "hr.employee": ["active", "parent_id", "user_id"],
        "project.project": ["active", "is_template", "name"],
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
        # the screen's active_test: Odoo 20 keys a cache on it for relational fields only
        Project = self.env["project.project"].with_context(active_test=True)
        Project.check_access("read")
        modes = set(self.mapped("mode"))
        # the screen's methods would compute these once a row
        openable_users = Project._get_openable_manager_users() if "manager" in modes else None
        openable_departments = Project._get_openable_departments() if "department" in modes else None
        for board in self:
            date_start, date_end = (fields.Date.to_string(day) for day in board._get_period_dates())
            if board.mode == "project":
                board.data = Project.get_team_dashboard_data(board.project_id.id, date_start, date_end)
            elif board.mode == "manager":
                board.data = Project._get_manager_dashboard(board.manager_user_id, openable_users, date_start, date_end)
            else:
                board.data = Project._get_department_dashboard(
                    board.department_id.sudo(), openable_departments, date_start, date_end
                )

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(SQL("CREATE VIEW %s AS (%s)", SQL.identifier(self._table), self._get_query()))

    # readonly as core's: an override without it takes RPC reads off the read-only cursor
    @api.readonly
    def read(self, fields=None, load="_classic_read"):
        """A read that names no fields leaves data out; one that names it gets it."""
        return super().read(fields or self._get_unnamed_read_fnames(), load=load)

    @api.model
    @api.readonly
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None, **read_kwargs):
        """A search_read that names no fields leaves data out, as read() does."""
        return super().search_read(
            domain, fields or self._get_unnamed_read_fnames(), offset=offset, limit=limit, order=order, **read_kwargs
        )

    def _access_domain(self, operation):
        domain = super()._access_domain(operation)
        if domain.is_false():
            return domain
        # not an ir.access domain: Odoo caches those per user and companies, the teams follow the hierarchy;
        # the compute's active_test, which the access cache does not key on either
        return domain & self.with_context(active_test=True)._get_openable_domain()

    @api.model
    def _get_unnamed_read_fnames(self):
        """The fields of a read naming none, as core expands it, but data: one dashboard per row
        would be computed. search_read expands its own list and does not call read."""
        return [fname for fname in self.fields_get(attributes=()) if fname != "data"]

    def _get_query(self):
        period_values = SQL(", ").join(
            SQL("(%s, %s, %s)", period, number, label) for number, (period, label) in enumerate(PERIODS)
        )
        return SQL(
            BOARD_QUERY,
            periods=period_values,
            period_count=len(PERIODS),
            rows_per_subject=len(MODES) * len(PERIODS),
        )

    def _get_period_dates(self):
        """The first and last day of the period on the reader's today, as the screen's getPeriodDates
        counts them: a week is the screen's default week (_get_dashboard_period) of its day."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        if self.period == "this_month":
            return date_utils.start_of(today, "month"), date_utils.end_of(today, "month")
        week_day = today - timedelta(weeks=1) if self.period == "last_week" else today
        return self.env["project.project"]._get_dashboard_period(False, False, week_day)

    @api.model
    def _get_openable_domain(self):
        """The rows whose dashboard the screen opens for the user: a mode's rows are hidden when
        the screen raises for want of access rights, the others follow its rule for the mode."""
        row_domain_getters = {
            "project": self._get_openable_project_row_domain,
            "manager": self._get_openable_manager_row_domain,
            "department": self._get_openable_department_row_domain,
        }
        return Domain.OR(
            Domain("mode", "=", mode) & self._get_deferred_row_domain(mode, get_row_domain)
            for mode, get_row_domain in row_domain_getters.items()
            if all(self.env[model_name].has_access("read") for model_name in SCREEN_MODELS[mode])
        )

    @api.model
    def _get_deferred_row_domain(self, mode, get_row_domain):
        """get_row_domain() built when needed only: not for a search whose domain leaves the mode
        out, nor for checking the rows of other modes (an AND builds its predicates lazily)."""

        def optimize(custom, board_model):
            search_domain = board_model.env.context.get("search_domain")
            if (
                isinstance(search_domain, Domain)
                and (Domain("mode", "=", mode) & search_domain).optimize(board_model).is_false()
            ):
                return Domain.FALSE
            return get_row_domain()

        return Domain.custom(
            to_sql=lambda table: get_row_domain().optimize_full(table._model)._to_sql(table),
            optimize=optimize,
        )

    @api.model
    def _get_openable_project_row_domain(self):
        return Domain("project_id", "access", "read")

    # subqueries built with the user's rights: no list of every team inlined for those who see them all
    @api.model
    def _get_openable_manager_row_domain(self):
        employees = self.env["hr.employee.public"]._search(
            self.env["project.project"]._get_openable_manager_employee_domain()
        )
        other_managers = Domain("manager_user_id", "any!", employees.select(employees.table["user_id"]))
        return other_managers | Domain("manager_user_id", "=", self.env.uid)

    @api.model
    def _get_openable_department_row_domain(self):
        departments = self.env["hr.department"].with_context(active_test=False)
        return Domain(
            "department_id", "any!", departments._search(self.env["project.project"]._get_openable_department_domain())
        )
