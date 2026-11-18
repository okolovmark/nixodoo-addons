from odoo.tests import HttpCase, new_test_user, tagged

RECORD_URL = "/odoo/action-%s/%s"
PROJECT_ACTION = "project.open_view_project_all_config"
TASK_ACTION = "project.action_view_task"


@tagged("post_install", "-at_install")
class TestTaskKeyRoutes(HttpCase):
    def setUp(self):
        super().setUp()
        new_test_user(self.env, "project_ai_driven_key_routes", groups="base.group_user,project.group_project_user")
        self.project_model = self.env["project.project"].with_context(test_project_key=True)
        self.task_model = self.env["project.task"].with_context(test_project_key=True)
        self.project = self.project_model.create({"name": "Zulu Quebec Route", "key": "ZQROUTE"})
        self.task = self.task_model.create({"name": "Routed", "project_id": self.project.id})
        self.authenticate("project_ai_driven_key_routes", "project_ai_driven_key_routes")

    def _open(self, path):
        response = self.url_open(path, allow_redirects=False)
        self.assertEqual(response.status_code, 301, path)
        return response.headers["Location"]

    def _assert_opens(self, path, action_xmlid, record_id):
        location = self._open(path)
        self.assertTrue(location.endswith(RECORD_URL % (action_xmlid, record_id)), "%s: %s" % (path, location))

    def test_a_project_key_opens_its_project(self):
        self._assert_opens("/projects/zqroute", PROJECT_ACTION, self.project.id)

    def test_a_task_key_opens_its_task(self):
        self._assert_opens("/tasks/%s" % self.task.key.lower(), TASK_ACTION, self.task.id)

    def test_an_unknown_key_opens_an_empty_form(self):
        self._assert_opens("/tasks/ZQROUTE-99", TASK_ACTION, "new")

    def test_the_redirect_ends_on_the_web_client(self):
        response = self.url_open("/tasks/%s" % self.task.key)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.url.endswith(RECORD_URL % (TASK_ACTION, self.task.id)), response.url)

    def test_wildcards_in_a_key_match_only_themselves(self):
        for path in ("/tasks/zqroute_1", "/tasks/%25", "/tasks/ZQROUTE-%25"):
            with self.subTest(path=path):
                self._assert_opens(path, TASK_ACTION, "new")
        self._assert_opens("/projects/zqrout_", PROJECT_ACTION, "new")

    def test_a_key_the_user_cannot_read_opens_an_empty_form(self):
        other_company = self.env["res.company"].create({"name": "Zulu Quebec Elsewhere"})
        hidden_project = self.project_model.create(
            {"name": "Zulu Quebec Hidden", "key": "ZQHIDDEN", "company_id": other_company.id}
        )
        hidden_task = self.task_model.create({"name": "Hidden", "project_id": hidden_project.id})

        self._assert_opens("/tasks/%s" % hidden_task.key, TASK_ACTION, "new")
        self._assert_opens("/projects/zqhidden", PROJECT_ACTION, "new")
