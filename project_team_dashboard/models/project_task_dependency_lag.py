from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProjectTaskDependencyLag(models.Model):
    _name = "project.task.dependency.lag"
    _description = "Task Dependency Lag"

    task_id = fields.Many2one(
        "project.task",
        string="Task",
        required=True,
        ondelete="cascade",
        index=True,
    )
    depends_on_id = fields.Many2one(
        "project.task",
        string="Predecessor",
        required=True,
        ondelete="cascade",
    )
    lag_days = fields.Integer(
        string="Lag, days",
        help="Minimum number of calendar days between the predecessor's end and this task's start.",
    )

    _sql_constraints = [
        (
            "task_depends_on_uniq",
            "unique(task_id, depends_on_id)",
            "A dependency can have only one lag.",
        ),
    ]

    @api.constrains("lag_days")
    def _check_lag_days(self):
        if any(record.lag_days < 0 for record in self):
            raise ValidationError(_("A dependency lag cannot be negative."))

    @api.constrains("task_id", "depends_on_id")
    def _check_dependency_exists(self):
        self.task_id.invalidate_recordset(["depend_on_ids"])
        for record in self:
            if (
                record.depends_on_id == record.task_id
                or record.depends_on_id not in record.task_id.with_context(active_test=False).depend_on_ids
            ):
                raise ValidationError(
                    _("A lag can only be set on an existing dependency of task '%s'.") % record.task_id.display_name
                )
