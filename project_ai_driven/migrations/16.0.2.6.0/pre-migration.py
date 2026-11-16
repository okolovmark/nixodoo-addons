from odoo.addons.project_ai_driven.hooks import rename_checklist


def migrate(cr, version):
    if not version:
        return
    rename_checklist(cr)
