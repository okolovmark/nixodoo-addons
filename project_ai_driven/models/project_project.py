from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ProjectProject(models.Model):
    _inherit = "project.project"

    is_ai_driven = fields.Boolean(string="AI-driven Project")
    develops_this_odoo = fields.Boolean(
        string="Develops This Odoo",
        copy=False,
        tracking=True,
        help="The tasks of this project change this Odoo database: their form shows the installed"
        " modules they touch. One project of the database at most carries it.",
    )
    # Name, table and columns of project_task_pull_request: the stages it marked stay marked.
    pr_required_states = fields.Many2many(
        "project.task.type",
        "project_pr_required",
        "project_id",
        "state_id",
        string="Stages Requiring a Resolution",
        help="A task of an AI-driven project enters these stages only with a Resolution,"
        " and with a pull request when the Resolution is Code.",
    )
    estimate_required_states = fields.Many2many(
        "project.task.type",
        "project_estimate_required",
        "project_id",
        "state_id",
        string="Stages Requiring a Type and an Estimate",
        help="A task of an AI-driven project enters these stages only with a task type"
        " and an estimate above zero hours.",
    )

    @api.constrains("develops_this_odoo", "is_ai_driven")
    def _check_develops_this_odoo_is_ai_driven(self):
        not_ai_driven_projects = self.filtered(lambda record: record.develops_this_odoo and not record.is_ai_driven)
        if not_ai_driven_projects:
            raise ValidationError(
                _(
                    "Only an AI-driven project develops this Odoo: set AI-driven Project on it, or have a"
                    " Settings administrator clear Develops This Odoo on it or move it to another project"
                    " first.\nProjects: %s"
                )
                % ", ".join(not_ai_driven_projects.mapped("display_name"))
            )

    @api.constrains("develops_this_odoo")
    def _check_one_project_develops_this_odoo(self):
        if not any(self.mapped("develops_this_odoo")):
            return
        # Archived projects count; sudo: the one holding the flag may be in a company the user does not see.
        flagged_projects = self.sudo().with_context(active_test=False).search([("develops_this_odoo", "=", True)])
        if len(flagged_projects) > 1:
            holding_projects = (flagged_projects - self) or flagged_projects
            raise ValidationError(
                _("One project at most develops this Odoo, and Develops This Odoo is set on %s: clear it there first.")
                % ", ".join(holding_projects.mapped("display_name"))
            )

    @api.model_create_multi
    def create(self, vals_list):
        # Before super(): an explicit flag meets the access error, not the constraints run inside it.
        if any(vals.get("develops_this_odoo") for vals in vals_list):
            self._ensure_develops_this_odoo_editable()
        projects = super().create(vals_list)
        # And on the records: a context default sets the flag inside super().
        if any(projects.mapped("develops_this_odoo")):
            projects._ensure_develops_this_odoo_editable()
        return projects

    def write(self, vals):
        if "develops_this_odoo" in vals and self.filtered(
            lambda record: record.develops_this_odoo != bool(vals["develops_this_odoo"])
        ):
            self._ensure_develops_this_odoo_editable()
        if "active" in vals and not vals["active"]:
            self.filtered("active")._ensure_develops_this_odoo_project_kept()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_develops_this_odoo(self):
        self._ensure_develops_this_odoo_project_kept()

    def _ensure_develops_this_odoo_editable(self):
        # The field itself carries no groups: the task form and its related field read it for every project user.
        if not self.env.is_system():
            raise AccessError(_("Only a Settings administrator sets or clears Develops This Odoo on a project."))

    def _ensure_develops_this_odoo_project_kept(self):
        flagged_projects = self.filtered("develops_this_odoo")
        if flagged_projects and not self.env.is_system():
            raise AccessError(
                _(
                    "%s develops this Odoo: a Settings administrator moves Develops This Odoo to another project"
                    " or clears it before the project is archived or deleted."
                )
                % ", ".join(flagged_projects.mapped("display_name"))
            )
