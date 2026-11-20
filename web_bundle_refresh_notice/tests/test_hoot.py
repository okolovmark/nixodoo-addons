import logging
import re
from contextlib import ExitStack
from unittest.mock import patch

import odoo.tests
from odoo.tests.tag_selector import TagsSelector
from odoo.tools import config

from odoo.addons.web.tests import test_js

MODULE = "web_bundle_refresh_notice"


class HootSummary(logging.Handler):
    """The count of passed tests in the summary Hoot logs at the end of a run, which reports a filter
    that selects no test as a success."""

    passed_count = 0

    def emit(self, record):
        if match := re.match(r"\[HOOT\] Passed (\d+) tests", record.getMessage()):
            self.passed_count = int(match[1])


@odoo.tests.tagged("post_install", "-at_install")
class TestHootSuite(test_js.HootCommon):
    """web's own check of the Hoot files runs in a run of web's tests only: this one runs core's on
    this module's files."""

    def _get_bundle_assets(self, bundle):
        return [
            asset
            for asset in super()._get_bundle_assets(bundle)
            if asset["filename"].endswith(".test.js") and test_js.RE_ASSET_ADDON.match(asset["url"])[1] == MODULE
        ]

    def test_check_suite(self):
        self.assertTrue(
            self._get_bundle_assets("web.assets_unit_tests"), f"the unit test bundle has the Hoot files of {MODULE}"
        )
        self._check_forbidden_statements("web.assets_unit_tests")


class HootModuleCase(test_js.HootCommon):
    """Runs this module's Hoot tests in a run that installs or updates it, as CI's: web's own suites
    only run in a run that loads web's tests too, and there they run these ones as well."""

    web_suite_class = None
    web_suite_method = ""
    url = ""

    def _web_suite_runs_this_module(self):
        """A Hoot parameter that selects tests turns the module filter of web's suite off: the suite
        then runs only the tests the parameters name; one that only excludes keeps it
        (`HootCommon._get_hoot_filters`)."""
        updating = config["init"] or config["update"] or config["reinit"]
        modules = self.registry.updated_modules if updating else self.registry._init_modules
        if "web" not in modules:
            return False
        web_suite = self.web_suite_class(self.web_suite_method)
        return (
            TagsSelector(config["test_tags"], sorted(modules)).check(web_suite)
            and MODULE in web_suite._test_modules
            and not any(sign == "+" for sign, _part in test_js._get_filters(web_suite._test_params))
        )

    def _run_hoot(self):
        if self._web_suite_runs_this_module():
            self.skipTest(f"{self.web_suite_class.__name__} runs the Hoot tests of {MODULE} in this run")
        summary = HootSummary()
        browser_logger = self._logger.getChild("browser")
        browser_logger.addHandler(summary)
        self.addCleanup(browser_logger.removeHandler, summary)
        if not browser_logger.isEnabledFor(logging.RUNBOT):
            self.addCleanup(browser_logger.setLevel, browser_logger.level)
            browser_logger.setLevel(logging.RUNBOT)
        self.browser_js(
            f"{self.url}&id={self._generate_hash(f'@{MODULE}')}",
            "",
            "",
            login="admin",
            timeout=1800,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=test_js.unit_test_error_checker,
        )
        self.assertGreater(summary.passed_count, 0, f"Hoot finds no test of {MODULE} under its filter")


@odoo.tests.tagged("hoot", "post_install", "-at_install")
class TestHootDesktop(HootModuleCase):
    web_suite_class = test_js.WebSuite
    web_suite_method = "test_unit_desktop"
    url = "/web/tests?headless&loglevel=2&preset=desktop&timeout=15000"

    @odoo.tests.no_retry
    def test_hoot_desktop(self):
        self._run_hoot()


@odoo.tests.tagged("post_install", "-at_install")
class TestHootModuleCase(odoo.tests.TransactionCase):
    def test_a_run_gives_the_browser_logger_its_level_back(self):
        hoot_case = TestHootDesktop("test_hoot_desktop")
        hoot_case._logger = logging.getLogger(f"{__name__}.{self._testMethodName}")
        browser_logger = hoot_case._logger.getChild("browser")
        browser_logger.setLevel(logging.WARNING)
        browser_logger.propagate = False

        def browser_js(*args, **kwargs):
            browser_logger.log(logging.RUNBOT, "[HOOT] Passed 1 tests")

        with (
            ExitStack() as hoot_case_cleanups,
            patch.object(hoot_case, "addCleanup", hoot_case_cleanups.callback),
            patch.object(hoot_case, "_web_suite_runs_this_module", return_value=False),
            patch.object(hoot_case, "browser_js", browser_js),
        ):
            hoot_case._run_hoot()

        self.assertEqual(browser_logger.level, logging.WARNING)
