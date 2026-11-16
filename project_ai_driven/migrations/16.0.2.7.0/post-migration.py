from odoo.addons.project_ai_driven.hooks import convert_questions_to_rows


def migrate(cr, version):
    if not version:
        return
    convert_questions_to_rows(cr, is_update=True)
