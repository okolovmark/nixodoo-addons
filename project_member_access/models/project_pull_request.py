from odoo import _, api, models
from odoo.exceptions import AccessError

from .project_member import RESTRICTED_ACCESS_MODES


class ProjectPullRequest(models.Model):
    _inherit = "project.pull.request"

    @api.model_create_multi
    def create(self, vals_list):
        pull_requests = super().create(vals_list)
        pull_requests._ensure_restricted_tasks_written()
        return pull_requests

    def write(self, vals):
        self._ensure_restricted_tasks_written()
        result = super().write(vals)
        if "task_ids" in vals:
            # again for the tasks linked: those unlinked were checked before
            self._ensure_restricted_tasks_written()
        return result

    def unlink(self):
        self._ensure_restricted_tasks_written()
        return super().unlink()

    def _apply_ir_rules(self, query, mode="read"):
        # a pull request whose tasks are all archived still has tasks to be read through
        super(ProjectPullRequest, self.with_context(active_test=False))._apply_ir_rules(query, mode)

    def _filter_access_rules_python(self, operation):
        # filtered_domain maps all the tasks into one value per leaf, where SQL reads "one of them"
        return self._filter_access_rules(operation)

    @api.model
    def _find_or_create(self, link):
        # sudo: the pull request of a link exists once, also when its tasks are all hidden to the user
        pull_request = self.sudo().search([("url", "=", link.url)])
        return pull_request.with_env(self.env) if pull_request else super()._find_or_create(link)

    def _ensure_restricted_tasks_written(self):
        """Refuse to change these pull requests unless the user writes each of their tasks
        in a Public or Private project, archived included: such a task shows them, and
        linking one changes it. The user may have linked a pull request of a hidden task
        to their own.
        """
        if self.env.su or self.env.user._is_system():
            return
        restricted_tasks = (
            self.sudo()
            .with_context(active_test=False)
            .task_ids.filtered(lambda task: task.project_id.access_mode in RESTRICTED_ACCESS_MODES)
        )
        if restricted_tasks.with_env(self.env)._filter_access_rules("write") != restricted_tasks:
            raise AccessError(
                _("A pull request linked to a task you cannot edit is changed only by those who edit that task.")
            )
