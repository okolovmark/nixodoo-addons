from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Give the projects and tasks that exist their keys: the update adds the columns empty."""
    if not version:
        return
    api.Environment(cr, SUPERUSER_ID, {})["project.project"]._set_missing_keys()
