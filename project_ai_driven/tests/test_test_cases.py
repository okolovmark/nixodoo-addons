import copy
from ast import literal_eval
from datetime import datetime
from unittest.mock import patch

from freezegun import freeze_time
from lxml import etree

from odoo.exceptions import AccessError
from odoo.tests import Form

from .common import MODULE, ProjectAiDrivenCommon

# What a client from before the change sends, and the state it stands for.
OLD_STATE_VALUES = (("todo", "not_run"), ("done", "passed"), ("cancelled", "not_applicable"))
BUTTON_STATES = {
    "action_set_passed": "passed",
    "action_set_failed": "failed",
    "action_set_not_run": "not_run",
    "action_set_waiting": "waiting",
    "action_set_not_applicable": "not_applicable",
}
BUTTON_INVISIBLE = {name: "state == '%s'" % state for name, state in BUTTON_STATES.items()}
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

    def _stored_flag(self, case):
        self.env.flush_all()
        self.env.cr.execute("SELECT passed FROM project_task_testcase WHERE id = %s", [case.id])
        return self.env.cr.fetchone()[0]

    def _task_form_arch(self):
        return self.env.ref("project.view_task_form2")._get_combined_arch()

    def _assert_material_icons(self, icon_names):
        """Odoo 20 draws a button icon from the Material Symbols font: a Font Awesome name shows as text."""
        self.assertTrue(icon_names)
        self.assertFalse([name for name in icon_names if not name or name.startswith("fa")], icon_names)

    def test_passed_follows_the_state(self):
        case = self._new_case()
        self.assertRecordValues(case, [{"state": "not_run", "passed": False}])
        for state in ("passed", "failed", "passed", "waiting", "passed", "not_applicable", "passed", "not_run"):
            case.write({"state": state})
            self.assertEqual(case.passed, state == "passed", state)
            self.assertEqual(self._stored_flag(case), state == "passed", state)
        self.assertTrue(self.Testcase._fields["passed"].readonly)

    def test_a_write_does_not_set_passed_apart_from_the_state(self):
        failed_case = self._new_case(state="failed")
        passed_case = self._new_case(state="passed")
        failed_case.write({"passed": True, "state": "failed"})
        passed_case.write({"passed": False})
        self.assertRecordValues(
            failed_case | passed_case,
            [{"state": "failed", "passed": False}, {"state": "passed", "passed": True}],
        )
        self.assertFalse(self._stored_flag(failed_case))
        self.assertTrue(self._stored_flag(passed_case))

    def test_old_state_values_are_translated_at_create_and_write(self):
        for old_state, state in OLD_STATE_VALUES:
            created_case = self._new_case(state=old_state)
            written_case = self._new_case(state="failed")
            written_case.write({"state": old_state})
            self.assertRecordValues(created_case | written_case, [{"state": state}, {"state": state}])

    def test_the_passed_flag_of_an_old_client_stands_for_a_pass(self):
        created_cases = self._new_case(passed=True, state="done") | self._new_case(passed=True)
        written_case = self._new_case()
        written_case.write({"passed": True, "state": "done"})
        flag_only_case = self._new_case(state="failed")
        flag_only_case.write({"passed": True})
        self.assertRecordValues(
            created_cases | written_case | flag_only_case,
            [{"state": "passed", "passed": True}] * 4,
        )

    def test_passed_false_alone_changes_nothing(self):
        cases = self._new_case(state="passed", verification_kind="automated") | self._new_case(state="failed")
        results = self._results(cases)
        cases.with_user(self.user_dev).write({"passed": False})
        self.assertEqual(self._results(cases), results)
        self.assertEqual(cases.mapped("passed"), [True, False])

    def test_a_batch_create_translates_each_case_and_leaves_the_values_as_sent(self):
        vals_list = [
            {"name": name, "task_id": self.task.id, **vals}
            for name, vals in (
                ("Old pass", {"passed": True, "state": "done"}),
                ("Old flag", {"passed": True}),
                ("Old cancel", {"state": "cancelled"}),
                ("Old open", {"state": "todo"}),
                ("New fail", {"state": "failed"}),
                ("Untouched", {}),
            )
        ]
        sent_vals_list = copy.deepcopy(vals_list)
        cases = self.Testcase.create(vals_list)
        self.assertEqual(vals_list, sent_vals_list)
        self.assertEqual(
            cases.mapped("state"),
            ["passed", "passed", "not_applicable", "not_run", "failed", "not_run"],
        )

    def test_a_write_leaves_the_values_as_sent(self):
        case = self._new_case(state="failed")
        vals = {"passed": True, "state": "todo"}
        case.write(vals)
        self.assertEqual(vals, {"passed": True, "state": "todo"})
        self.assertEqual(case.state, "not_run")

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

    def test_an_old_value_stamps_and_takes_back_as_the_state_it_stands_for(self):
        done_case = self._new_case()
        flag_case = self._new_case()
        with freeze_time(EARLIER):
            done_case.with_user(self.user_dev).write({"state": "done"})
            flag_case.with_user(self.user_dev).write({"passed": True})
        self.assertRecordValues(
            done_case | flag_case,
            [{"state": "passed", "verified_by_id": self.user_dev.id, "verified_on": EARLIER}] * 2,
        )

        todo_case = self._new_case(state="failed", verification_kind="automated", test_reference=TEST_REFERENCE)
        self.assertTrue(todo_case.verified_by_id)
        todo_case.with_user(self.user_dev).write({"state": "todo"})
        self.assertEqual(self._results(todo_case), [("Boot test", "not_run", False, False, False, TEST_REFERENCE)])

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

    def test_copying_tasks_copies_the_result_of_each_ones_cases(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        with freeze_time(EARLIER):
            self.Testcase.with_user(self.user_dev).create(
                [
                    {
                        "name": "Passed case",
                        "task_id": self.task.id,
                        "state": "passed",
                        "verification_kind": "automated",
                        "test_reference": TEST_REFERENCE,
                    },
                    {"name": "Failed case", "task_id": other_task.id, "state": "failed"},
                ],
            )
        self._new_case(name="Unstamped pass", state="passed", verified_by_id=False, verified_on=False)
        self._new_case(name="Open case", test_reference=TEST_REFERENCE)
        results = [
            ("Open case", "not_run", False, False, False, TEST_REFERENCE),
            ("Passed case", "passed", self.user_dev.id, EARLIER, "automated", TEST_REFERENCE),
            ("Unstamped pass", "passed", False, False, False, False),
        ]
        other_results = [("Failed case", "failed", self.user_dev.id, EARLIER, False, False)]
        self.assertEqual(self._results(self.task.testcase_ids), results)
        self.assertEqual(self._results(other_task.testcase_ids), other_results)

        with freeze_time(LATER):
            task_copy, other_copy = (self.task | other_task).copy()

        self.assertEqual(self._results(task_copy.testcase_ids), results)
        self.assertEqual(self._results(other_copy.testcase_ids), other_results)
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
        cases_field = self._task_form_arch().xpath("//field[@name='testcase_ids']")[0]
        list_buttons = cases_field.xpath("./list/button")
        self.assertEqual({button.get("name"): button.get("invisible") for button in list_buttons}, BUTTON_INVISIBLE)
        self.assertTrue(all(button.get("title") for button in list_buttons))
        self._assert_material_icons([button.get("icon") for button in list_buttons])
        self.assertNotIn("passed", cases_field.xpath("./list/field/@name"))
        card_buttons = cases_field.xpath("./kanban//button")
        self.assertEqual({button.get("name"): button.get("invisible") for button in card_buttons}, BUTTON_INVISIBLE)
        self._assert_material_icons(cases_field.xpath("./kanban//button/i/@data-icon"))

    def test_a_card_of_the_task_form_shows_the_verification_only_when_complete(self):
        (card_template,) = self._task_form_arch().xpath("//field[@name='testcase_ids']/kanban//t[@t-name='card']")
        (verification_line,) = card_template.xpath(".//div[field[@name='verified_on']]")
        self.assertEqual(verification_line.get("t-if"), COMPLETE_VERIFICATION)
        (state_badge,) = card_template.xpath(".//field[@name='state']")
        self.assertEqual({key: state_badge.get(key) for key in STATE_DECORATIONS}, STATE_DECORATIONS)

    def test_the_case_form_gives_the_other_results_from_its_header(self):
        form_arch = self.env.ref("%s.project_task_testcase_view_form" % MODULE)._get_combined_arch()
        header_buttons = form_arch.xpath("/form/header/button")
        self.assertEqual({button.get("name"): button.get("invisible") for button in header_buttons}, BUTTON_INVISIBLE)
        self.assertTrue(all(button.get("type") == "object" and button.get("string") for button in header_buttons))
        self._assert_material_icons([button.get("icon") for button in header_buttons])
        (state_node,) = form_arch.xpath("//field[@name='state']")
        self.assertEqual(state_node.getparent().tag, "header")
        self.assertEqual(state_node.get("widget"), "statusbar")
        self.assertEqual(state_node.get("readonly"), "1")
        self.assertNotIn("clickable", state_node.get("options", ""))
        self.assertEqual(
            state_node.get("statusbar_visible"),
            "not_run,passed,failed",
            "waiting and not applicable show only on the case that has them",
        )

    def test_the_menu_offers_cards_for_a_phone(self):
        action = self.env.ref("%s.action_view_testcase" % MODULE)
        self.assertEqual(action.view_mode.split(","), ["list", "kanban", "form"])
        kanban_arch = etree.fromstring(self.Testcase.get_view(view_type="kanban")["arch"])
        (card_template,) = kanban_arch.xpath("//t[@t-name='card']")
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

    def test_the_task_form_adds_a_case_not_run_and_leaves_the_stamp_to_the_rule(self):
        given_values = {
            "state": "passed",
            "verified_by_id": self.user_lead,
            "verified_on": EARLIER,
            "passed": True,
        }
        with Form(self.task.with_user(self.user_dev)) as task_form:
            with task_form.testcase_ids.new() as case_form:
                case_form.name = "Login works"
                case_form.test_reference = TEST_REFERENCE
                for field_name, value in given_values.items():
                    with self.assertRaisesRegex(AssertionError, "readonly field '%s'$" % field_name):
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
        group_bys = [literal_eval(context)["group_by"] for context in search_arch.xpath("//group/filter/@context")]
        self.assertLessEqual({"state", "verification_kind"}, set(group_bys))
