import copy
import importlib.util
import logging
from ast import literal_eval
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import Form, tagged
from odoo.tools.sql import column_exists

from ..hooks import MODULE, convert_test_case_states
from ..models.project_task_image_mixin import ImageMover, get_image_source
from .common import ProjectAiDrivenCommon
from .test_inline_images import RED, data_uri, image_html, image_tag

MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.5.0" / "post-migration.py"
HOOKS_LOGGER = "odoo.addons.%s.hooks" % MODULE
OLD_STATES = ("todo", "done", "cancelled")
BUTTON_STATES = {
    "action_set_passed": "passed",
    "action_set_failed": "failed",
    "action_set_not_run": "not_run",
    "action_set_waiting": "waiting",
    "action_set_not_applicable": "not_applicable",
}
TEST_REFERENCE = "/module:TestClass.test_method"
EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
STATE_DECORATIONS = {
    "decoration-danger": "state == 'failed'",
    "decoration-success": "state == 'passed'",
    "decoration-info": "state == 'waiting'",
    "decoration-muted": "state == 'not_applicable'",
}
COMPLETE_VERIFICATION = "record.verified_by_id.raw_value and record.verified_on.raw_value"
# A stored state, and what the conversion leaves of it.
STORED_CASES = (
    ("todo", "not_run"),
    ("todo", "not_run"),
    ("done", "passed"),
    ("done", "passed"),
    ("done", "passed"),
    ("cancelled", "not_applicable"),
    ("cancelled", "not_applicable"),
    ("not_run", "not_run"),
    ("passed", "passed"),
    ("failed", "failed"),
    ("waiting", "waiting"),
    ("not_applicable", "not_applicable"),
)
STORED_CASE_ROWS = """
    SELECT id, state, write_date, write_uid, verified_by_id, verified_on, verification_kind
      FROM project_task_testcase
     WHERE id IN %s
  ORDER BY id
"""


