from odoo.addons.project_ai_driven.hooks import fill_task_messages


def migrate(cr, version):
    if not version:
        return
    fill_task_messages(cr)
