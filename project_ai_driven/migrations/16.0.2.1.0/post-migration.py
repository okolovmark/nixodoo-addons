from odoo import SUPERUSER_ID, api

from odoo.addons.project_ai_driven.hooks import convert_old_pr_uri_values


def migrate(cr, version):
    if not version:
        return
    convert_old_pr_uri_values(api.Environment(cr, SUPERUSER_ID, {}))
