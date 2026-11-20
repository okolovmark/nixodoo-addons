from odoo import api, fields, models

from .project_task_recurrence import is_next_occurrence_creation

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
    _inherit = ["project.task.image.mixin", "project.task.access.mixin"]

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
        default_state = self._get_created_default_state(vals_list)
        return super().create([self._prepare_result_values(vals, default_state, on_create=True) for vals in vals_list])

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
        """A result given with a button (the task's case list or cards, or the
        header of a case opened from the Test Cases menu) is a person's verdict:
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

    def _prepare_result_values(self, vals, default_state=False, on_create=False):
        """vals with the verification their state gives: a pass or a fail, given
        or taken from default_state, is a new verification by the user who gives
        it (sudo keeps the user) where vals name no verifier or date; a Not run
        given takes the verification back, whatever vals give, but for the
        Verification of a case the recurrence prepares for the next task
        (_get_copy_values_from_zero). A case Not run by default is a prepared
        one: it keeps the verification vals give.
        """
        if vals.get("state") == "not_run":
            taken_back_fields = VERIFICATION_FIELDS
            if on_create and is_next_occurrence_creation(self.env):
                taken_back_fields = ("verified_by_id", "verified_on")
            return {**vals, **dict.fromkeys(taken_back_fields, False)}
        if vals.get("state", default_state) in VERIFIED_STATES:
            return {"verified_by_id": self.env.user.id, "verified_on": fields.Datetime.now(), **vals}
        return dict(vals)

    def _get_copy_values_from_zero(self):
        """The values of a copy of each case that starts it over, for the next
        task of a recurrence: Not run, without Verified by, Verified on and
        comments. It keeps the Automated test reference, which names a test,
        not a run, and the Verification of a case prepared Not run, which says
        how it is to be verified; a case with a result loses the Verification
        of that result.

        Not a Verification written after the creation, as a task copy writes
        it: core creates the sub-tasks of the next task without pairing them
        with those they repeat, so the Not run given keeps it there.
        """
        return [
            record.copy_data(
                {
                    "state": "not_run",
                    "comments": False,
                    "test_reference": record.test_reference,
                    "verification_kind": record.state == "not_run" and record.verification_kind,
                }
            )[0]
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

    def _copy_to_task(self, task):
        """A copy of this case on task with its result as it is."""
        self.ensure_one()
        result_values = self._get_result_values()
        case_copy = self.copy({"task_id": task.id, **result_values})
        verification_values = {fname: result_values[fname] for fname in VERIFICATION_FIELDS}
        if self.state == "not_run" and any(verification_values.values()):
            # the Not run the copy is given takes its verification back; written alone it stays
            case_copy.write(verification_values)
        return case_copy
