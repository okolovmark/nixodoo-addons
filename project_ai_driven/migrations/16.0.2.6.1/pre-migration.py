from odoo.addons.project_ai_driven.hooks import forget_checklist_activities


def migrate(cr, version):
    if not version:
        return
    forget_checklist_activities(cr)
