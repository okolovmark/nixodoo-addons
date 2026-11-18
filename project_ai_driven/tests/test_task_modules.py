from lxml import etree

from odoo import Command
from odoo.tests import Form, tagged
from odoo.tools.safe_eval import safe_eval

from .common import MODULE, ProjectAiDrivenCommon

TECHNICAL_NAME_KEY = "module_technical_name"
MODULES_NODE = "//field[@name='module_ids']"
MODULES_TABLE = "project_task_ir_module_module_rel"
PICKER_VIEWS = {
    "list": "%s.ir_module_module_view_tree_task_picker" % MODULE,
    "kanban": "%s.ir_module_module_view_kanban_task_picker" % MODULE,
}


@tagged("post_install", "-at_install")
class TestTaskModules(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.Module = cls.env["ir.module.module"]
        cls.module_base, cls.module_mail, cls.module_project = (
            cls.Module.search([("name", "=", name)]) for name in ("base", "mail", "project")
        )
        # No module of the test is "installed": ir.module.module caches those past the rollback.
        cls.upgrading_module, cls.removing_module, cls.absent_module, cls.uninstallable_module = cls.Module.create(
            [
                {"name": "project_ai_driven_upgrading", "shortdesc": "Upgrading Module", "state": "to upgrade"},
                {"name": "project_ai_driven_removing", "shortdesc": "Removing Module", "state": "to remove"},
                {"name": "project_ai_driven_absent", "shortdesc": "Absent Module", "state": "uninstalled"},
                {"name": "project_ai_driven_uninstallable", "shortdesc": "Broken Module", "state": "uninstallable"},
            ]
        )
        cls._keep_only_own_module_access()
        cls._set_develops_this_odoo(cls.project)

    @classmethod
    def _keep_only_own_module_access(cls):
        """Another module may let every internal user read modules, as
        base_install_request does: the project user of the tests reads them by
        this module's access right alone.
        """
        own_access = cls.env.ref("%s.access_ir_module_module_project_user" % MODULE)
        reading_accesses = cls.env["ir.access"].search(
            [
                ("model_id.model", "=", "ir.module.module"),
                ("for_read", "=", True),
                ("group_id", "not in", [False, cls.env.ref("base.group_system").id]),
            ]
        )
        (reading_accesses - own_access).active = False

    def _task_values(self, modules):
        return {"name": "Task with modules", "project_id": self.project.id, "module_ids": [Command.set(modules.ids)]}

    def _create_task(self, modules):
        return self.Task.create(self._task_values(modules))

    def _create_internal_user(self):
        return self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")

    def _uninstall(self, module):
        module.write({"state": "uninstalled"})
        self.env.invalidate_all()

    def _picker_context(self, user):
        return Form(self.task.with_user(user))._get_context("module_ids")

    def _module_nodes(self, user, view_xmlid, view_type):
        view = self.env.ref(view_xmlid)
        arch = self.Task.with_user(user).get_views([(view.id, view_type)])["views"][view_type]["arch"]
        return etree.fromstring(arch).xpath(MODULES_NODE)

    def test_installed_modules_are_added_on_create(self):
        task = self._create_task(self.module_project | self.module_base)

        self.assertEqual(task.module_ids, self.module_base | self.module_project)
        self.assertEqual(task.module_names, "base, project")

    def test_the_modules_of_a_task_are_stored_in_their_named_table(self):
        task = self._create_task(self.module_project | self.module_base)
        self.env.flush_all()

        self.env.cr.execute("SELECT module_id FROM %s WHERE task_id = %%s" % MODULES_TABLE, (task.id,))
        self.assertEqual({module_id for (module_id,) in self.env.cr.fetchall()}, set(task.module_ids.ids))

    def test_modules_marked_for_an_operation_are_installed_ones(self):
        self.task.write({"module_ids": [Command.link(self.module_mail.id)]})
        self.task.write({"module_ids": [Command.link(self.upgrading_module.id), Command.link(self.removing_module.id)]})

        self.assertEqual(self.task.module_ids, self.module_mail | self.upgrading_module | self.removing_module)
        self.assertEqual(self.task.module_names, "mail, project_ai_driven_removing, project_ai_driven_upgrading")

    def test_module_names_are_false_without_modules(self):
        self.assertFalse(self.task.module_ids)
        self.assertIs(self.task.module_names, False)
        self.assertIs(self.task.read(["module_names"])[0]["module_names"], False)

    def test_module_names_follow_the_modules(self):
        self.task.module_ids = self.module_mail
        self.assertEqual(self.task.module_names, "mail")

        self.task.module_ids = [Command.link(self.module_base.id)]
        self.assertEqual(self.task.module_names, "base, mail")

    def test_a_change_of_the_modules_posts_no_tracking(self):
        tracking_texts = self._tracking_texts(self.task)
        self.task.module_ids = self.upgrading_module
        self.task.module_ids = [Command.link(self.removing_module.id)]
        self.task.module_ids = [Command.unlink(self.upgrading_module.id)]
        self.assertEqual(self._tracking_texts(self.task), tracking_texts)

    def test_a_module_not_installed_is_refused_on_create(self):
        with self.assertRefused("project_ai_driven_absent"):
            self._create_task(self.module_project | self.absent_module)

    def test_a_module_not_installed_is_refused_as_a_default_of_the_context(self):
        with self.assertRefused("project_ai_driven_absent"):
            self.Task.with_context(default_module_ids=self.absent_module.ids).create(
                {"name": "Task with a default module", "project_id": self.project.id}
            )

    def test_a_batch_create_is_refused_when_one_task_carries_a_module_not_installed(self):
        with self.assertRefused("project_ai_driven_absent"):
            self.Task.create([self._task_values(self.module_base), self._task_values(self.absent_module)])

    def test_a_module_not_installed_is_refused_on_link(self):
        self.task.module_ids = self.module_base

        with self.assertRefused("project_ai_driven_absent"):
            self.task.write({"module_ids": [Command.link(self.absent_module.id)]})

    def test_the_modules_not_installed_of_a_set_are_named(self):
        modules = self.module_mail | self.uninstallable_module | self.absent_module

        with self.assertRefused("Not installed: project_ai_driven_absent, project_ai_driven_uninstallable"):
            self.task.write({"module_ids": [Command.set(modules.ids)]})

    def test_a_module_uninstalled_later_stays_on_the_task(self):
        task = self._create_task(self.removing_module | self.module_base)
        self._uninstall(self.removing_module)

        self.assertEqual(task.module_ids, self.removing_module | self.module_base)
        self.assertEqual(task.module_names, "base, project_ai_driven_removing")

        task.write({"name": "Saved again", "module_ids": [Command.set(task.module_ids.ids)]})
        task.write({"module_ids": [Command.link(self.module_mail.id)]})
        task.write({"module_ids": [Command.set((self.removing_module | self.module_project).ids)]})
        self.assertEqual(task.module_names, "project, project_ai_driven_removing")

        with Form(task.with_user(self.user_dev)) as task_form:
            self.assertIn(self.removing_module, task_form.module_ids)
            task_form.name = "Saved from the form"
        self.assertEqual(task.module_ids, self.removing_module | self.module_project)

    def test_a_module_uninstalled_later_is_linked_again_on_the_tasks_holding_it(self):
        tasks = self._create_task(self.removing_module) | self._create_task(self.removing_module | self.module_base)
        self._uninstall(self.removing_module)

        tasks.write({"module_ids": [Command.link(self.removing_module.id)]})

        self.assertEqual(tasks[0].module_names, "project_ai_driven_removing")
        self.assertEqual(tasks[1].module_names, "base, project_ai_driven_removing")

    def test_a_write_on_several_tasks_is_refused_when_one_adds_a_module_not_installed(self):
        holding_task = self._create_task(self.removing_module)
        self._uninstall(self.removing_module)

        with self.assertRefused("project_ai_driven_removing"):
            (holding_task | self.task).write({"module_ids": [Command.link(self.removing_module.id)]})

    def test_the_technical_name_is_the_name_under_the_context_key(self):
        module = self.absent_module

        self.assertEqual(module.display_name, "Absent Module")
        technical_module = module.with_context(**{TECHNICAL_NAME_KEY: True})
        self.assertEqual(technical_module.display_name, "project_ai_driven_absent")
        self.assertEqual(
            technical_module.read(["display_name"]), [{"id": module.id, "display_name": "project_ai_driven_absent"}]
        )

    def test_the_technical_name_follows_a_rename(self):
        technical_module = self.absent_module.with_context(**{TECHNICAL_NAME_KEY: True})
        self.assertEqual(technical_module.display_name, "project_ai_driven_absent")

        self.absent_module.name = "project_ai_driven_renamed"

        self.assertEqual(technical_module.display_name, "project_ai_driven_renamed")

    def test_a_module_is_found_by_its_technical_name_or_its_title(self):
        technical_modules = self.Module.with_context(**{TECHNICAL_NAME_KEY: True})

        for searched_name in ("project_ai_driven_absent", "Absent Module"):
            self.assertIn((self.absent_module.id, "Absent Module"), self.Module.name_search(searched_name))
            self.assertIn(
                (self.absent_module.id, "project_ai_driven_absent"), technical_modules.name_search(searched_name)
            )

    def test_a_copy_has_no_modules(self):
        self.task.module_ids = self.module_base | self.module_mail

        self.assertFalse(self.task.copy().module_ids)

    def test_a_project_user_reads_the_modules_of_the_form_by_technical_name(self):
        self.assertFalse(self.user_dev.has_group("base.group_system"))
        self.task.module_ids = self.module_project | self.upgrading_module

        task_form = Form(self.task.with_user(self.user_dev))

        self.assertFalse(task_form._get_modifier("module_ids", "invisible"))
        modules_node = task_form._view["tree"].xpath(MODULES_NODE)[0]
        self.assertEqual(modules_node.get("widget"), "many2many_tags")
        self.assertEqual(safe_eval(modules_node.get("options")), {"no_create": True})
        field_context = task_form._get_context("module_ids")
        form_modules = task_form.module_ids[:].with_context(**field_context)
        self.assertEqual(
            sorted(form_modules.read(["display_name"]), key=lambda values: values["id"]),
            sorted(
                [
                    {"id": self.module_project.id, "display_name": "project"},
                    {"id": self.upgrading_module.id, "display_name": "project_ai_driven_upgrading"},
                ],
                key=lambda values: values["id"],
            ),
        )
        self.assertEqual(
            self.task.with_user(self.user_dev).read(["module_names"])[0]["module_names"],
            "project, project_ai_driven_upgrading",
        )

        offered_modules = (
            self.Module.with_user(self.user_dev)
            .with_context(**field_context)
            .name_search("project_ai_driven_", domain=safe_eval(modules_node.get("domain")))
        )
        offered_module_ids = [module_id for module_id, _name in offered_modules]
        self.assertIn((self.upgrading_module.id, "project_ai_driven_upgrading"), offered_modules)
        self.assertIn((self.removing_module.id, "project_ai_driven_removing"), offered_modules)
        self.assertNotIn(self.absent_module.id, offered_module_ids)
        self.assertNotIn(self.uninstallable_module.id, offered_module_ids)

    def test_the_picker_shows_technical_names_without_actions(self):
        for user in (self.user_dev, self.env.ref("base.user_admin")):
            picker_modules = self.Module.with_user(user).with_context(**self._picker_context(user))
            picker_views = picker_modules.get_views([(False, view_type) for view_type in PICKER_VIEWS])["views"]
            for view_type, view_xmlid in PICKER_VIEWS.items():
                with self.subTest(user=user.login, view_type=view_type):
                    self.assertEqual(picker_views[view_type]["id"], self.env.ref(view_xmlid).id)
                    picker_arch = etree.fromstring(picker_views[view_type]["arch"])
                    self.assertEqual(set(picker_arch.xpath("//field/@name")), {"name", "shortdesc"})
                    self.assertFalse(picker_arch.xpath("//button | //a"))

    def test_the_apps_keep_the_views_of_base(self):
        default_views = self.Module.get_views([(False, view_type) for view_type in PICKER_VIEWS])["views"]
        picker_view_ids = [self.env.ref(view_xmlid).id for view_xmlid in PICKER_VIEWS.values()]

        for view_type in PICKER_VIEWS:
            self.assertNotIn(default_views[view_type]["id"], picker_view_ids)

    def test_the_form_hides_the_modules_outside_ai_driven_projects(self):
        other_project = self.env["project.project"].create({"name": "Other Project", "type_ids": [Command.clear()]})
        other_task = self.Task.create({"name": "Other Task", "project_id": other_project.id})

        task_form = Form(other_task.with_user(self.user_dev))

        self.assertTrue(task_form._get_modifier("module_ids", "invisible"))

    def test_an_internal_user_without_project_rights_reads_a_task_as_before(self):
        internal_user = self._create_internal_user()
        self.task.module_ids = self.module_base
        task = self.task.with_user(internal_user)

        self.assertFalse({"module_ids", "module_names"} & set(task.fields_get()))
        self.assertEqual(task.read(["name"]), [{"id": task.id, "name": "AI-driven Task"}])
        self.assertFalse(self._module_nodes(internal_user, "project.view_task_form2", "form"))
        self.assertFalse(self._module_nodes(internal_user, "project.view_task_search_form", "search"))

    def test_an_internal_user_allowed_to_create_tasks_creates_one_without_modules(self):
        internal_user = self._create_internal_user()
        # Core lets project users alone create tasks in a project; another module may let every employee.
        self.env["ir.access"].create(
            {
                "name": "project.task.internal.create",
                "model_id": self.env["ir.model"]._get_id("project.task"),
                "group_id": self.env.ref("base.group_user").id,
                "operation": "cr",
            }
        )
        project = self.env["project.project"].create(
            {"name": "Internal Project", "user_id": internal_user.id, "type_ids": [Command.clear()]}
        )

        task = self.Task.with_user(internal_user).create({"name": "Internal Task", "project_id": project.id})

        self.assertFalse(task.sudo().module_ids)

    def test_tasks_are_found_by_a_module_technical_name_or_title(self):
        self.task.module_ids = self.upgrading_module
        other_task = self._create_task(self.module_base)
        tasks = self.Task.with_user(self.user_dev)

        for search_view in ("project.view_task_search_form", "project.view_task_search_form_base"):
            module_nodes = self._module_nodes(self.user_dev, search_view, "search")
            self.assertEqual([node.get("string") for node in module_nodes], ["Module"], search_view)
        for searched_name in ("project_ai_driven_upgrading", "Upgrading Module"):
            found_tasks = tasks.search([("module_ids", "ilike", searched_name)])
            self.assertIn(self.task, found_tasks)
            self.assertNotIn(other_task, found_tasks)
