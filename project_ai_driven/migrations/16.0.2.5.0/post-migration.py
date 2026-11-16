from odoo.addons.project_ai_driven.hooks import convert_test_case_states


def migrate(cr, version):
    if not version:
        return
    convert_test_case_states(cr)
