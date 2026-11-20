from odoo import api, fields, models

TESTCASE_STATES = {
    "not_run": "Not run",
    "passed": "Passed",
    "failed": "Failed",
    "waiting": "Waiting",
    "not_applicable": "Not applicable",
}
VERIFIED_STATES = ("passed", "failed")
VERIFICATION_KINDS = {
    "manual": "Manual",
    "automated": "Automated test",
    "demo": "Demo",
}
VERIFICATION_FIELDS = ("verified_by_id", "verified_on", "verification_kind")


class ProjectTaskTestcase(models.Model):
    _name = "project.task.testcase"
    _description = "Testcase"
    _inherit = ["project.task.image.mixin"]

    sequence = fields.Integer(string="Sequence", default=10)
    state = fields.Selection(
        selection=list(TESTCASE_STATES.items()),
        string="Status",
        required=True,
        copy=False,
        default="not_run",
    )
    name = fields.Char(required=True)
    steps_to_execute = fields.Html()
    expected_results = fields.Html()
    comments = fields.Html()
    verified_by_id = fields.Many2one("res.users", string="Verified by", copy=False)
    verified_on = fields.Datetime(string="Verified on", copy=False)
    verification_kind = fields.Selection(
        selection=list(VERIFICATION_KINDS.items()),
        string="Verification",
        copy=False,
    )
    test_reference = fields.Char(
        string="Automated test reference",
        copy=False,
        help="The automated test that verifies this case, e.g. a test selector such as /module:TestClass.test_method",
    )
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True, index=True)
    task_state = fields.Char(string="Task state", related="task_id.stage_id.name", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._prepare_result_values(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._prepare_result_values(vals))

    def action_set_passed(self):
        return self._set_manual_result("passed")

    def action_set_failed(self):
        return self._set_manual_result("failed")

    def action_set_waiting(self):
        return self._set_manual_result("waiting")

    def action_set_not_applicable(self):
        return self._set_manual_result("not_applicable")

    def action_set_not_run(self):
        """Take the result back: no Manual here, the write clears the verification."""
        return self.write({"state": "not_run"})

    def _set_manual_result(self, state):
        """A result given with a button (the task's case list or cards, the
        case form) is a person's verdict: Manual, unless the case already says
        how it is verified.
        """
        unset_cases = self.filtered(lambda record: not record.verification_kind)
        unset_cases.write({"state": state, "verification_kind": "manual"})
        return (self - unset_cases).write({"state": state})

    def _prepare_result_values(self, vals):
        result_vals = dict(vals)
        for field_name, value in self._get_verification_values(result_vals.get("state")).items():
            result_vals.setdefault(field_name, value)
        return result_vals

    def _get_verification_values(self, state):
        """A pass or a fail is a new verification, by the user who gives it
        (sudo keeps the user); Not run takes the verification back.
        """
        if state in VERIFIED_STATES:
            return {"verified_by_id": self.env.user.id, "verified_on": fields.Datetime.now()}
        if state == "not_run":
            return dict.fromkeys(VERIFICATION_FIELDS, False)
        return {}

    def _get_copy_values_from_zero(self):
        """The values of a copy of each case that starts it over: Not run,
        without verification and comments. It keeps the Automated test
        reference, which names a test, not a run.
        """
        return [
            record.copy_data({"state": "not_run", "comments": False, "test_reference": record.test_reference})[0]
            for record in self
        ]

    def _get_result_values(self):
        """The result of this case as values for a copy of it, verification included."""
        self.ensure_one()
        return {
            "state": self.state,
            "verified_by_id": self.verified_by_id.id,
            "verified_on": self.verified_on,
            "verification_kind": self.verification_kind,
            "test_reference": self.test_reference,
        }
