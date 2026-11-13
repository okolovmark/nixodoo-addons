from odoo import SUPERUSER_ID, api

from odoo.addons.project_ai_driven.hooks import fill_stage_log


def migrate(cr, version):
    if not version:
        return
    fill_stage_log(api.Environment(cr, SUPERUSER_ID, {}))
