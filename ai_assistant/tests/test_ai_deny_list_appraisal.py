from odoo.tests import tagged

from .test_ai_assistant_tools import AssistantCase


@tagged("post_install", "-at_install")
class TestAiDenyListAppraisal(AssistantCase):
    def setUp(self):
        super().setUp()
        if "hr.appraisal" not in self.env:
            self.skipTest("hr_appraisal_oca is not installed")

    def test_the_appraisal_fields_of_an_employee_are_denied(self):
        tools = self._tools(self.employee)
        for model_name, field_names in (
            (
                "hr.employee",
                (
                    "can_open_last_appraisal",
                    "appraisal_count",
                    "ongoing_appraisal_count",
                    "last_appraisal_id",
                    "last_appraisal_state",
                ),
            ),
            ("hr.employee.public", ("can_open_last_appraisal", "last_appraisal_id")),
        ):
            for field_name in field_names:
                with self.subTest(model=model_name, field=field_name):
                    self.assertTrue(tools._denied_field(self.env[model_name]._fields[field_name]))

    def test_the_last_appraisal_state_of_a_public_profile_is_denied(self):
        field = self.env["hr.employee.public"]._fields.get("last_appraisal_state")
        if not field:
            self.skipTest("no installed module adds last_appraisal_state to the public profile")
        self.assertTrue(self._tools(self.employee)._denied_field(field))
