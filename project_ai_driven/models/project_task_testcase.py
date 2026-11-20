from odoo import api, fields, models

from .task_line import check_task_line_move

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
    # copy=True: a copied task carries its cases under their own name, not "name (copy)"
    name = fields.Char(required=True, copy=True)
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
        default_state = self._get_created_default_state(vals_list)
        return super().create(
            [self._prepare_result_values(vals, vals.get("state", default_state)) for vals in vals_list]
        )

    def write(self, vals):
        check_task_line_move(self, vals)
        return super().write(self._prepare_result_values(vals, vals.get("state")))

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
        form of a case opened from the Test Cases menu) is a person's verdict:
        Manual, unless the case already says how it is verified.
        """
        unset_cases = self.filtered(lambda record: not record.verification_kind)
        unset_cases.write({"state": state, "verification_kind": "manual"})
        return (self - unset_cases).write({"state": state})

    @api.model
    def _get_created_default_state(self, vals_list):
        """The state a create gives a case sent without one: a context or user default included."""
        if all("state" in vals for vals in vals_list):
            return False
        return self.default_get(["state"]).get("state")

    def _prepare_result_values(self, vals, state):
        """Return vals with the verification of state, the state the records
        get (a create's default included): a pass or a fail fills what vals
        leave out. Only a Not run vals give clears it, whatever vals give with
        it: a case prepared Not run by default keeps its Verification.
        """
        if vals.get("state") == "not_run":
            return {**vals, **dict.fromkeys(VERIFICATION_FIELDS, False)}
        return {**self._get_verification_values(state), **vals}

    def _get_verification_values(self, state):
        """A pass or a fail is a new verification, by the user who gives it
        (sudo keeps the user).
        """
        if state in VERIFIED_STATES:
            return {"verified_by_id": self.env.user.id, "verified_on": fields.Datetime.now()}
        return {}

    def _get_task_copy_values(self, keep_progress):
        """The values of a copy of each case for a copy of its task: it keeps
        its automated test reference, and with keep_progress its result,
        verification included, and its comments; else it is Not run without
        comments. A Not run case gets its verification back after the copy,
        see _write_verification_on_copy.
        """
        return [
            {
                **values,
                "test_reference": case.test_reference,
                **(case._get_result_values() if keep_progress else {"state": "not_run", "comments": False}),
            }
            for case, values in zip(self, self.copy_data())
        ]

    def _get_result_values(self):
        """The result of this case as values for a copy of it, verification included."""
        self.ensure_one()
        return {
            "state": self.state,
            "verified_by_id": self.verified_by_id.id,
            "verified_on": self.verified_on,
            "verification_kind": self.verification_kind,
        }

    def _write_verification_on_copy(self, case_copy, keep_progress):
        """Write the verification of this case, when it is Not run, on its
        copy case_copy. The copy is created with its state, as a default may
        be another one, and a Not run given takes the verification back; a
        verification written alone stays. Without keep_progress only the
        Verification is written: how a case is to be verified is part of
        preparing it, as its Automated test reference, while Verified by and
        Verified on stamp a result.
        """
        self.ensure_one()
        result_values = self._get_result_values()
        field_names = VERIFICATION_FIELDS if keep_progress else ("verification_kind",)
        verification_values = {fname: result_values[fname] for fname in field_names}
        if self.state == "not_run" and any(verification_values.values()):
            case_copy.write(verification_values)
