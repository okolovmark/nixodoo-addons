from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.translate import PYTHON_TRANSLATION_COMMENT, TranslationModuleReader

from odoo.addons.project_support_request.controllers import project_support_request as controller


@tagged("post_install", "-at_install")
class TestSupportRequest(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Stage = cls.env["project.task.type"]
        cls.stage_backlog = Stage.create({"name": "Backlog", "sequence": 1})
        cls.stage_in_progress = Stage.create({"name": "In Progress", "sequence": 2})
        cls.stage_done = Stage.create({"name": "Done", "fold": True, "sequence": 3})
        cls.project = cls.env["project.project"].create(
            {
                "name": "Test AI-driven Project",
                "is_ai_driven": True,
                "type_ids": [Command.set((cls.stage_backlog + cls.stage_in_progress + cls.stage_done).ids)],
            }
        )
        cls.project_plain = cls.env["project.project"].create({"name": "Plain Project"})
        # The database may hold its own project that develops it, and one project at most does.
        cls.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        cls.project.develops_this_odoo = True

        project_user_groups = [
            Command.set([cls.env.ref("base.group_user").id, cls.env.ref("project.group_project_user").id])
        ]
        cls.user_dev1, cls.user_dev2 = cls.env["res.users"].create(
            [
                {
                    "name": "Dev One",
                    "login": "support_request_dev1",
                    "email": "dev1@example.com",
                    "group_ids": project_user_groups,
                },
                {
                    "name": "Dev Two",
                    "login": "support_request_dev2",
                    "email": "dev2@example.com",
                    "group_ids": project_user_groups,
                },
            ]
        )

        cls.bug_task1, cls.bug_task2, cls.feature_task, cls.bug_done, cls.bug_dev2 = cls.env["project.task"].create(
            [
                cls._request_vals("Bug: Login fails", cls.stage_in_progress, "bug", cls.user_dev1),
                cls._request_vals("Bug: Export broken", cls.stage_backlog, "bug", cls.user_dev1),
                cls._request_vals("Feature: Dark mode", cls.stage_in_progress, "feature", cls.user_dev1),
                cls._request_vals("Bug: Already fixed", cls.stage_done, "bug", cls.user_dev1),
                cls._request_vals("Bug: Dev2 only", cls.stage_in_progress, "bug", cls.user_dev2),
            ]
        )

    @classmethod
    def _request_vals(cls, name, stage, task_type, uat_user):
        return {
            "name": name,
            "project_id": cls.project.id,
            "stage_id": stage.id,
            "task_type": task_type,
            "uat_user_ids": [Command.set(uat_user.ids)],
            "user_ids": [Command.clear()],
        }

    def _call_route(self, user, route_name, context=None, **kwargs):
        user_request = SimpleNamespace(env=self.env(user=user, context=dict(self.env.context, **(context or {}))))
        with patch.object(controller, "request", user_request):
            return getattr(controller.SupportRequestController(), route_name)(**kwargs)

    def _filer_without_project_groups(self):
        return self.env["res.users"].create(
            {
                "name": "Support Widget Filer",
                "login": "support_widget_filer",
                "group_ids": [Command.set([self.env.ref("base.group_user").id])],
            }
        )

    def _portal_user(self):
        return self.env["res.users"].create(
            {
                "name": "Support Portal User",
                "login": "support_portal_user",
                "group_ids": [Command.set([self.env.ref("base.group_portal").id])],
            }
        )

    def test_get_systray_requests_bugs_tab_for_dev1(self):
        result = self._call_route(self.user_dev1, "get_systray_requests", tab="bugs", limit=0)
        self.assertTrue(result["configured"])
        self.assertEqual(result["bugs_count"], 2)
        self.assertEqual(result["others_count"], 1)
        self.assertEqual(result["total"], 2)
        self.assertCountEqual(
            [ticket["id"] for ticket in result["tickets"]],
            [self.bug_task1.id, self.bug_task2.id],
        )

    def test_get_systray_requests_others_tab_gives_the_fields_of_each_request(self):
        result = self._call_route(self.user_dev1, "get_systray_requests", tab="others", limit=0)

        self.assertEqual((result["bugs_count"], result["others_count"], result["total"]), (2, 1, 1))
        self.assertEqual(len(result["tickets"]), 1)
        self.assertEqual(
            {name: result["tickets"][0][name] for name in ("id", "name", "task_type", "stage_name", "priority_level")},
            {
                "id": self.feature_task.id,
                "name": "Feature: Dark mode",
                "task_type": "feature",
                "stage_name": self.stage_in_progress.name,
                "priority_level": self.feature_task.priority_level,
            },
        )

    def test_get_systray_requests_lists_the_requests_of_the_signed_in_user(self):
        result = self._call_route(self.user_dev2, "get_systray_requests", tab="bugs", limit=0)

        self.assertEqual((result["bugs_count"], result["others_count"], result["total"]), (1, 0, 1))
        self.assertEqual([ticket["id"] for ticket in result["tickets"]], self.bug_dev2.ids)

    def test_get_systray_requests_limits_the_list_and_not_the_counts(self):
        result = self._call_route(self.user_dev1, "get_systray_requests", tab="bugs", limit=1)

        self.assertEqual(len(result["tickets"]), 1)
        self.assertIn(result["tickets"][0]["id"], (self.bug_task1 | self.bug_task2).ids)
        self.assertEqual((result["bugs_count"], result["total"]), (2, 2))

    def test_create_request_files_the_title_type_and_description_in_the_first_open_stage(self):
        ticket = self._call_route(
            self.user_dev1, "create_request", name="  Dark mode  ", task_type="feature", description="<p>Dim it</p>"
        )

        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertRecordValues(
            task,
            [
                {
                    "name": "Dark mode",
                    "task_type": "feature",
                    "project_id": self.project.id,
                    "stage_id": self.stage_backlog.id,
                    "uat_user_ids": self.user_dev1.ids,
                    "user_ids": [],
                },
            ],
        )
        self.assertIn("Dim it", task.description)

    def test_the_config_offers_the_task_types_create_request_accepts(self):
        task_types = self._call_route(self.user_dev1, "get_config")["task_types"]

        self.assertIn({"value": "bug", "label": "Bug"}, task_types)
        self.assertIn({"value": "feature", "label": "Feature"}, task_types)
        self.assertEqual(
            self._call_route(self.user_dev1, "create_request", name="Typeless", task_type="no_such_type"),
            {"error": "Invalid task type: no_such_type"},
        )
        self.assertEqual(
            self._call_route(self.user_dev1, "create_request", name="  "),
            {"error": "Support request title is required."},
        )

    def test_the_config_names_each_priority_level(self):
        self.assertEqual(
            self._call_route(self.user_dev1, "get_config")["priority_levels"],
            {"4": "Highest", "3": "High", "2": "Medium", "1": "Low", "0": "Lowest"},
        )

    def test_the_config_names_the_priority_levels_in_the_language_of_the_user(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        priority_level_field = self.env["ir.model.fields"]._get("project.task", "priority_level")
        priority_level_field.selection_ids.filtered(lambda option: option.value == "4").with_context(
            lang="fr_FR"
        ).name = "La plus haute"

        priority_levels = self._call_route(self.user_dev1, "get_config", context={"lang": "fr_FR"})["priority_levels"]

        self.assertEqual(priority_levels["4"], "La plus haute")

    def test_a_new_request_and_the_systray_carry_the_task_key(self):
        keys_on = {"test_project_key": True}
        self.project.with_context(**keys_on).key = "ZQS"

        ticket = self._call_route(self.user_dev1, "create_request", context=keys_on, name="Keyed request")
        result = self._call_route(self.user_dev1, "get_systray_requests", context=keys_on, tab="bugs", limit=0)

        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertEqual(ticket["ticket_key"], "ZQS-1")
        tickets = {ticket["id"]: ticket for ticket in result["tickets"]}
        self.assertEqual(tickets[task.id]["key"], "ZQS-1")
        self.assertEqual(tickets[self.bug_task1.id]["key"], "", "a task without a key gives an empty one")
        self.assertEqual(tickets[task.id]["create_date"], fields.Datetime.to_string(task.create_date))
        self.assertEqual(tickets[task.id]["stage_name"], self.stage_backlog.name)

    def test_create_request_by_a_user_without_project_rights_files_an_unassigned_request(self):
        filer = self._filer_without_project_groups()

        ticket = self._call_route(filer, "create_request", name="Filed without project rights", task_type="bug")

        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.stage_id, self.stage_backlog)
        self.assertEqual(task.uat_user_ids, filer)
        self.assertFalse(task.user_ids, "the filer must not be assigned to their own request")
        systray = self._call_route(filer, "get_systray_requests", tab="bugs", limit=0)
        self.assertEqual(
            [ticket["id"] for ticket in systray["tickets"]], task.ids, "the filer reads back the request filed for them"
        )

    def test_a_filer_lists_their_requests_in_the_company_of_the_project_without_selecting_it(self):
        current_company = self.env.ref("base.main_company")
        support_company = self.env["res.company"].create({"name": "Support Company"})
        self.project.develops_this_odoo = False
        support_project = self.env["project.project"].create(
            {"name": "Support Company Project", "is_ai_driven": True, "company_id": support_company.id}
        )
        support_project.develops_this_odoo = True
        filer_in_both, filer_outside = self.env["res.users"].create(
            [
                {
                    "name": name,
                    "login": login,
                    "group_ids": [Command.set([self.env.ref("base.group_user").id])],
                    "company_id": current_company.id,
                    "company_ids": [Command.set(companies.ids)],
                }
                for name, login, companies in (
                    ("Filer In Both", "support_filer_in_both", current_company | support_company),
                    ("Filer Outside", "support_filer_outside", current_company),
                )
            ]
        )

        current_only = {"allowed_company_ids": current_company.ids}

        for filer in (filer_in_both, filer_outside):
            with self.subTest(filer=filer.name):
                ticket = self._call_route(filer, "create_request", context=current_only, name=f"Filed by {filer.name}")
                systray = self._call_route(filer, "get_systray_requests", context=current_only, tab="bugs", limit=0)

                request = self.env["project.task"].browse(ticket["ticket_id"])
                self.assertEqual(request.company_id, support_company)
                self.assertEqual([ticket_data["id"] for ticket_data in systray["tickets"]], request.ids)
                self.assertTrue(request.with_user(filer).with_context(current_only).has_access("read"))

    def test_a_portal_user_gets_no_support_project_and_files_no_request(self):
        portal_user = self._portal_user()

        self.assertIs(self._call_route(portal_user, "get_config")["project_id"], False)
        self.assertEqual(
            self._call_route(portal_user, "get_systray_requests"),
            {"configured": False, "bugs_count": 0, "others_count": 0, "tickets": [], "total": 0},
        )
        self.assertEqual(
            self._call_route(portal_user, "create_request", name="Filed by a portal user"),
            {"error": "Only internal users file support requests."},
        )
        self.assertFalse(self.env["project.task"].search([("name", "=", "Filed by a portal user")]))

    def test_requests_go_to_the_project_that_develops_this_odoo(self):
        self.assertEqual(self._call_route(self.user_dev1, "get_config")["project_id"], self.project.id)
        self.assertTrue(self._call_route(self.user_dev1, "get_systray_requests")["configured"])
        ticket = self._call_route(self.user_dev1, "create_request", name="Filed to the flagged project")
        self.assertEqual(self.env["project.task"].browse(ticket["ticket_id"]).project_id, self.project)

    def test_a_user_who_cannot_read_the_project_that_develops_this_odoo_files_requests_to_it(self):
        filer = self._filer_without_project_groups()
        self.project.privacy_visibility = "followers"
        self.assertFalse(self.env["project.project"].with_user(filer).search([("id", "=", self.project.id)]))

        self.assertEqual(self._call_route(filer, "get_config")["project_id"], self.project.id)

    def test_no_project_that_develops_this_odoo_is_not_configured(self):
        self.project.develops_this_odoo = False

        self._assert_not_configured()

    def test_an_archived_project_that_develops_this_odoo_is_not_configured(self):
        self.project.active = False

        self._assert_not_configured()

    def test_the_errors_of_a_request_are_exported_for_translation(self):
        server_terms = {
            "Only internal users file support requests.",
            "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo"
            " on the AI-driven project that develops it, or restores the archived one that carries it.",
            "Support request title is required.",
            "Invalid task type: %s",
        }

        exported_terms = {
            source
            for _module, _type, _name, _res_id, source, _value, comments in TranslationModuleReader(
                self.env.cr, modules=["project_support_request"]
            )
            if PYTHON_TRANSLATION_COMMENT in comments
        }

        self.assertEqual(server_terms - exported_terms, set(), "the errors the translation export misses")

    def _assert_not_configured(self):
        self.assertEqual(self._call_route(self.user_dev1, "_get_support_project_id"), 0)
        self.assertIs(self._call_route(self.user_dev1, "get_config")["project_id"], False)
        self.assertEqual(
            self._call_route(self.user_dev1, "get_systray_requests"),
            {"configured": False, "bugs_count": 0, "others_count": 0, "tickets": [], "total": 0},
        )
        self.assertIn(
            "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo"
            " on the AI-driven project that develops it, or restores the archived one that carries it.",
            self._call_route(self.user_dev1, "create_request", name="X")["error"],
        )
