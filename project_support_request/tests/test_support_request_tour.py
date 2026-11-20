from odoo import Command
from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSupportRequestTour(HttpCase):
    def test_a_user_without_project_rights_lists_and_files_requests_from_the_support_item(self):
        stage_open, stage_closed = self.env["project.task.type"].create(
            [{"name": "Backlog", "sequence": 1}, {"name": "Done", "sequence": 2, "fold": True}]
        )
        project = self.env["project.project"].create(
            {
                "name": "Project That Develops This Odoo",
                "is_ai_driven": True,
                "type_ids": [Command.set((stage_open + stage_closed).ids)],
            }
        )
        self.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        project.develops_this_odoo = True
        filer = new_test_user(self.env, login="support_tour_filer", groups="base.group_user")
        open_bug = self.env["project.task"].create(
            [
                {
                    "name": name,
                    "project_id": project.id,
                    "stage_id": stage.id,
                    "task_type": task_type,
                    "uat_user_ids": [Command.set(filer.ids)],
                    "user_ids": [Command.clear()],
                }
                for name, stage, task_type in (
                    ("Tour open bug", stage_open, "bug"),
                    ("Tour open feature", stage_open, "feature"),
                    ("Tour closed bug", stage_closed, "bug"),
                )
            ]
        )[0]
        # Keys stay off in tests: the key is given by hand.
        open_bug.key = "ZQT-1"
        # Core's task form names a widget of partner_autocomplete, which a minimal install
        # lacks: the request opens on a plain form so the tour does not depend on it.
        self.env["ir.ui.view"].create(
            {
                "name": "project.task.view.form.support.request.tour",
                "model": "project.task",
                "type": "form",
                "priority": 1,
                "arch": '<form><sheet><field name="name"/></sheet></form>',
            }
        )

        self.start_tour("/odoo", "project_support_request_tour", login=filer.login)

        request = self.env["project.task"].search([("name", "=", "Tour new request")])
        self.assertEqual(
            (request.project_id, request.stage_id, request.task_type, request.uat_user_ids, request.user_ids),
            (project, stage_open, "improvement", filer, self.env["res.users"]),
        )


@tagged("post_install", "-at_install")
class TestSupportRequestOtherCompanyTour(HttpCase):
    """A filer of the main company files and opens requests of the project of another company, on
    core's task form and list."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.current_company = cls.env.ref("base.main_company")
        cls.support_company = cls.env["res.company"].create({"name": "Support Company"})
        cls.stage_closed = cls.env["project.task.type"].create({"name": "Done", "sequence": 2, "fold": True})
        cls.project = cls.env["project.project"].create(
            {
                "name": "Project That Develops This Odoo",
                "is_ai_driven": True,
                "company_id": cls.support_company.id,
                "type_ids": [Command.create({"name": "Backlog", "sequence": 1}), Command.link(cls.stage_closed.id)],
            }
        )
        cls.env["project.project"].with_context(active_test=False).search(
            [("develops_this_odoo", "=", True)]
        ).develops_this_odoo = False
        cls.project.develops_this_odoo = True
        cls._show_core_task_views_without_partner_autocomplete()

    @classmethod
    def _show_core_task_views_without_partner_autocomplete(cls):
        """Core's task form and list name a widget of partner_autocomplete, which a minimal install
        lacks and which logs a warning: the field gets its default widget, the views stay core's."""
        View = cls.env["ir.ui.view"]
        xpath = (
            '<xpath expr="//field[@widget=\'res_partner_many2one\']" position="attributes">'
            '<attribute name="widget"/></xpath>'
        )
        for view_type in ("form", "list"):
            view = View.browse(View.default_view("project.task", view_type))
            widget_count = len(view._get_combined_arch().xpath("//field[@widget='res_partner_many2one']"))
            View.create(
                {
                    "name": f"project.task.{view_type}.support.request.tour",
                    "model": "project.task",
                    "inherit_id": view.id,
                    "arch": f"<data>{xpath * widget_count}</data>",
                }
            )

    def _assert_tour_files_and_opens_requests(self, allowed_companies):
        filer = new_test_user(
            self.env,
            login="support_tour_company_filer",
            groups="base.group_user",
            company_id=self.current_company.id,
            company_ids=[Command.set(allowed_companies.ids)],
        )
        # Created as the route creates a request: by the superuser in the filer's name, who follows it.
        closed_request = (
            self.env["project.task"]
            .with_user(filer)
            .sudo()
            .create(
                {
                    "name": "Closed request from another company",
                    "project_id": self.project.id,
                    "stage_id": self.stage_closed.id,
                    "uat_user_ids": [Command.set(filer.ids)],
                    "user_ids": [Command.clear()],
                }
            )
        )
        closed_request.message_post(
            body="Screenshot of the error", attachments=[("error_screenshot.txt", b"Access Error")]
        )

        self.start_tour("/odoo", "project_support_request_other_company_tour", login=filer.login)

        request = self.env["project.task"].search([("name", "=", "Request from another company")])
        self.assertEqual((request.project_id, request.uat_user_ids), (self.project, filer))

    def test_a_user_allowed_in_the_support_company_opens_requests_without_selecting_it(self):
        self._assert_tour_files_and_opens_requests(self.current_company | self.support_company)

    def test_a_user_not_allowed_in_the_support_company_opens_their_requests(self):
        self._assert_tour_files_and_opens_requests(self.current_company)
