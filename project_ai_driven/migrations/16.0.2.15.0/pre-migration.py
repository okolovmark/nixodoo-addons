from odoo.addons.project_ai_driven.hooks import forget_dropped_fields


def migrate(cr, version):
    if not version:
        return
    forget_dropped_fields(cr)
