from odoo.tests.common import HttpCase, new_test_user, tagged

RECORD_URL = "/web#id=%s&view_type=form&model=%s&action=%s"


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

    def _record_url(self, record_id, model_name, action_xmlid):
        return RECORD_URL % (record_id, model_name, self.env.ref(action_xmlid).id)

    def _open(self, path):
        response = self.url_open(path, allow_redirects=False)
        self.assertEqual(response.status_code, 301, path)
        return response.headers["Location"]

    def test_a_project_key_opens_its_project(self):
        self.assertTrue(
            self._open("/projects/zqroute").endswith(
                self._record_url(self.project.id, "project.project", "project.open_view_project_all_config")
            )
        )

    def test_a_task_key_opens_its_task(self):
        self.assertTrue(
            self._open("/tasks/%s" % self.task.key.lower()).endswith(
                self._record_url(self.task.id, "project.task", "project.action_view_task")
            )
        )

    def test_an_unknown_key_opens_an_empty_form(self):
        self.assertTrue(
            self._open("/tasks/ZQROUTE-99").endswith(self._record_url(-1, "project.task", "project.action_view_task"))
        )

    def test_the_redirect_ends_on_the_web_client(self):
        response = self.url_open("/tasks/%s" % self.task.key)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.url.endswith(self._record_url(self.task.id, "project.task", "project.action_view_task")),
            response.url,
        )

    def test_wildcards_in_a_key_match_only_themselves(self):
        for path in ("/tasks/zqroute_1", "/tasks/%25", "/tasks/ZQROUTE-%25"):
            with self.subTest(path=path):
                self.assertTrue(
                    self._open(path).endswith(self._record_url(-1, "project.task", "project.action_view_task"))
                )
        self.assertTrue(
            self._open("/projects/zqrout_").endswith(
                self._record_url(-1, "project.project", "project.open_view_project_all_config")
            )
        )

    def test_a_key_the_user_cannot_read_opens_an_empty_form(self):
        other_company = self.env["res.company"].create({"name": "Zulu Quebec Elsewhere"})
        hidden_project = self.project_model.create(
            {"name": "Zulu Quebec Hidden", "key": "ZQHIDDEN", "company_id": other_company.id}
        )
        hidden_task = self.task_model.create({"name": "Hidden", "project_id": hidden_project.id})

        self.assertTrue(
            self._open("/tasks/%s" % hidden_task.key).endswith(
                self._record_url(-1, "project.task", "project.action_view_task")
            )
        )
        self.assertTrue(
            self._open("/projects/zqhidden").endswith(
                self._record_url(-1, "project.project", "project.open_view_project_all_config")
            )
        )
