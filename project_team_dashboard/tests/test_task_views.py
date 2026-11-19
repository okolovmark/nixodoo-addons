from lxml import etree

from odoo import Command
from odoo.tests import TransactionCase, tagged
from odoo.tools.safe_eval import safe_eval

from .common import TeamDashboardCommon

# the field types the web client registers each widget of these views for (`supportedTypes`)
WIDGET_FIELD_TYPES = {
    "boolean_toggle": ("boolean",),
    "date": ("date",),
    "daterange": ("date", "datetime"),
    "float_time": ("float",),
    "handle": ("integer",),
    "many2many_avatar_user": ("many2many", "one2many"),
    "many2many_tags": ("many2many", "one2many"),
}


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
        form_label = form.xpath("//field[@name='allocated_hours']")[0].get("string")
        arch = self.env.ref("project_team_dashboard.project_task_backlog_view_tree")._get_combined_arch()
        self.assertEqual(arch.xpath("//field[@name='allocated_hours']")[0].get("string"), "%s (h)" % form_label)

    def test_the_backlog_opens_its_own_list(self):
        action = self.env.ref("project_team_dashboard.action_project_task_backlog")

        self.assertEqual(action.view_mode, "list,form")
        self.assertEqual(action.view_ids.view_id, self.env.ref("project_team_dashboard.project_task_backlog_view_tree"))

    def test_no_view_puts_a_widget_on_a_field_type_it_does_not_support(self):
        """The web client keeps such a widget and only warns on the console."""
        view_ids = (
            self.env["ir.model.data"]
            .search([("module", "=", "project_team_dashboard"), ("model", "=", "ir.ui.view")])
            .mapped("res_id")
        )
        widget_misuses = []
        for view in self.env["ir.ui.view"].browse(view_ids):
            for node in etree.fromstring(view.arch).iter("field"):
                widget = node.get("widget")
                if not widget:
                    continue
                self.assertIn(widget, WIDGET_FIELD_TYPES, "add the supportedTypes of its field component")
                field_type = self.env[view.model]._fields[node.get("name")].type
                if field_type not in WIDGET_FIELD_TYPES[widget]:
                    widget_misuses.append((view.name, node.get("name"), widget, field_type))
        self.assertEqual(widget_misuses, [])


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

    def test_a_project_user_finds_backlog_tasks_by_key(self):
        # Task keys are off while tests run.
        self.task_with_module.key = "BKL-7"
        self.other_task.key = "BKL-8"
        search_view = self.backlog_action.search_view_id
        arch = self.Task.with_user(self.user_dev1).get_views([(search_view.id, "search")])["views"]["search"]["arch"]
        self.assertTrue(etree.fromstring(arch).xpath("//field[@name='key']"))

        found_tasks = self.Task.with_user(self.user_dev1).search(
            safe_eval(self.backlog_action.domain) + [("key", "ilike", "BKL-7")]
        )

        self.assertEqual(found_tasks, self.task_with_module)

    def test_an_internal_user_without_project_rights_gets_no_module_field(self):
        internal_user = self.env["res.users"].create(
            {
                "name": "Internal Only",
                "login": "backlog_search_internal",
                "group_ids": [Command.set(self.env.ref("base.group_user").ids)],
            }
        )

        self.assertFalse(self._module_nodes(internal_user))

    def test_the_backlog_leaves_out_project_less_tasks_and_the_tasks_of_project_templates(self):
        project_template = self.env["project.project"].create(
            {"name": "Backlog Project Template", "is_template": True, "type_ids": [Command.set(self.stage_backlog.ids)]}
        )
        project_template_task, project_less_task = self.Task.create(
            [
                {"name": "In a project template", "project_id": project_template.id, "stage_id": self.stage_backlog.id},
                {"name": "Taken off its project", "project_id": self.project.id, "stage_id": self.stage_backlog.id},
            ]
        )
        # Odoo clears the stage of a task whose project is cleared, unless the same write sets one.
        project_less_task.write({"project_id": False, "stage_id": self.stage_backlog.id})
        self.assertFalse(project_less_task.project_id)
        self.assertTrue(project_less_task.stage_id.is_backlog)
        self.assertFalse(project_template_task.has_template_ancestor)

        backlog_tasks = self.Task.search(safe_eval(self.backlog_action.domain))

        self.assertIn(self.task_with_module, backlog_tasks)
        self.assertNotIn(project_template_task, backlog_tasks)
        self.assertNotIn(project_less_task, backlog_tasks)
