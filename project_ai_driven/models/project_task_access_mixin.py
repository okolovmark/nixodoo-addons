from odoo import models
from odoo.osv import expression


class ProjectTaskAccessMixin(models.AbstractModel):
    """A line of a task (its task_id) is found, read, written, deleted and
    created exactly by whoever can read its task, archived or not: see ADR 0007.
    """

    _name = "project.task.access.mixin"
    _description = "Line Following the Access of its Task"

    def write(self, vals):
        result = super().write(vals)
        if "task_id" in vals:
            # again for the task a line moves to: core checked the task it leaves only
            self.check_access_rule("write")
        return result

    def _apply_ir_rules(self, query, mode="read"):
        """check_access_rule does not come through here: see _filter_access_rules_python."""
        super()._apply_ir_rules(query, mode)
        if self.env.su:
            return
        # not an ir.rule, so _flush_search does not flush the field it filters on
        self.flush_model(["task_id"])
        readable_tasks = self.env["project.task"].with_context(active_test=False)._search([], order="id")
        expression.expression([("task_id", "in", readable_tasks)], self.sudo(), self._table, query)

    def _filter_access_rules_python(self, operation):
        allowed_lines = super()._filter_access_rules_python(operation)
        if self.env.su:
            return allowed_lines
        # the task of a line deleted meanwhile cannot be read; core lets such a line through
        existing_lines = allowed_lines.exists()
        tasks = self.env["project.task"].browse(existing_lines.task_id.ids)
        readable_task_ids = set(tasks._filter_access_rules_python("read").ids)
        return allowed_lines - existing_lines.filtered(lambda line: line.task_id.id not in readable_task_ids)
