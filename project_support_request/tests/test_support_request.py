from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
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
                    "groups_id": project_user_groups,
                },
                {
                    "name": "Dev Two",
                    "login": "support_request_dev2",
                    "email": "dev2@example.com",
                    "groups_id": project_user_groups,
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

    def _base_domain(self, project_id, uid):
        return [
            ("project_id", "=", project_id),
            ("stage_id.fold", "=", False),
            ("uat_user_ids", "in", [uid]),
        ]

    def _first_open_stage(self):
        return self.env["project.task.type"].search(
            [("project_ids", "in", [self.project.id]), ("fold", "=", False)],
            order="sequence",
            limit=1,
        )

    def _call_route(self, user, route_name, **kwargs):
        user_request = SimpleNamespace(env=self.env(user=user))
        with patch.object(controller, "request", user_request):
            return getattr(controller.SupportRequestController(), route_name)(**kwargs)

    def _filer_without_project_groups(self):
        return self.env["res.users"].create(
            {
                "name": "Support Widget Filer",
                "login": "support_widget_filer",
                "groups_id": [Command.set([self.env.ref("base.group_user").id])],
            }
        )

    def _portal_user(self):
        return self.env["res.users"].create(
            {
                "name": "Support Portal User",
                "login": "support_portal_user",
                "groups_id": [Command.set([self.env.ref("base.group_portal").id])],
            }
        )

    def test_bug_count_for_dev1(self):
        Task = self.env["project.task"].with_user(self.user_dev1)
        domain = self._base_domain(self.project.id, self.user_dev1.id)
        self.assertEqual(Task.search_count(domain + [("task_type", "=", "bug")]), 2)

    def test_others_count_for_dev1(self):
        Task = self.env["project.task"].with_user(self.user_dev1)
        domain = self._base_domain(self.project.id, self.user_dev1.id)
        self.assertEqual(Task.search_count(domain + [("task_type", "!=", "bug")]), 1)

    def test_folded_stage_excluded(self):
        Task = self.env["project.task"].with_user(self.user_dev1)
        tickets = Task.search(self._base_domain(self.project.id, self.user_dev1.id))
        self.assertNotIn(self.bug_done, tickets)

    def test_other_user_tickets_not_visible(self):
        Task = self.env["project.task"].with_user(self.user_dev1)
        domain = self._base_domain(self.project.id, self.user_dev1.id)
        self.assertNotIn(self.bug_dev2, Task.search(domain + [("task_type", "=", "bug")]))

    def test_dev2_sees_own_bug(self):
        Task = self.env["project.task"].with_user(self.user_dev2)
        domain = self._base_domain(self.project.id, self.user_dev2.id)
        bugs = Task.search(domain + [("task_type", "=", "bug")])
        self.assertIn(self.bug_dev2, bugs)
        self.assertNotIn(self.bug_task1, bugs)

    def test_search_limit(self):
        Task = self.env["project.task"].with_user(self.user_dev1)
        domain = self._base_domain(self.project.id, self.user_dev1.id)
        self.assertEqual(len(Task.search(domain + [("task_type", "=", "bug")], limit=1)), 1)

    def test_search_no_limit(self):
        Task = self.env["project.task"].with_user(self.user_dev1)
        domain = self._base_domain(self.project.id, self.user_dev1.id)
        self.assertEqual(len(Task.search(domain + [("task_type", "=", "bug")], limit=None)), 2)

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

    def test_ticket_data_fields(self):
        task = self.bug_task1
        self.assertTrue(task.name)
        self.assertTrue(task.stage_id.name)
        self.assertTrue(task.create_date)
        self.assertEqual(task.task_type, "bug")

    def test_create_ticket_bug(self):
        task = (
            self.env["project.task"]
            .with_user(self.user_dev1)
            .create(
                {
                    "name": "New Bug from Widget",
                    "project_id": self.project.id,
                    "task_type": "bug",
                    "uat_user_ids": [Command.link(self.user_dev1.id)],
                    "user_ids": [Command.clear()],
                    "stage_id": self._first_open_stage().id,
                }
            )
        )
        self.assertTrue(task.exists())
        self.assertEqual(task.name, "New Bug from Widget")
        self.assertEqual(task.task_type, "bug")
        self.assertEqual(task.project_id, self.project)
        self.assertIn(self.user_dev1, task.uat_user_ids)
        self.assertFalse(task.user_ids, "Creator should not be auto-assigned")

    def test_create_ticket_feature(self):
        task = (
            self.env["project.task"]
            .with_user(self.user_dev1)
            .create(
                {
                    "name": "New Feature Request",
                    "project_id": self.project.id,
                    "task_type": "feature",
                    "uat_user_ids": [Command.link(self.user_dev1.id)],
                    "user_ids": [Command.clear()],
                }
            )
        )
        self.assertTrue(task.exists())
        self.assertEqual(task.task_type, "feature")

    def test_create_ticket_uses_first_unfolded_stage(self):
        first_stage = self._first_open_stage()
        task = (
            self.env["project.task"]
            .with_user(self.user_dev1)
            .create(
                {
                    "name": "Stage Test Ticket",
                    "project_id": self.project.id,
                    "task_type": "bug",
                    "uat_user_ids": [Command.link(self.user_dev1.id)],
                    "user_ids": [Command.clear()],
                    "stage_id": first_stage.id,
                }
            )
        )
        self.assertEqual(task.stage_id, first_stage)
        self.assertFalse(task.stage_id.fold)

    def test_task_type_selection_available(self):
        task_type_field = self.env["project.task"]._fields.get("task_type")
        self.assertIsNotNone(task_type_field)
        selection = task_type_field.selection
        if callable(selection):
            selection = selection(self.env["project.task"])
        values = [value for value, _label in selection]
        self.assertIn("bug", values)
        self.assertIn("feature", values)

    def test_project_task_create_is_denied_without_the_project_user_group(self):
        """Why create_request runs as sudo: the Support button is open to every internal user,
        and core grants task create only through Project / User."""
        filer = self._filer_without_project_groups()
        self.assertFalse(filer.has_group("project.group_project_user"))
        with self.assertRaises(AccessError):
            self.env["project.task"].with_user(filer).create({"name": "Blocked ticket", "project_id": self.project.id})

    def test_sudo_create_files_the_ticket_and_the_filer_can_read_it_back(self):
        filer = self._filer_without_project_groups()
        task = (
            self.env["project.task"]
            .sudo()
            .create(
                {
                    "name": "Ticket filed through the widget",
                    "project_id": self.project.id,
                    "task_type": "bug",
                    "uat_user_ids": [Command.link(filer.id)],
                    "user_ids": [Command.clear()],
                    "stage_id": self._first_open_stage().id,
                }
            )
        )
        self.assertIn(filer, task.uat_user_ids)
        self.assertFalse(task.user_ids, "the filer must not be auto-assigned")
        self.assertEqual(
            self.env["project.task"]
            .with_user(filer)
            .search_count([("id", "=", task.id), ("uat_user_ids", "in", [filer.id])]),
            1,
            "the filer must be able to read back the ticket the widget created for them",
        )

    def test_create_request_by_a_user_without_project_rights_files_an_unassigned_request(self):
        filer = self._filer_without_project_groups()

        ticket = self._call_route(filer, "create_request", name="Filed without project rights", task_type="bug")

        task = self.env["project.task"].browse(ticket["ticket_id"])
        self.assertEqual(task.project_id, self.project)
        self.assertEqual(task.stage_id, self._first_open_stage())
        self.assertEqual(task.uat_user_ids, filer)
        self.assertFalse(task.user_ids, "the filer must not be assigned to their own request")

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

    def test_the_texts_of_the_support_item_are_exported_for_translation(self):
        exported_terms = {
            (source, PYTHON_TRANSLATION_COMMENT in comments)
            for _module, _type, _name, _res_id, source, _value, comments in TranslationModuleReader(
                self.env.cr, modules=["project_support_request"]
            )
        }
        server_terms = (
            "Only internal users file support requests.",
            controller.NO_SUPPORT_PROJECT_ERROR,
            "Support request title is required.",
            "Invalid task type: %s",
        )
        web_terms = ("Priority: %s", "Lowest", "Low", "Medium", "High", "Highest", "%sm ago", "%sh ago", "%sd ago")

        self.assertEqual(
            ({(term, True) for term in server_terms} | {(term, False) for term in web_terms}) - exported_terms,
            set(),
            "the terms the translation export misses, True for those of the server",
        )

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
