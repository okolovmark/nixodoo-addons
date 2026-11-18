from lxml import etree

from odoo import Command
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestTaskViews(TransactionCase):
    def test_backlog_list_shows_the_size_under_the_label_of_the_field(self):
        arch = self.env.ref("project_team_dashboard.project_task_backlog_view_tree")._get_combined_arch()
        size_column = arch.xpath("//field[@name='tshirt_estimate']")[0]
        self.assertIsNone(size_column.get("string"))
        self.assertEqual(self.env["project.task"].fields_get(["tshirt_estimate"])["tshirt_estimate"]["string"], "Size")

    def test_backlog_list_names_the_hours_as_the_form_does(self):
        # the node project_ai_driven adds to the form: hr_timesheet adds another one
        form = etree.fromstring(self.env.ref("project_ai_driven.view_task_form2_common").arch)
        form_label = form.xpath("//field[@name='planned_hours']")[0].get("string")
        arch = self.env.ref("project_team_dashboard.project_task_backlog_view_tree")._get_combined_arch()
        self.assertEqual(arch.xpath("//field[@name='planned_hours']")[0].get("string"), "%s (h)" % form_label)


@tagged("post_install", "-at_install")
class TestBacklogSearch(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.backlog_action = cls.env.ref("project_team_dashboard.action_project_task_backlog")
        # Not "installed": ir.module.module caches those past the rollback.
        cls.searched_module = cls.env["ir.module.module"].create(
            {"name": "backlog_searched_module", "shortdesc": "Backlog Searched Module", "state": "to upgrade"}
        )
        cls.task_with_module, cls.other_task = cls.Task.create(
            [
                {
                    "name": "Backlog task with the module",
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_backlog.id,
                    "module_ids": [Command.set(cls.searched_module.ids)],
                },
                {
                    "name": "Backlog task with another module",
                    "project_id": cls.project.id,
                    "stage_id": cls.stage_backlog.id,
                    "module_ids": [Command.set(cls.env.ref("base.module_base").ids)],
                },
            ]
        )

    def _module_nodes(self, user):
        search_view = self.backlog_action.search_view_id
        arch = self.Task.with_user(user).get_views([(search_view.id, "search")])["views"]["search"]["arch"]
        return etree.fromstring(arch).xpath("//field[@name='module_ids']")

    def test_a_project_user_finds_backlog_tasks_by_module(self):
        module_nodes = self._module_nodes(self.user_dev1)

        self.assertEqual([node.get("string") for node in module_nodes], ["Module"])
        module_domain = [(module_nodes[0].get("name"), "ilike", self.searched_module.name)]
        found_tasks = self.Task.with_user(self.user_dev1).search(safe_eval(self.backlog_action.domain) + module_domain)
        self.assertIn(self.task_with_module, found_tasks)
        self.assertNotIn(self.other_task, found_tasks)

    def test_an_internal_user_without_project_rights_gets_no_module_field(self):
        internal_user = self.env["res.users"].create(
            {
                "name": "Internal Only",
                "login": "backlog_search_internal",
                "groups_id": [Command.set(self.env.ref("base.group_user").ids)],
            }
        )

        self.assertFalse(self._module_nodes(internal_user))