@tagged("post_install", "-at_install")
class TestTestCases(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Testcase = cls.env["project.task.testcase"]

    def _new_case(self, **vals):
        return self.Testcase.create({"name": "Boot test", "task_id": self.task.id, **vals})

    def _results(self, cases):
        return sorted(
            (
                case.name,
                case.state,
                case.verified_by_id.id,
                case.verified_on,
                case.verification_kind,
                case.test_reference,
            )
            for case in cases
        )

    def test_the_passed_flag_is_gone(self):
        self.assertNotIn("passed", self.Testcase._fields)
        self.assertFalse(self.env["ir.model.fields"]._get("project.task.testcase", "passed"))
        self.assertFalse(column_exists(self.env.cr, "project_task_testcase", "passed"))
        case = self._new_case()
        refusal = r"^Invalid field 'passed' on model 'project\.task\.testcase'$"
        with self.assertRaisesRegex(ValueError, refusal):
            self._new_case(passed=True)
        with self.assertRaisesRegex(ValueError, refusal):
            case.write({"passed": True})

    def test_an_old_state_value_is_refused_at_create_and_write(self):
        case = self._new_case(state="failed")
        for old_state in OLD_STATES:
            refusal = r"^Wrong value for project\.task\.testcase\.state: '%s'$" % old_state
            with self.subTest(old_state=old_state):
                with self.assertRaisesRegex(ValueError, refusal):
                    self._new_case(state=old_state)
                with self.assertRaisesRegex(ValueError, refusal):
                    case.write({"state": old_state})
        self.assertEqual(case.state, "failed")

    def test_a_batch_create_leaves_the_values_as_sent(self):
        vals_list = [
            {"name": name, "task_id": self.task.id, **vals}
            for name, vals in (
                ("Pass", {"state": "passed"}),
                ("Fail", {"state": "failed", "verification_kind": "automated"}),
                ("Not applicable", {"state": "not_applicable"}),
                ("Untouched", {}),
            )
        ]
        sent_vals_list = copy.deepcopy(vals_list)
        cases = self.Testcase.create(vals_list)
        self.assertEqual(vals_list, sent_vals_list)
        self.assertEqual(cases.mapped("state"), ["passed", "failed", "not_applicable", "not_run"])

    def test_a_write_leaves_the_values_as_sent(self):
        case = self._new_case(state="failed")
        vals = {"state": "passed"}
        case.write(vals)
        self.assertEqual(vals, {"state": "passed"})
        self.assertEqual(case.state, "passed")

    def test_a_pass_or_a_fail_is_stamped_with_the_user_who_gives_it(self):
        case = self._new_case()
        with freeze_time(EARLIER):
            case.with_user(self.user_dev).write({"state": "passed"})
        self.assertRecordValues(case, [{"verified_by_id": self.user_dev.id, "verified_on": EARLIER}])
        with freeze_time(LATER):
            case.with_user(self.user_lead).write({"state": "failed"})
        self.assertRecordValues(case, [{"verified_by_id": self.user_lead.id, "verified_on": LATER}])

    def test_a_re_run_is_a_new_verification(self):
        case = self._new_case()
        with freeze_time(EARLIER):
            case.with_user(self.user_dev).write({"state": "passed"})
        with freeze_time(LATER):
            case.with_user(self.user_lead).write({"state": "passed"})
        self.assertRecordValues(case, [{"verified_by_id": self.user_lead.id, "verified_on": LATER}])

    def test_sudo_stamps_the_user_making_the_change(self):
        sudo_testcase = self.Testcase.with_user(self.user_lead).sudo()
        with freeze_time(EARLIER):
            case = sudo_testcase.create({"name": "Sudo case", "task_id": self.task.id, "state": "passed"})
        self.assertRecordValues(case, [{"verified_by_id": self.user_lead.id, "verified_on": EARLIER}])
        with freeze_time(LATER):
            case.with_user(self.user_dev).sudo().write({"state": "failed"})
        self.assertRecordValues(case, [{"verified_by_id": self.user_dev.id, "verified_on": LATER}])

    def test_a_verification_given_with_the_result_is_kept(self):
        given_case = self._new_case()
        cleared_case = self._new_case()
        with freeze_time(LATER):
            given_case.with_user(self.user_dev).write(
                {"state": "passed", "verified_by_id": self.user_lead.id, "verified_on": EARLIER},
            )
            cleared_case.with_user(self.user_dev).write({"state": "failed", "verified_by_id": False})
        self.assertRecordValues(
            given_case | cleared_case,
            [
                {"verified_by_id": self.user_lead.id, "verified_on": EARLIER},
                {"verified_by_id": False, "verified_on": LATER},
            ],
        )

    def test_not_run_takes_the_verification_back_and_keeps_the_reference(self):
        case = self._new_case(state="passed", verification_kind="automated", test_reference=TEST_REFERENCE)
        self.assertTrue(case.verified_by_id)
        case.write({"state": "not_run"})
        self.assertRecordValues(
            case,
            [
                {
                    "verified_by_id": False,
                    "verified_on": False,
                    "verification_kind": False,
                    "test_reference": TEST_REFERENCE,
                },
            ],
        )

    def test_waiting_and_not_applicable_leave_the_verification(self):
        for state in ("waiting", "not_applicable"):
            with freeze_time(EARLIER):
                case = self.Testcase.with_user(self.user_dev).create(
                    {
                        "name": "Verified case",
                        "task_id": self.task.id,
                        "state": "passed",
                        "verification_kind": "demo",
                        "test_reference": TEST_REFERENCE,
                    },
                )
            with freeze_time(LATER):
                case.with_user(self.user_lead).write({"state": state})
            self.assertEqual(
                self._results(case),
                [("Verified case", state, self.user_dev.id, EARLIER, "demo", TEST_REFERENCE)],
            )

    def test_a_case_created_passed_or_failed_is_stamped(self):
        with freeze_time(EARLIER):
            cases = self.Testcase.with_user(self.user_dev).create(
                [
                    {"name": name, "task_id": self.task.id, **vals}
                    for name, vals in (
                        ("Passed", {"state": "passed"}),
                        ("Failed", {"state": "failed"}),
                        ("Waiting", {"state": "waiting"}),
                        ("Prepared", {"verification_kind": "automated", "test_reference": TEST_REFERENCE}),
                    )
                ],
            )
        self.assertRecordValues(
            cases,
            [
                {"verified_by_id": self.user_dev.id, "verified_on": EARLIER, "verification_kind": False},
                {"verified_by_id": self.user_dev.id, "verified_on": EARLIER, "verification_kind": False},
                {"verified_by_id": False, "verified_on": False, "verification_kind": False},
                {"verified_by_id": False, "verified_on": False, "verification_kind": "automated"},
            ],
        )

    def test_a_pass_or_a_fail_a_create_takes_from_a_default_is_stamped(self):
        self.env["ir.default"].set("project.task.testcase", "state", "failed", user_id=self.user_dev.id)
        as_developer = self.Testcase.with_user(self.user_dev)
        with freeze_time(EARLIER):
            context_cases = as_developer.with_context(default_state="passed").create(
                [
                    {"name": "Context default", "task_id": self.task.id},
                    {"name": "Given state", "task_id": self.task.id, "state": "waiting"},
                ],
            )
            user_default_case = as_developer.create({"name": "User default", "task_id": self.task.id})
        self.assertRecordValues(
            context_cases | user_default_case,
            [
                {"state": "passed", "verified_by_id": self.user_dev.id, "verified_on": EARLIER},
                {"state": "waiting", "verified_by_id": False, "verified_on": False},
                {"state": "failed", "verified_by_id": self.user_dev.id, "verified_on": EARLIER},
            ],
        )

    def test_not_run_takes_back_a_verification_given_with_it(self):
        given_verification = {"verified_by_id": self.user_lead.id, "verified_on": EARLIER, "verification_kind": "demo"}
        case = self._new_case(state="passed", test_reference=TEST_REFERENCE)
        case.write({"state": "not_run", **given_verification})
        written_case = self._new_case(name="Written verification")
        written_case.write(given_verification)
        created_cases = self.Testcase.create(
            [
                {"name": "Given Not run", "task_id": self.task.id, "state": "not_run", **given_verification},
                {
                    "name": "Default Not run",
                    "task_id": self.task.id,
                    "test_reference": TEST_REFERENCE,
                    **given_verification,
                },
            ],
        )
        self.assertEqual(
            self._results(case | written_case | created_cases),
            [
                ("Boot test", "not_run", False, False, False, TEST_REFERENCE),
                ("Default Not run", "not_run", self.user_lead.id, EARLIER, "demo", TEST_REFERENCE),
                ("Given Not run", "not_run", False, False, False, False),
                ("Written verification", "not_run", self.user_lead.id, EARLIER, "demo", False),
            ],
        )

    def test_the_result_buttons_give_a_manual_result_where_no_kind_is_set(self):
        for method_name, state in BUTTON_STATES.items():
            if state == "not_run":
                continue
            cases = self._new_case(name="Unset") | self._new_case(name="Automated", verification_kind="automated")
            self.assertIs(getattr(cases.with_user(self.user_dev), method_name)(), True, method_name)
            stamped_user = self.user_dev.id if state in ("passed", "failed") else False
            self.assertRecordValues(
                cases,
                [
                    {"state": state, "verification_kind": "manual", "verified_by_id": stamped_user},
                    {"state": state, "verification_kind": "automated", "verified_by_id": stamped_user},
                ],
            )

    def test_a_result_button_writes_each_case_once(self):
        testcase_class = type(self.Testcase)
        model_write = testcase_class.write
        written_ids = []

        def recording_write(records, vals):
            written_ids.extend(records.ids)
            return model_write(records, vals)

        cases = self._new_case(name="Unset") | self._new_case(name="Automated", verification_kind="automated")
        with patch.object(testcase_class, "write", recording_write):
            cases.with_user(self.user_dev).action_set_failed()
        self.assertCountEqual(written_ids, cases.ids)
        self.assertEqual(cases.mapped("verification_kind"), ["manual", "automated"])

    def test_the_not_run_button_takes_the_result_back(self):
        case = self._new_case(state="failed", verification_kind="manual", test_reference=TEST_REFERENCE)
        self.assertIs(case.with_user(self.user_dev).action_set_not_run(), True)
        self.assertEqual(self._results(case), [("Boot test", "not_run", False, False, False, TEST_REFERENCE)])

    def test_completion_counts_passed_over_every_applicable_case(self):
        self.assertEqual(self.task.testcase_completion, 100, "no case yet")
        self._new_case(name="Dropped", state="not_applicable")
        self.assertEqual(self.task.testcase_completion, 100, "no applicable case")
        self._new_case(name="Passed", state="passed")
        failed_case = self._new_case(name="Failed", state="failed")
        self._new_case(name="Not run")
        self.assertEqual(self.task.testcase_completion, 33)
        self.assertIn("33.33% Done", self.task.testcase_completion_xml)
        self.assertIn("<ul><li><b>Not run: 1</b></li><li><b>Failed: 1</b></li></ul>", self.task.kanban_testcases)

        failed_case.write({"state": "passed"})
        self.assertEqual(self.task.testcase_completion, 66)
        self.assertIn("66.67% Done", self.task.testcase_completion_xml)
        self.assertIn("<ul><li><b>Not run: 1</b></li></ul>", self.task.kanban_testcases)
        self.assertNotIn("Failed", self.task.kanban_testcases)

    def test_the_card_names_the_completion_of_every_case_not_the_reader_s_own(self):
        self._new_case(name="Passed", state="passed")
        self._new_case(name="Not run")

        card_text = self.task.with_user(self.user_lead).testcase_completion_xml

        self.assertIn("Test cases:", card_text)
        self.assertIn("50.0% Done", card_text)
        self.assertNotIn("Your", card_text, "a case has no assignee: the figure is the task's, not the reader's")

    def test_kanban_lists_the_cases_not_run_failed_and_waiting(self):
        self._new_case(state="passed")
        self._new_case(state="not_applicable")
        self.assertNotIn("<li>", self.task.kanban_testcases)
        for state in ("waiting", "failed", "not_run", "not_run"):
            self._new_case(state=state)
        self.assertIn(
            "<ul><li><b>Not run: 2</b></li><li><b>Failed: 1</b></li><li><b>Waiting: 1</b></li></ul>",
            self.task.kanban_testcases,
        )

    def test_copying_a_task_copies_the_result_of_its_cases(self):
        with freeze_time(EARLIER):
            self.Testcase.with_user(self.user_dev).create(
                {
                    "name": "Passed case",
                    "task_id": self.task.id,
                    "state": "passed",
                    "verification_kind": "automated",
                    "test_reference": TEST_REFERENCE,
                },
            )
        self._new_case(name="Unstamped pass", state="passed", verified_by_id=False, verified_on=False)
        self._new_case(name="Open case", test_reference=TEST_REFERENCE)
        self._new_case(name="Prepared case", verification_kind="automated", test_reference=TEST_REFERENCE)
        results = [
            ("Open case", "not_run", False, False, False, TEST_REFERENCE),
            ("Passed case", "passed", self.user_dev.id, EARLIER, "automated", TEST_REFERENCE),
            ("Prepared case", "not_run", False, False, "automated", TEST_REFERENCE),
            ("Unstamped pass", "passed", False, False, False, False),
        ]
        self.assertEqual(self._results(self.task.testcase_ids), results)

        with freeze_time(LATER):
            task_copy = self.task.copy()

        self.assertEqual(self._results(task_copy.testcase_ids), results)
        self.assertEqual(self._results(self.task.testcase_ids), results, "the original keeps its own cases")
        self.assertFalse(task_copy.testcase_ids & self.task.testcase_ids)

    def test_test_cases_need_project_user(self):
        case = self._new_case()
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        self.assertEqual(self.Testcase.with_user(self.user_dev).search_count([("task_id", "=", self.task.id)]), 1)
        with self.assertRaises(AccessError):
            self.Testcase.with_user(internal_user).search_count([])
        with self.assertRaises(AccessError):
            case.with_user(internal_user).action_set_passed()
        case.with_user(self.user_dev).action_set_passed()
        self.assertRecordValues(
            case,
            [{"state": "passed", "verified_by_id": self.user_dev.id, "verification_kind": "manual"}],
        )

    def test_the_list_and_the_cards_offer_each_case_the_other_results(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        cases_field = form_arch.xpath("//field[@name='testcase_ids']")[0]
        list_buttons = cases_field.xpath("./tree/button")
        self.assertEqual(
            {button.get("name"): literal_eval(button.get("attrs")) for button in list_buttons},
            {name: {"invisible": [("state", "=", state)]} for name, state in BUTTON_STATES.items()},
        )
        self.assertTrue(all(button.get("title") and button.get("icon") for button in list_buttons))
        self.assertNotIn("passed", cases_field.xpath("./tree/field/@name"))
        self.assertEqual(
            {button.get("name"): button.get("t-if") for button in cases_field.xpath("./kanban//button")},
            {name: "record.state.raw_value != '%s'" % state for name, state in BUTTON_STATES.items()},
        )

    def test_a_card_of_the_task_form_shows_the_verification_only_when_complete(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (card_template,) = form_arch.xpath("//field[@name='testcase_ids']/kanban//t[@t-name='kanban-box']")
        (verification_line,) = card_template.xpath(".//div[field[@name='verified_on']]")
        self.assertEqual(verification_line.get("t-if"), COMPLETE_VERIFICATION)

    def test_the_case_form_gives_the_other_results_from_its_header(self):
        form_arch = self.env.ref("%s.project_task_testcase_view_form" % MODULE)._get_combined_arch()
        header_buttons = form_arch.xpath("/form/header/button")
        self.assertEqual(
            {button.get("name"): literal_eval(button.get("attrs")) for button in header_buttons},
            {name: {"invisible": [("state", "=", state)]} for name, state in BUTTON_STATES.items()},
        )
        self.assertTrue(all(button.get("type") == "object" and button.get("string") for button in header_buttons))
        (state_node,) = form_arch.xpath("//field[@name='state']")
        self.assertEqual(state_node.getparent().tag, "header")
        self.assertEqual(state_node.get("widget"), "statusbar")
        self.assertNotIn("clickable", state_node.get("options", ""))
        self.assertEqual(
            state_node.get("statusbar_visible"),
            "not_run,passed,failed",
            "waiting and not applicable show only on the case that has them",
        )

    def test_the_menu_offers_cards_for_a_phone(self):
        action = self.env.ref("%s.action_view_testcase" % MODULE)
        self.assertEqual(action.view_mode.split(","), ["tree", "kanban", "form"])
        kanban_arch = self.Testcase.get_views([(False, "kanban")])["views"]["kanban"]["arch"]
        card_template = etree.fromstring(kanban_arch).xpath("//t[@t-name='kanban-box']")[0]
        self.assertLessEqual(
            {"name", "task_id", "state", "verified_by_id", "verified_on"},
            set(card_template.xpath(".//field/@name")),
        )
        (state_badge,) = card_template.xpath(".//field[@name='state']")
        self.assertEqual(state_badge.get("widget"), "badge")
        self.assertEqual({key: state_badge.get(key) for key in STATE_DECORATIONS}, STATE_DECORATIONS)
        list_arch = self.env.ref("%s.testcase_list_view" % MODULE)._get_combined_arch()
        self.assertEqual({key: list_arch.get(key) for key in STATE_DECORATIONS}, STATE_DECORATIONS)
        (verification_line,) = card_template.xpath(".//div[field[@name='verified_on']]")
        self.assertEqual(verification_line.get("t-if"), COMPLETE_VERIFICATION)

    def test_the_state_badge_of_a_card_keeps_a_margin_from_a_long_name(self):
        task_form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        card_views = {
            "task form": task_form_arch.xpath("//field[@name='testcase_ids']/kanban")[0],
            "menu": self.env.ref("%s.project_task_testcase_view_kanban" % MODULE)._get_combined_arch(),
        }
        for view_name, card_view in card_views.items():
            with self.subTest(view=view_name):
                (state_badge,) = card_view.xpath(".//t[@t-name='kanban-box']//field[@name='state']")
                self.assertIn("ms-2", state_badge.get("class", "").split())

    def test_the_task_form_adds_a_case_not_run_and_leaves_the_stamp_to_the_rule(self):
        given_values = {
            "state": "passed",
            "verified_by_id": self.user_lead,
            "verified_on": EARLIER,
        }
        with Form(self.task.with_user(self.user_dev)) as task_form:
            with task_form.testcase_ids.new() as case_form:
                case_form.name = "Login works"
                case_form.test_reference = TEST_REFERENCE
                for field_name, value in given_values.items():
                    with self.assertRaisesRegex(AssertionError, "readonly field %s$" % field_name):
                        setattr(case_form, field_name, value)
        self.assertEqual(
            self._results(self.task.testcase_ids), [("Login works", "not_run", False, False, False, TEST_REFERENCE)]
        )

    def test_the_action_opens_on_the_cases_not_run(self):
        action = self.env.ref("%s.action_view_testcase" % MODULE)
        self.assertEqual(literal_eval(action.context.strip()), {"search_default_not_run": 1})
        search_arch = self.env.ref("%s.project_task_testcase_view_search" % MODULE)._get_combined_arch()
        not_run_case = self._new_case()
        failed_case = self._new_case(state="failed")
        self._new_case(state="passed")
        for filter_name, case in (("not_run", not_run_case), ("failed", failed_case)):
            domain = literal_eval(search_arch.xpath("//filter[@name='%s']" % filter_name)[0].get("domain"))
            self.assertEqual(self.Testcase.search(domain + [("task_id", "=", self.task.id)]), case, filter_name)


@tagged("post_install", "-at_install")
class TestTestCaseAccess(ProjectAiDrivenCommon):
    """A global rule hides one task from everyone, whichever modules' task rules are installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.rule"].create(
            {
                "name": "Hide the hidden task",
                "model_id": cls.env["ir.model"]._get_id("project.task"),
                "domain_force": "[('name', '!=', 'Hidden Task')]",
            },
        )
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.project.id})
        cls.both_tasks = cls.task | cls.hidden_task
        cls.Testcase = cls.env["project.task.testcase"]
        cls.hidden_case = cls.Testcase.create({"name": "Hidden case", "task_id": cls.hidden_task.id})
        cls.visible_case = cls.Testcase.create({"name": "Visible case", "task_id": cls.task.id})
        cls.as_developer = cls.Testcase.with_user(cls.user_dev)

    def test_cases_of_a_task_the_user_cannot_read_are_not_found_or_read(self):
        with self.assertRaises(AccessError):
            self.hidden_task.with_user(self.user_dev).read(["name"])
        domain = [("task_id", "in", self.both_tasks.ids)]
        self.assertEqual(self.as_developer.search(domain), self.visible_case)
        self.assertEqual(self.as_developer.search_count(domain), 1)
        groups = self.as_developer.read_group(domain, ["task_id"], ["task_id"])
        self.assertEqual([(group["task_id"][0], group["task_id_count"]) for group in groups], [(self.task.id, 1)])
        self.assertEqual([name for _case_id, name in self.as_developer.name_search("case", domain)], ["Visible case"])
        with self.assertRaises(AccessError):
            self.hidden_case.with_user(self.user_dev).read(["name"])

    def test_a_task_the_user_reads_gives_its_cases_on_a_lazy_read(self):
        self.env.invalidate_all()
        self.assertEqual(self.task.with_user(self.user_dev).testcase_ids, self.visible_case)

    def test_cases_of_a_task_in_a_company_the_user_has_not_selected_are_not_found_or_read(self):
        home_company, other_company = self.env["res.company"].create(
            [{"name": "Test Case Home Company"}, {"name": "Test Case Other Company"}],
        )
        self.user_dev.write(
            {
                "company_ids": [Command.link(home_company.id), Command.link(other_company.id)],
                "company_id": home_company.id,
            },
        )
        other_project = self.env["project.project"].create({"name": "Other Project", "company_id": other_company.id})
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": other_project.id})
        other_case = self.Testcase.create({"name": "Other case", "task_id": other_task.id})
        domain = [("task_id", "=", other_task.id)]
        home_only = self.as_developer.with_context(allowed_company_ids=home_company.ids)
        self.assertFalse(home_only.search(domain))
        with self.assertRaises(AccessError):
            home_only.browse(other_case.id).read(["name"])
        with self.assertRaises(AccessError):
            home_only.browse(other_case.id).write({"state": "waiting"})
        both_companies = self.as_developer.with_context(allowed_company_ids=(home_company | other_company).ids)
        self.assertEqual(both_companies.search(domain), other_case)
        both_companies.browse(other_case.id).write({"state": "waiting"})
        self.assertEqual(both_companies.browse(other_case.id).read(["state"])[0]["state"], "waiting")

    def test_a_case_of_a_task_the_user_cannot_read_is_not_written_or_deleted(self):
        hidden_case = self.hidden_case.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_case.write({"state": "passed"})
        with self.assertRaises(AccessError):
            hidden_case.unlink()

    def test_cases_cannot_be_added_to_a_task_the_user_cannot_read(self):
        with self.assertRaises(AccessError):
            self.as_developer.create({"name": "Sneaked case", "task_id": self.hidden_task.id})

    def test_a_case_moves_only_onto_a_task_the_user_reads(self):
        case = self.visible_case.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            case.write({"task_id": self.hidden_task.id})
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        case.write({"task_id": other_task.id})
        self.assertEqual(self.as_developer.search([("task_id", "=", other_task.id)]), case)

    def test_a_case_refused_a_task_moves_no_image_onto_it(self):
        vals = {"name": "Pasted steps", "steps_to_execute": image_html(image_tag(data_uri(RED)))}
        with patch.object(ImageMover, "move", autospec=True, side_effect=ImageMover.move) as move:
            with self.assertRaises(AccessError):
                self.as_developer.create({**vals, "task_id": self.hidden_task.id})
            self.assertFalse(move.called, "the create is refused before the images are moved")
            self.as_developer.create({**vals, "task_id": self.task.id})
        self.assertTrue(move.called, "on a task the user reads the image moves")

    def test_a_case_refused_the_task_it_is_moved_onto_copies_no_image_onto_it(self):
        steps = image_html(image_tag(data_uri(RED)))
        case = self.Testcase.create({"name": "Pasted steps", "task_id": self.task.id, "steps_to_execute": steps})
        case = case.with_user(self.user_dev)
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        with patch.object(ImageMover, "move", autospec=True, side_effect=ImageMover.move) as move:
            with self.assertRaises(AccessError):
                case.write({"task_id": self.hidden_task.id})
            self.assertFalse(move.called, "the move is refused before the images are copied")
            case.write({"task_id": other_task.id})
        image_copy = self.env["ir.attachment"].search(
            [("res_model", "=", "project.task"), ("res_id", "=", other_task.id)]
        )
        self.assertEqual(image_copy.datas, RED.encode())
        self.assertEqual(case.steps_to_execute, image_html(image_tag(get_image_source(image_copy))))

    def test_a_reader_of_the_task_archived_or_not_keeps_every_operation_on_its_cases(self):
        for is_active in (True, False):
            self.task.active = is_active
            with self.subTest(active=is_active):
                case = self.as_developer.create({"name": "Own case", "task_id": self.task.id})
                self.assertEqual(self.as_developer.search([("task_id", "=", self.task.id)]), self.visible_case | case)
                self.assertEqual(case.read(["name"]), [{"id": case.id, "name": "Own case"}])
                case.write({"state": "passed"})
                self.assertEqual(case.state, "passed")
                case.unlink()
                self.assertFalse(case.exists())


@tagged("post_install", "-at_install")
class TestTestCaseStateConversion(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # what the database holds besides the cases of a test is not this test's to count
        convert_test_case_states(cls.env.cr)

    def _store_cases(self, stored_states):
        """Cases as a database from before holds them: an old state, and a write date and writer
        that a write of the ORM in this transaction would change.
        """
        cases = self.env["project.task.testcase"].create(
            [{"name": "Old case %d" % index, "task_id": self.task.id} for index in range(len(stored_states))],
        )
        self.env.flush_all()
        for case, state in zip(cases, stored_states):
            self.env.cr.execute(
                "UPDATE project_task_testcase SET state = %s, write_date = %s, write_uid = %s WHERE id = %s",
                (state, EARLIER, self.user_dev.id, case.id),
            )
        cases.invalidate_recordset()
        return cases

    def _stored_rows(self, cases):
        self.env.flush_all()
        self.env.cr.execute(STORED_CASE_ROWS, [tuple(cases.ids)])
        return self.env.cr.fetchall()

    def _convert(self):
        """Return what the conversion returns and the line it logs."""
        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            counts = convert_test_case_states(self.env.cr)
        return counts, logs.records[0].getMessage()

    def test_every_case_gets_the_state_of_the_new_selection(self):
        cases = self._store_cases([state for state, _new_state in STORED_CASES])
        self.assertEqual(cases.mapped("state"), [state for state, _new_state in STORED_CASES])

        counts, logged_line = self._convert()

        self.assertEqual(counts, {"todo": 2, "done": 3, "cancelled": 2})
        self.assertEqual(
            logged_line,
            "Test case states converted: 2 todo to not_run, 3 done to passed, 2 cancelled to not_applicable",
        )
        self.assertEqual(cases.mapped("state"), [new_state for _state, new_state in STORED_CASES])
        expected_rows = [
            (case.id, new_state, EARLIER, self.user_dev.id, None, None, None)
            for case, (_state, new_state) in zip(cases, STORED_CASES)
        ]
        self.assertEqual(self._stored_rows(cases), expected_rows)

    def test_second_run_changes_nothing(self):
        cases = self._store_cases([state for state, _new_state in STORED_CASES])
        convert_test_case_states(self.env.cr)
        rows = self._stored_rows(cases)

        counts, logged_line = self._convert()

        self.assertEqual(counts, {"todo": 0, "done": 0, "cancelled": 0})
        self.assertEqual(
            logged_line,
            "Test case states converted: 0 todo to not_run, 0 done to passed, 0 cancelled to not_applicable",
        )
        self.assertEqual(self._stored_rows(cases), rows)

    def test_migration_script_converts_on_update_only(self):
        spec = importlib.util.spec_from_file_location("post_migration", MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        case = self._store_cases(["cancelled"])

        script.migrate(self.env.cr, None)
        self.assertEqual(case.state, "cancelled")

        script.migrate(self.env.cr, "16.0.2.4.1")
        self.assertEqual(case.state, "not_applicable")
