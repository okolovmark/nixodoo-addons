from odoo import Command
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSupportRequestTour(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.own_company = cls.env.ref("base.main_company")
        cls.support_company = cls.env["res.company"].create({"name": "Company of the Support Project"})
        cls.stage_open, cls.stage_closed = cls.env["project.task.type"].create(
            [{"name": "Backlog", "sequence": 1}, {"name": "Done", "sequence": 2, "fold": True}]
        )
        cls.project = cls.env["project.project"].create(
            {
                "name": "Project That Develops This Odoo",
                "is_ai_driven": True,
                "company_id": cls.support_company.id,
                "type_ids": [Command.set((cls.stage_open + cls.stage_closed).ids)],
            }
        )
        cls.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        cls.project.develops_this_odoo = True

    def _assert_the_tour_files_and_reads_requests(self, allowed_companies):
        filer = new_test_user(
            self.env,
            login="support_tour_filer",
            groups="base.group_user",
            company_id=self.own_company.id,
            company_ids=[Command.set(allowed_companies.ids)],
        )
        self.env["project.task"].create(
            {
                "name": "Closed request of the filer",
                "project_id": self.project.id,
                "stage_id": self.stage_closed.id,
                "task_type": "bug",
                "uat_user_ids": [Command.set(filer.ids)],
                "user_ids": [Command.clear()],
            }
        )

        self.start_tour("/web", "project_support_request_tour", login=filer.login)

        request = self.env["project.task"].search([("name", "=", "Request from the tour")])
        self.assertEqual(
            (request.project_id, request.company_id, request.uat_user_ids),
            (self.project, self.support_company, filer),
        )

    def test_a_user_without_the_company_of_the_support_project_files_and_reads_requests(self):
        self._assert_the_tour_files_and_reads_requests(self.own_company)

    def test_a_user_allowed_in_the_company_of_the_support_project_reads_requests_without_selecting_it(self):
        self._assert_the_tour_files_and_reads_requests(self.own_company | self.support_company)
