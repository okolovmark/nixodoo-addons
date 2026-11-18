from odoo import api, models
from odoo.exceptions import UserError

SHARE_TARGET_FIELDS = ("res_model", "res_id")


class PortalShare(models.TransientModel):
    _inherit = "portal.share"

    @api.model_create_multi
    def create(self, vals_list):
        self._ensure_not_shared_inside(vals_list)
        return super().create(vals_list)

    @api.model
    def _ensure_not_shared_inside(self, vals_list):
        """Refuse a share wizard of a project or a task of a Public or Private project,
        whoever opens it: the project's wizard adds collaborators on create, and an RPC
        or an action URL reaches the wizards past the refused share actions.
        """
        missing_fields = [name for name in SHARE_TARGET_FIELDS if any(name not in vals for vals in vals_list)]
        defaults = self.default_get(missing_fields) if missing_fields else {}
        for vals in vals_list:
            model_name, res_id = (vals.get(name, defaults.get(name)) for name in SHARE_TARGET_FIELDS)
            if model_name not in ("project.project", "project.task") or not res_id:
                continue
            record = self.env[model_name].sudo().browse(res_id)
            project = record.project_id if model_name == "project.task" else record
            if project._get_restricted_projects():
                raise UserError(project._get_share_refusal_message())
