from odoo import models

from odoo.addons.project_team_dashboard.models.project_task import Task as DashboardTask


class ProjectTask(models.Model):
    """In a registry that loads project_timeline after project_team_dashboard (installed later in
    the same process), project_timeline's computes would sit above the dashboard's. This module
    loads after both, so its methods are the ones Odoo calls: they call the dashboard's computes
    themselves, never super(), which would reach whichever of the two came last."""

    _inherit = "project.task"

    def _compute_planned_date_start(self):
        DashboardTask._compute_planned_date_start(self)

    def _compute_planned_date_end(self):
        DashboardTask._compute_planned_date_end(self)
