from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    api.Environment(cr, SUPERUSER_ID, {})["project.task"]._renew_stale_keys()
