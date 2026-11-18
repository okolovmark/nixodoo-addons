from odoo import api, models


class ProjectTaskSession(models.Model):
    _name = "project.task.session"
    _inherit = ["project.task.session", "project.member.access.line.mixin"]

    @api.model
    def _group_session_values(self, keys, vals_list, rows_by_key):
        """A row found keeps the project of its task, as a new row takes it:
        a project given with the values is not written on it.
        """
        update_vals_by_row, create_vals_by_key = super()._group_session_values(keys, vals_list, rows_by_key)
        for update_vals in update_vals_by_row.values():
            update_vals.pop("project_id", None)
        return update_vals_by_row, create_vals_by_key
