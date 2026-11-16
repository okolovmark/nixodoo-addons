from odoo.addons.project_ai_driven.hooks import recompute_task_sizes


def migrate(cr, version):
    if not version:
        return
    recompute_task_sizes(cr)
