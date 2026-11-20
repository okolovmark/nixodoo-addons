import re
from unittest.mock import patch

import odoo.tests
from odoo.tests.tag_selector import TagsSelector
from odoo.tools import config, file_open

from odoo.addons.web.tests import test_js

MODULE = "project_team_dashboard"


@odoo.tests.tagged("post_install", "-at_install")
class TestGanttLibrary(odoo.tests.HttpCase):
    def test_the_vis_timeline_script_the_gantt_loads_is_served(self):
        with file_open(f"{MODULE}/static/src/gantt/vis_timeline.js") as loader:
            lib_url = re.search(r'const LIB_URL = "([^"]+)"', loader.read())[1]
        response = self.url_open(lib_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)


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
        """web's suite is in a run that loads web's tests: one that installs or updates web or base,
        which updates every module, or one that installs and updates nothing. The registry's
        updated_modules cannot tell: the first signaling check after the update reloads the registry,
        which empties it.

        A Hoot parameter that selects tests turns the module filter of web's suite off: the suite
        then runs only the tests the parameters name; one that only excludes keeps it
        (`HootCommon._get_hoot_filters`).

        Outside CI's shape the guess can miss: `-i base` or `-i web` on a database that has them
        installs nothing and loads no web tests (Hoot runs none), and `-i <module>` on an existing
        empty database installs base without naming it (Hoot runs twice)."""
        requested_modules = {*config["init"], *config["update"], *config["reinit"]}
        if requested_modules and not requested_modules & {"base", "web"}:
            return False
        web_suite = self.web_suite_class(self.web_suite_method)
        return (
            TagsSelector(config["test_tags"], sorted(self.registry._init_modules)).check(web_suite)
            and MODULE in web_suite._test_modules
            and not any(sign == "+" for sign, _part in test_js._get_filters(web_suite._test_params))
        )

    def _run_hoot(self):
        if self._web_suite_runs_this_module():
            self.skipTest(f"{self.web_suite_class.__name__} runs the Hoot tests of {MODULE} in this run")
        self.browser_js(
            f"{self.url}&id={self._generate_hash(f'@{MODULE}')}",
            "",
            "",
            login="admin",
            timeout=1800,
            success_signal="[HOOT] Test suite succeeded",
            error_checker=test_js.unit_test_error_checker,
        )


@odoo.tests.tagged("hoot", "post_install", "-at_install")
class TestHootDesktop(HootModuleCase):
    web_suite_class = test_js.WebSuite
    web_suite_method = "test_unit_desktop"
    url = "/web/tests?headless&loglevel=2&preset=desktop&timeout=15000"

    @odoo.tests.no_retry
    def test_hoot_desktop(self):
        self._run_hoot()


@odoo.tests.tagged("hoot", "post_install", "-at_install")
class TestHootMobile(HootModuleCase):
    browser_size = "375x667"
    touch_enabled = True
    web_suite_class = test_js.MobileWebSuite
    web_suite_method = "test_unit_mobile"
    url = "/web/tests?headless&loglevel=2&preset=mobile&tag=-headless&timeout=15000"

    @odoo.tests.no_retry
    def test_hoot_mobile(self):
        self._run_hoot()


@odoo.tests.tagged("post_install", "-at_install")
class TestHootRunners(odoo.tests.TransactionCase):
    RUNNERS = ((TestHootDesktop, "test_hoot_desktop"), (TestHootMobile, "test_hoot_mobile"))

    def test_the_runners_carry_the_tags_of_web_suites(self):
        for runner_class, _test_method in self.RUNNERS:
            with self.subTest(runner=runner_class.__name__):
                self.assertIn("hoot", runner_class.test_tags)
                self.assertGreaterEqual(runner_class.test_tags, runner_class.web_suite_class.test_tags)

    def test_a_runner_leaves_the_module_to_web_suite_only_when_that_suite_runs_it(self):
        modules = ["web", MODULE]
        for runner_class, test_method in self.RUNNERS:
            runner = runner_class(test_method)
            runner.registry = self.registry
            for test_tags, is_left_to_web in (
                (f"/web,/{MODULE}", True),
                (f"/web,/{MODULE},/web[@web/core]", False),
                (f"/web,/{MODULE},-/web[@web/core]", True),
            ):
                with (
                    self.subTest(runner=runner_class.__name__, test_tags=test_tags),
                    patch.dict(config.options, {"test_tags": test_tags, "update": dict.fromkeys(modules, True)}),
                ):
                    self.assertEqual(runner._web_suite_runs_this_module(), is_left_to_web)

    def test_a_runner_reads_from_the_modules_a_run_installs_or_updates_whether_web_suites_run(self):
        """The tests of an update run read a registry reloaded after the update, whose updated modules are none."""
        for runner_class, test_method in self.RUNNERS:
            runner = runner_class(test_method)
            runner.registry = self.registry
            for case, init, update, test_tags, is_left_to_web in (
                ("database created and installed in one run", [MODULE, "base"], [], f"/{MODULE}", True),
                ("same, CI without tags", [MODULE, "base"], [], "+standard", True),
                ("CI after the dependencies", [MODULE], [], "+standard", False),
                ("install after the dependencies", [MODULE], [], f"/{MODULE}", False),
                ("update of the module", [], [MODULE], f"/{MODULE}", False),
                ("update of all modules", [], ["base"], f"/{MODULE}", True),
                ("tests of an installed database", [], [], f"/{MODULE}", True),
            ):
                with (
                    self.subTest(runner=runner_class.__name__, case=case),
                    patch.dict(
                        config.options,
                        {
                            "test_tags": test_tags,
                            "init": dict.fromkeys(init, True),
                            "update": dict.fromkeys(update, True),
                            "reinit": [],
                        },
                    ),
                    patch.object(self.registry, "updated_modules", []),
                ):
                    self.assertEqual(runner._web_suite_runs_this_module(), is_left_to_web)

    def test_the_suite_check_reads_the_module_of_test_files_only(self):
        module_test_file = {"url": f"/{MODULE}/static/tests/gantt.test.js", "filename": "gantt.test.js"}
        assets = [
            module_test_file,
            {"url": f"/{MODULE}/static/src/gantt/gantt_view.js", "filename": "gantt_view.js"},
            {"url": "/web/static/tests/core.test.js", "filename": "core.test.js"},
            {"url": "lib.min.js", "filename": "lib.min.js"},
        ]
        with patch.object(test_js.HootCommon, "_get_bundle_assets", return_value=assets):
            self.assertEqual(
                TestHootSuite("test_check_suite")._get_bundle_assets("web.assets_unit_tests"), [module_test_file]
            )
