from odoo import _, models
from odoo.exceptions import UserError


class ProjectMemberAccessLineMixin(models.AbstractModel):
    _name = "project.member.access.line.mixin"
    _description = "Task Line Under Member Access Rules"

    def write(self, vals):
        result = super().write(vals)
        if "project_id" in vals:
            self._check_project_is_task_project()
        if "task_id" in vals and not isinstance(self, self.pool["project.task.access.mixin"]):
            # again for the task a line moves to: core checked the task it leaves only
            # (project.task.access.mixin checks the lines it carries itself)
            self.check_access_rule("write")
        return result

    def _filter_access_rules_python(self, operation):
        # the rules read a line deleted meanwhile, which fails; core lets a missing record through
        existing_lines = self.exists()
        allowed_lines = super(ProjectMemberAccessLineMixin, existing_lines)._filter_access_rules_python(operation)
        return allowed_lines | (self - existing_lines)

    def _apply_ir_rules(self, query, mode="read"):
        # the rules of other operations read the stored project of a line, which core flushes for read rules only
        if mode != "read" and not self.env.su:
            self.flush_model(["project_id", "task_id"])
        return super()._apply_ir_rules(query, mode)

    def _check_project_is_task_project(self):
        """The rules read the stored project of a line: written without its task, core keeps
        it as given (create recomputes it from the task, so only a write is checked).
        """
        if self.env.su:
            return
        if self.sudo().filtered(lambda line: line.project_id != line.task_id.project_id):
            raise UserError(
                _(
                    "Checklist items, question rows, test cases and sessions take the project"
                    " of their task: it is not set on its own."
                )
            )
