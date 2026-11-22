import json
from unittest.mock import patch

from odoo import Command
from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools.translate import code_translations

from odoo.addons.project_support_request.controllers.project_support_request import NO_SUPPORT_PROJECT_ERROR

CONFIG_URL = "/project_support_request/config"
CREATE_URL = "/project_support_request/create"
SYSTRAY_URL = "/project_support_request/systray"


@tagged("post_install", "-at_install")
class TestSupportRequestRoutes(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_open = cls.env["project.task.type"].create({"name": "Backlog", "sequence": 1})
        cls.project = cls.env["project.project"].create(
            {
                "name": "Project That Develops This Odoo",
                "is_ai_driven": True,
                "type_ids": [Command.set(cls.stage_open.ids)],
            }
        )
        cls.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        cls.project.develops_this_odoo = True
        cls.internal_user = new_test_user(cls.env, login="support_route_internal", groups="base.group_user")
        cls.portal_user = new_test_user(cls.env, login="support_route_portal", groups="base.group_portal")

    def _call(self, url, **params):
        payload = {"id": 0, "jsonrpc": "2.0", "method": "call", "params": params}
        response = self.url_open(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 200, url)
        return response.json()

    def _result(self, url, **params):
        body = self._call(url, **params)
        self.assertNotIn("error", body, url)
        return body["result"]

    def test_the_routes_answer_a_signed_in_user_only(self):
        self.authenticate(None, None)
        for url in (CONFIG_URL, CREATE_URL, SYSTRAY_URL):
            self.assertEqual(self._call(url).get("error", {}).get("code"), 100, "%s must ask for a session" % url)

    def test_an_internal_user_files_a_request_and_finds_it_in_the_systray(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)

        config = self._result(CONFIG_URL)
        self.assertEqual(config["project_id"], self.project.id)
        self.assertEqual(config["uid"], self.internal_user.id)
        self.assertIn("bug", [task_type["value"] for task_type in config["task_types"]])

        ticket = self._result(CREATE_URL, name="Filed over JSON-RPC", task_type="bug")
        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.uat_user_ids, self.internal_user)

        systray = self._result(SYSTRAY_URL, tab="bugs", limit=0)
        self.assertEqual(
            (systray["configured"], systray["bugs_count"], systray["others_count"], systray["total"]),
            (True, 1, 0, 1),
        )
        self.assertEqual([ticket["id"] for ticket in systray["tickets"]], [task.id])

    def test_a_filed_request_answers_with_the_key_of_its_task(self):
        self.project.with_context(test_project_key=True).key = "ZQSUP"
        self.authenticate(self.internal_user.login, self.internal_user.login)

        # Task keys are off under test_enable unless the request's context switches them on.
        ticket = self._result(CREATE_URL, name="Filed with a key", task_type="bug", context={"test_project_key": True})

        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertEqual((ticket["ticket_key"], task.key), ("ZQSUP-1", "ZQSUP-1"))

    def test_the_systray_lists_a_request_with_the_fields_the_widget_shows(self):
        self.project.with_context(test_project_key=True).key = "ZQSYS"
        self.authenticate(self.internal_user.login, self.internal_user.login)
        ticket = self._result(
            CREATE_URL, name="Listed with its fields", task_type="bug", context={"test_project_key": True}
        )
        task = self.env["project.task"].browse(ticket["ticket_id"])
        task.priority_level = "4"

        tickets = self._result(SYSTRAY_URL, tab="bugs", limit=0)["tickets"]

        self.assertEqual(
            tickets,
            [
                {
                    "id": task.id,
                    "key": "ZQSYS-1",
                    "name": "Listed with its fields",
                    "task_type": "bug",
                    "stage_name": self.stage_open.name,
                    "priority_level": "4",
                    "create_date": str(task.create_date),
                    "sequence": task.sequence,
                }
            ],
        )

    def test_the_task_types_are_labelled_in_the_users_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.env["ir.model.fields"]._get("project.task", "task_type").selection_ids.filtered(
            lambda selection: selection.value == "bug"
        ).update_field_translations("name", {"fr_FR": "Anomalie"})
        # Selection labels are cached per language, and this translation skips the write that clears them.
        self.env.registry.clear_caches()
        filer = new_test_user(self.env, login="support_route_french", groups="base.group_user", lang="fr_FR")
        self.authenticate(filer.login, filer.login)

        task_types = self._result(CONFIG_URL)["task_types"]

        self.assertIn({"value": "bug", "label": "Anomalie"}, task_types)

    def test_the_errors_of_a_request_come_in_the_users_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        filer = new_test_user(self.env, login="support_route_french", groups="base.group_user", lang="fr_FR")
        portal_user = new_test_user(
            self.env, login="support_route_french_portal", groups="base.group_portal", lang="fr_FR"
        )
        french_terms = {
            "Support request title is required.": "La demande d'assistance n'a pas de titre.",
            "Invalid task type: %s": "Type de tâche inconnu : %s",
            NO_SUPPORT_PROJECT_ERROR: "Aucun projet actif ne développe cet Odoo.",
            "Only internal users file support requests.": "Seuls les utilisateurs internes en déposent.",
        }

        with patch.dict(code_translations.python_translations, {("project_support_request", "fr_FR"): french_terms}):
            self.authenticate(filer.login, filer.login)
            errors = [
                self._result(CREATE_URL, name="  ")["error"],
                self._result(CREATE_URL, name="Filed with an unknown type", task_type="no_such_type")["error"],
            ]
            self.project.develops_this_odoo = False
            self.env.flush_all()
            errors.append(self._result(CREATE_URL, name="Filed without a support project")["error"])
            self.authenticate(portal_user.login, portal_user.login)
            errors.append(self._result(CREATE_URL, name="Filed by a portal user")["error"])

        self.assertEqual(
            errors,
            [
                "La demande d'assistance n'a pas de titre.",
                "Type de tâche inconnu : no_such_type",
                "Aucun projet actif ne développe cet Odoo.",
                "Seuls les utilisateurs internes en déposent.",
            ],
        )

    def test_a_request_is_filed_with_the_type_picked_and_refused_with_an_unknown_one(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)

        ticket = self._result(CREATE_URL, name="Filed as an improvement", task_type="improvement")
        refused = self._result(CREATE_URL, name="Filed with an unknown type", task_type="no_such_type")

        self.assertEqual(self.env["project.task"].browse(ticket["ticket_id"]).task_type, "improvement")
        self.assertIn("error", refused)
        self.assertFalse(self.env["project.task"].search([("name", "=", "Filed with an unknown type")]))

    def test_a_portal_user_gets_no_support_project_and_files_no_request(self):
        self.authenticate(self.portal_user.login, self.portal_user.login)

        self.assertIs(self._result(CONFIG_URL)["project_id"], False)
        self.assertEqual(
            self._result(SYSTRAY_URL),
            {"configured": False, "bugs_count": 0, "others_count": 0, "tickets": [], "total": 0},
        )
        self.assertEqual(
            self._result(CREATE_URL, name="Filed by a portal user over JSON-RPC"),
            {"error": "Only internal users file support requests."},
        )
        self.assertFalse(self.env["project.task"].search([("name", "=", "Filed by a portal user over JSON-RPC")]))

    def test_a_user_without_the_company_of_the_support_project_lists_their_request(self):
        own_company = self.env["res.company"].create({"name": "Company of the Filer"})
        filer = new_test_user(
            self.env,
            login="support_route_other_company",
            groups="base.group_user",
            company_id=own_company.id,
            company_ids=[Command.set(own_company.ids)],
        )
        self.authenticate(filer.login, filer.login)

        ticket = self._result(CREATE_URL, name="Filed from another company", task_type="bug")

        systray = self._result(SYSTRAY_URL, tab="bugs", limit=0)
        self.assertNotEqual(self.env["project.task"].browse(ticket["ticket_id"]).company_id, own_company)
        self.assertEqual(
            (systray["bugs_count"], [listed["id"] for listed in systray["tickets"]]), (1, [ticket["ticket_id"]])
        )
