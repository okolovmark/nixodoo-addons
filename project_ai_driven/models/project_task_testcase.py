from odoo import fields, models

STATES = {
    "done": "Done",
    "todo": "TODO",
    "waiting": "Waiting",
    "cancelled": "Cancelled",
}


class ProjectTaskTestcase(models.Model):
    _name = "project.task.testcase"
    _description = "Testcase"

    sequence = fields.Integer(string="Sequence", default=10)
    state = fields.Selection(
        selection=list(STATES.items()),
        string="Status",
        required=True,
        copy=False,
        default="todo",
    )
    # copy=True: a copied task carries its cases under their own name, not "name (copy)"
    name = fields.Char(required=True, copy=True)
    steps_to_execute = fields.Html()
    expected_results = fields.Html()
    comments = fields.Html()
    passed = fields.Boolean()
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True, index=True)
    task_state = fields.Char(string="Task state", related="task_id.stage_id.name", readonly=True)

    def change_state_done(self):
        for record in self:
            record.passed = True
            record.state = "done"

    def change_state_todo(self):
        for record in self:
            record.passed = False
            record.state = "todo"

    def change_state_cancelled(self):
        for record in self:
            record.passed = False
            record.state = "cancelled"

    def change_state_waiting(self):
        for record in self:
            record.passed = False
            record.state = "waiting"
