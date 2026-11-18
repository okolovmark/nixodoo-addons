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
