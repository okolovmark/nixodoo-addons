from odoo import Command
from odoo.tests import new_test_user, tagged

from .test_ai_assistant_tools import AssistantCase

BRIEF_DENIED_TEXT = "project.task.brief is on the AI deny list"
TASK_DESCRIPTION = "AI brief task description"


@tagged("post_install", "-at_install")
class TestAiDenyListTaskBrief(AssistantCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "project.task" not in cls.env or "brief" not in cls.env["project.task"]._fields:
            return
        cls.project_manager = new_test_user(
            cls.env,
            login="test_assistant_project_manager",
            groups="base.group_user,project.group_project_manager",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
        )
        project = cls.env["project.project"].create({"name": "AI Brief Project", "company_id": cls.company.id})
        cls.task = cls.env["project.task"].create(
            {"name": "AI Brief Task", "project_id": project.id, "description": "<p>%s</p>" % TASK_DESCRIPTION}
        )

    def setUp(self):
        super().setUp()
        if "project.task" not in self.env or "brief" not in self.env["project.task"]._fields:
            self.skipTest("project_ai_driven is not installed")

    def _assert_brief_refused(self, tool, arguments):
        content, is_error = self._call(self.project_manager, tool, model="project.task", **arguments)
        self.assertTrue(is_error, "%s %s" % (tool, arguments))
        self.assertIn(BRIEF_DENIED_TEXT, content)

    def test_the_brief_is_refused_wherever_it_is_named(self):
        tools = self._tools(self.project_manager)
        self.assertTrue(tools._denied_field(self.env["project.task"]._fields["brief"]))
        task_domain = '[["id", "=", %s]]' % self.task.id
        for tool, arguments in (
            ("search_read", {"domain": task_domain, "fields": ["brief"], "limit": 0, "order": ""}),
            ("read", {"ids": self.task.ids, "fields": ["name", "brief"]}),
            ("search_read", {"domain": '[["brief", "ilike", "AI"]]', "fields": ["name"], "limit": 0, "order": ""}),
            ("search_read", {"domain": task_domain, "fields": ["name"], "limit": 0, "order": "brief desc"}),
            (
                "read_group",
                {"domain": task_domain, "groupby": ["brief"], "measures": [], "orderby": "", "limit": 0},
            ),
        ):
            with self.subTest(tool=tool, arguments=arguments):
                self._assert_brief_refused(tool, arguments)

    def test_the_fields_tool_leaves_the_brief_out(self):
        payload, is_error = self._call(self.project_manager, "fields", model="project.task")
        self.assertFalse(is_error, payload)
        self.assertIn("name", payload["fields"])
        self.assertNotIn("brief", payload["fields"])
        self.assertNotIn("brief", self._tools(self.project_manager)._visible_fields("project.task"))

    def test_the_task_s_own_fields_stay_readable(self):
        payload, is_error = self._search_read(
            self.project_manager, "project.task", [["id", "=", self.task.id]], ["name", "description"]
        )
        self.assertFalse(is_error, payload)
        [row] = payload["records"]
        self.assertEqual(row["name"], "AI Brief Task")
        self.assertIn(TASK_DESCRIPTION, row["description"])

        payload, is_error = self._call(
            self.project_manager, "read", model="project.task", ids=self.task.ids, fields=["name", "description"]
        )
        self.assertFalse(is_error, payload)
        self.assertEqual([row["name"] for row in payload["records"]], ["AI Brief Task"])
