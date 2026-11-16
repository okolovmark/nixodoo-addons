from odoo.addons.project_ai_driven.hooks import forget_dropped_test_case_buttons


def migrate(cr, version):
    if not version:
        return
    forget_dropped_test_case_buttons(cr)
