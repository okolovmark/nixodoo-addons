from odoo.addons.project_ai_driven.hooks import categorize_span_notes


def migrate(cr, version):
    if not version:
        return
    categorize_span_notes(cr)
