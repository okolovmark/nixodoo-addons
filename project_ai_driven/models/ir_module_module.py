from odoo import api, models


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    @api.depends_context("module_technical_name")
    def _compute_display_name(self):
        return super()._compute_display_name()

    def name_get(self):
        """The technical name under the context key module_technical_name: a task names its modules by it."""
        if self.env.context.get("module_technical_name"):
            return [(module.id, module.name) for module in self]
        return super().name_get()
