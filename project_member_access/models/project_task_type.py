from odoo import _, api, models
from odoo.exceptions import AccessError

from .project_member import RESTRICTED_ACCESS_MODES


class ProjectTaskType(models.Model):
    _inherit = "project.task.type"

    @api.model_create_multi
    def create(self, vals_list):
        stages = super().create(vals_list)
        stages._ensure_restricted_project_admin()
        return stages

    def write(self, vals):
        # before the check in sudo, whose message names the projects
        self.check_access_rights("write")
        self.check_access_rule("write")
        self._ensure_restricted_project_admin()
        result = super().write(vals)
        if "project_ids" in vals:
            self._ensure_restricted_project_admin()
        return result

    @api.ondelete(at_uninstall=False)
    def _unlink_except_restricted_project_stage(self):
        self._ensure_restricted_project_admin()

    def _ensure_restricted_project_admin(self):
        """A stage of a Public or Private project is changed by an Admin of one of those projects."""
        if self.env.su or self.env.user._is_system():
            return
        for stage in self.sudo():
            restricted_projects = stage.project_ids.filtered(
                lambda project: project.access_mode in RESTRICTED_ACCESS_MODES
            )
            if restricted_projects and self.env.user not in restricted_projects.admin_user_ids:
                raise AccessError(
                    _("Only an Admin of %(projects)s changes its stage %(stage)s.")
                    % {"projects": ", ".join(restricted_projects.mapped("display_name")), "stage": stage.display_name}
                )
