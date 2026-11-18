from odoo import SUPERUSER_ID, api

from odoo.addons.project_team_dashboard.hooks import align_deadlines_with_planned_ends


def migrate(cr, version):
    if not version:
        return
    align_deadlines_with_planned_ends(api.Environment(cr, SUPERUSER_ID, {}))
