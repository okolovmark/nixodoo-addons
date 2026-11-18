from odoo import Command
from odoo.tests.common import HttpCase, JsonRpcException, new_test_user, tagged

from odoo.addons.project_support_request.controllers.project_support_request import INTERNAL_USERS_ONLY_ERROR

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

    def test_the_routes_answer_a_signed_in_user_only(self):
        self.authenticate(None, None)
        for url in (CONFIG_URL, CREATE_URL, SYSTRAY_URL):
            with self.assertRaises(JsonRpcException) as caught:
                self.make_jsonrpc_request(url)
            self.assertEqual(caught.exception.code, 100, "%s must ask for a session" % url)

    def test_an_internal_user_files_a_request_and_finds_it_in_the_systray(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)

        config = self.make_jsonrpc_request(CONFIG_URL)
        self.assertEqual(config["project_id"], self.project.id)
        self.assertEqual(config["uid"], self.internal_user.id)
        self.assertIn("bug", [task_type["value"] for task_type in config["task_types"]])

        ticket = self.make_jsonrpc_request(CREATE_URL, {"name": "Filed over JSON-RPC", "task_type": "bug"})
        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.uat_user_ids, self.internal_user)

        systray = self.make_jsonrpc_request(SYSTRAY_URL, {"tab": "bugs", "limit": 0})
        self.assertEqual(
            (systray["configured"], systray["bugs_count"], systray["others_count"], systray["total"]),
            (True, 1, 0, 1),
        )
        self.assertEqual([ticket["id"] for ticket in systray["tickets"]], [task.id])

    def test_a_portal_user_gets_no_support_project_and_files_no_request(self):
        self.authenticate(self.portal_user.login, self.portal_user.login)

        self.assertIs(self.make_jsonrpc_request(CONFIG_URL)["project_id"], False)
        self.assertEqual(
            self.make_jsonrpc_request(SYSTRAY_URL),
            {"configured": False, "bugs_count": 0, "others_count": 0, "tickets": [], "total": 0},
        )
        self.assertEqual(
            self.make_jsonrpc_request(CREATE_URL, {"name": "Filed by a portal user over JSON-RPC"}),
            {"error": INTERNAL_USERS_ONLY_ERROR},
        )
        self.assertFalse(self.env["project.task"].search([("name", "=", "Filed by a portal user over JSON-RPC")]))
