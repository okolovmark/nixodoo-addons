from odoo import api, fields, models
from odoo.exceptions import UserError

SUBTASK_STATES = {
    "done": "Done",
    "todo": "TODO",
    "waiting": "Waiting",
    "cancelled": "Cancelled",
}


class ProjectTaskSubtask(models.Model):
    _name = "project.task.subtask"
    _description = "Subtask"
    _inherit = ["mail.activity.mixin"]

    state = fields.Selection(
        selection=list(SUBTASK_STATES.items()),
        string="Status",
        required=True,
        copy=False,
        default="todo",
    )
    # copy=True: a copied task carries its items under their own name, not "name (copy)"
    name = fields.Char(required=True, string="Description", copy=True)
    reviewer_id = fields.Many2one("res.users", "Reviewer", readonly=True, default=lambda self: self.env.user)
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    user_id = fields.Many2one("res.users", "Assigned to", required=True)
    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True, index=True)
    task_state = fields.Char(string="Task state", related="task_id.stage_id.name", readonly=True)
    hide_button = fields.Boolean(compute="_compute_hide_button")
    recolor = fields.Boolean(compute="_compute_recolor")
    deadline = fields.Datetime()

    @api.depends("reviewer_id", "user_id")
    def _compute_hide_button(self):
        for record in self:
            if self.env.user not in [record.reviewer_id, record.user_id]:
                record.hide_button = True
            else:
                record.hide_button = False

    @api.depends("user_id", "state")
    def _compute_recolor(self):
        for record in self:
            if self.env.user == record.user_id and record.state == "todo":
                record.recolor = True
            else:
                record.recolor = False

    def _compute_reviewer_id(self):
        for record in self:
            record.reviewer_id = record.create_uid

    @api.model_create_multi
    def create(self, vals_list):
        result = super().create(vals_list)
        for vals in vals_list:
            vals = self._add_missing_default_values(vals)
            task = self.env["project.task"].browse(vals.get("task_id"))
            task.send_subtask_email(vals["name"], vals["state"], vals["reviewer_id"], vals["user_id"])
        return result

    def write(self, vals):
        old_names = dict(zip(self.mapped("id"), self.mapped("name")))
        result = super().write(vals)
        for record in self:
            if vals.get("state"):
                record.task_id.send_subtask_email(record.name, record.state, record.reviewer_id.id, record.user_id.id)
                if self.env.user != record.reviewer_id and self.env.user != record.user_id:
                    raise UserError(self.env._("Only users related to that subtask can change state."))
            if vals.get("name"):
                record.task_id.send_subtask_email(
                    record.name,
                    record.state,
                    record.reviewer_id.id,
                    record.user_id.id,
                    old_name=old_names[record.id],
                )
                if self.env.user != record.reviewer_id and self.env.user != record.user_id:
                    raise UserError(self.env._("Only users related to that subtask can change state."))
            if vals.get("user_id"):
                record.task_id.send_subtask_email(record.name, record.state, record.reviewer_id.id, record.user_id.id)
        return result

    def change_state_done(self):
        for record in self:
            record.state = "done"

    def change_state_todo(self):
        for record in self:
            record.state = "todo"

    def change_state_cancelled(self):
        for record in self:
            record.state = "cancelled"

    def change_state_waiting(self):
        for record in self:
            record.state = "waiting"
