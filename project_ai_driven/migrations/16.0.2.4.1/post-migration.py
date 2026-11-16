from odoo.addons.project_ai_driven.hooks import clear_estimates_that_are_not_numbers


def migrate(cr, version):
    if not version:
        return
    clear_estimates_that_are_not_numbers(cr)
