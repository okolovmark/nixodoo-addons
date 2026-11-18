from odoo import api, models
from odoo.exceptions import AccessError

from .project_member import RESTRICTED_ACCESS_MODES


class ProjectPullRequest(models.Model):
    _inherit = "project.pull.request"

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse pull requests linked to a task of a Public or Private project the user cannot edit."""
        pull_requests = super().create(vals_list)
        pull_requests._ensure_restricted_tasks_written()
        return pull_requests

    def write(self, vals):
        """Refuse the change unless the user edits every task of a Public or Private project
        linked to these pull requests, before the write and, for the tasks linked, after it.
        """
        self._ensure_restricted_tasks_written()
        result = super().write(vals)
        if "task_ids" in vals:
            # again for the tasks linked: those unlinked were checked before
            self._ensure_restricted_tasks_written()
        return result

    def unlink(self):
        """Refuse unless the user edits every task of a Public or Private project linked to these pull requests."""
        self._ensure_restricted_tasks_written()
        return super().unlink()

    @api.model
    def _find_or_create(self, link):
        # sudo: the pull request of a link exists once, also when its tasks are all hidden to the user
        pull_request = self.sudo().search([("url", "=", link.url)])
        if not pull_request:
            return super()._find_or_create(link)
        # the link names it: linking it reads it, and linked to an own task the rows show it anyway
        self.env._add_to_access_cache(pull_request)
        return pull_request.with_env(self.env)

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
        if restricted_tasks.with_env(self.env)._filtered_access("write") != restricted_tasks:
            raise AccessError(
                self.env._(
                    "A pull request linked to a task you cannot edit is changed only by those who edit that task."
                )
            )
