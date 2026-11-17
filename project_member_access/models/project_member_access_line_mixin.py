from odoo import models


class ProjectMemberAccessLineMixin(models.AbstractModel):
    _name = "project.member.access.line.mixin"
    _description = "Task Line Under Member Access Rules"

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
