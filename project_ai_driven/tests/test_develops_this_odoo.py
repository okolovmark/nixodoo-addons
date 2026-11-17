from contextlib import contextmanager

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import Form, tagged

from .common import ProjectAiDrivenCommon


@tagged("post_install", "-at_install")
class TestDevelopsThisOdoo(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Project = cls.env["project.project"]
        cls._set_develops_this_odoo(cls.project)
        cls.other_project = cls._create_project("Other AI-driven Project", is_ai_driven=True)
        cls.plain_project = cls._create_project("Plain Project")
        cls.module_base, cls.module_mail = (
            cls.env["ir.module.module"].search([("name", "=", name)]) for name in ("base", "mail")
        )
        cls.manager = cls._create_user(
            "Project Manager", "project_ai_driven_manager", "manager@example.com", "project.group_project_manager"
        )
        cls.settings_admin = cls._create_user(
            "Settings Administrator",
            "project_ai_driven_settings_admin",
            "settings_admin@example.com",
            "project.group_project_manager",
        )
        cls.settings_admin.groups_id = [Command.link(cls.env.ref("base.group_system").id)]

    @classmethod
    def _create_project(cls, name, **values):
        return cls.Project.create({"name": name, "type_ids": [Command.clear()], **values})

    def _task_form(self, project):
        task = self.env["project.task"].create({"name": "Task of %s" % project.name, "project_id": project.id})
        return Form(task.with_user(self.user_dev))

    def _flag_nodes_on_project_form(self, user):
        project_form_view = self.env.ref("project.edit_project")
        arch = self.Project.with_user(user).get_view(project_form_view.id, "form")["arch"]
        return etree.fromstring(arch).xpath("//field[@name='develops_this_odoo']")

    @contextmanager
    def assertFlagEditRefused(self):
        with self.assertRaises(AccessError) as refusal:
            yield
        self.assertIn("Only a Settings administrator", refusal.exception.args[0])

    @contextmanager
    def assertRemovalRefused(self):
        with self.assertRaises(AccessError) as refusal:
            yield
        self.assertIn(
            "%s develops this Odoo: a Settings administrator moves Develops This Odoo" % self.project.display_name,
            refusal.exception.args[0],
        )

    def test_the_project_that_develops_this_odoo_shows_the_flag_and_the_modules_of_its_tasks(self):
        project_form = Form(self.project)

        self.assertFalse(project_form._get_modifier("develops_this_odoo", "invisible"))
        self.assertTrue(project_form.develops_this_odoo)
        self.assertFalse(self._task_form(self.project)._get_modifier("module_ids", "invisible"))

    def test_an_ai_driven_project_without_the_flag_shows_it_and_hides_the_modules_of_its_tasks(self):
        project_form = Form(self.other_project)

        self.assertFalse(project_form._get_modifier("develops_this_odoo", "invisible"))
        self.assertFalse(project_form.develops_this_odoo)
        self.assertTrue(self._task_form(self.other_project)._get_modifier("module_ids", "invisible"))

    def test_a_project_not_ai_driven_hides_the_flag_and_the_modules_of_its_tasks(self):
        self.assertTrue(Form(self.plain_project)._get_modifier("develops_this_odoo", "invisible"))
        self.assertTrue(self._task_form(self.plain_project)._get_modifier("module_ids", "invisible"))

    def test_the_form_hides_the_flag_once_ai_driven_is_cleared(self):
        project_form = Form(self.other_project)
        project_form.is_ai_driven = False

        self.assertTrue(project_form._get_modifier("develops_this_odoo", "invisible"))

    def test_the_flag_stays_on_the_form_until_cleared_once_ai_driven_is_cleared(self):
        project_form = Form(self.project)
        project_form.is_ai_driven = False
        self.assertFalse(project_form._get_modifier("develops_this_odoo", "invisible"))

        project_form.develops_this_odoo = False
        self.assertTrue(project_form._get_modifier("develops_this_odoo", "invisible"))

    def test_the_project_form_shows_the_flag_to_a_settings_administrator_only(self):
        self.assertEqual(len(self._flag_nodes_on_project_form(self.settings_admin)), 1)
        self.assertFalse(self._flag_nodes_on_project_form(self.manager))
        self.assertFalse(self._flag_nodes_on_project_form(self.user_lead))

    def test_a_project_manager_cannot_set_or_clear_the_flag(self):
        manager_project_model = self.Project.with_user(self.manager)

        with self.assertFlagEditRefused():
            self.other_project.with_user(self.manager).develops_this_odoo = True
        with self.assertFlagEditRefused():
            self.project.with_user(self.manager).write({"develops_this_odoo": False})
        self.project.develops_this_odoo = False
        with self.assertFlagEditRefused():
            manager_project_model.create(
                {"name": "New AI-driven Project", "is_ai_driven": True, "develops_this_odoo": True}
            )

        self.assertFalse(self.Project.search([("develops_this_odoo", "=", True)]))

    def test_a_project_manager_creating_a_second_flagged_project_meets_the_access_error_first(self):
        with self.assertFlagEditRefused():
            self.Project.with_user(self.manager).create(
                {"name": "New AI-driven Project", "develops_this_odoo": True, "is_ai_driven": True}
            )

        self.assertEqual(self.Project.search([("develops_this_odoo", "=", True)]), self.project)

    def test_a_project_manager_cannot_create_a_project_with_the_flag_from_a_default(self):
        self.project.develops_this_odoo = False
        manager_project_model = self.Project.with_user(self.manager).with_context(
            default_is_ai_driven=True, default_develops_this_odoo=True
        )

        with self.assertFlagEditRefused():
            manager_project_model.create({"name": "New AI-driven Project"})
        with self.assertFlagEditRefused():
            manager_project_model.name_create("Quick AI-driven Project")

        self.assertFalse(self.Project.search([("develops_this_odoo", "=", True)]))

    def test_a_settings_administrator_creates_a_project_with_the_flag_from_a_default(self):
        self.project.develops_this_odoo = False
        admin_project_model = self.Project.with_user(self.settings_admin).with_context(
            default_is_ai_driven=True, default_develops_this_odoo=True
        )

        project_id = admin_project_model.name_create("Quick AI-driven Project")[0]

        self.assertEqual(self.Project.search([("develops_this_odoo", "=", True)]).ids, [project_id])

    def test_a_project_manager_cannot_archive_or_delete_the_project_that_develops_this_odoo(self):
        flagged_project = self.project.with_user(self.manager)

        for remove in (flagged_project.action_archive, flagged_project.toggle_active, flagged_project.unlink):
            with self.subTest(remove.__name__), self.assertRemovalRefused():
                remove()

        self.assertTrue(self.project.exists())
        self.assertTrue(self.project.active)

    def test_a_settings_administrator_archives_and_deletes_the_project_that_develops_this_odoo(self):
        # A project without tasks: core deletes the project before its tasks, and a task left without a
        # project is no longer one the project manager's rule lets a non-assignee delete, whatever the flag says.
        self.project.develops_this_odoo = False
        taskless_project = self._create_project("Taskless AI-driven Project", is_ai_driven=True)
        taskless_project.develops_this_odoo = True
        flagged_project = taskless_project.with_user(self.settings_admin)

        flagged_project.action_archive()
        self.assertFalse(taskless_project.active)
        flagged_project.unlink()
        self.assertFalse(taskless_project.exists())

    def test_a_project_manager_archives_and_deletes_a_project_that_does_not_develop_this_odoo(self):
        other_project = self.other_project.with_user(self.manager)

        other_project.action_archive()
        self.assertFalse(self.other_project.active)
        other_project.unlink()
        self.assertFalse(self.other_project.exists())

    def test_a_project_manager_archives_projects_next_to_the_archived_one_that_develops_this_odoo(self):
        self.project.active = False

        (self.project | self.other_project).with_user(self.manager).write({"active": False})

        self.assertFalse(self.other_project.active)
        self.assertTrue(self.project.develops_this_odoo)

    def test_a_project_manager_still_saves_a_project_with_the_flag_as_it_is(self):
        self.project.with_user(self.manager).write({"name": "Renamed Project", "develops_this_odoo": True})
        self.other_project.with_user(self.manager).write({"develops_this_odoo": False})
        self.Project.with_user(self.manager).create({"name": "New Project", "develops_this_odoo": False})

        self.assertEqual(self.project.name, "Renamed Project")
        self.assertTrue(self.project.develops_this_odoo)

    def test_a_settings_administrator_moves_the_flag_and_both_projects_keep_a_trace(self):
        self._flush_tracking()

        self.project.with_user(self.settings_admin).develops_this_odoo = False
        self.other_project.with_user(self.settings_admin).develops_this_odoo = True
        self._flush_tracking()

        self.assertEqual(self.Project.search([("develops_this_odoo", "=", True)]), self.other_project)
        for project, old_value, new_value in ((self.project, 1, 0), (self.other_project, 0, 1)):
            tracking_values = project.message_ids.tracking_value_ids.filtered(
                lambda value: value.field.name == "develops_this_odoo"
            )
            self.assertEqual(
                [(value.old_value_integer, value.new_value_integer) for value in tracking_values],
                [(old_value, new_value)],
                project.name,
            )

    def test_a_task_follows_whether_its_project_develops_this_odoo(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.other_project.id})
        self.assertTrue(self.task.develops_this_odoo)
        self.assertFalse(other_task.develops_this_odoo)

        self._set_develops_this_odoo(self.other_project)

        self.assertFalse(self.task.develops_this_odoo)
        self.assertTrue(other_task.develops_this_odoo)

    def test_a_second_project_is_refused_the_flag_with_the_name_of_the_one_that_has_it(self):
        with self.assertRefused("One project at most develops this Odoo", self.project.display_name):
            self.other_project.develops_this_odoo = True

        with self.assertRefused(self.project.display_name):
            self._create_project("New AI-driven Project", is_ai_driven=True, develops_this_odoo=True)

    def test_an_archived_project_keeps_the_flag_from_the_others(self):
        self.project.active = False

        with self.assertRefused(self.project.display_name):
            self.other_project.develops_this_odoo = True

    def test_two_projects_flagged_in_one_write_are_refused(self):
        self.project.develops_this_odoo = False
        new_project = self._create_project("New AI-driven Project", is_ai_driven=True)

        with self.assertRefused(self.other_project.display_name, new_project.display_name):
            (self.other_project | new_project).write({"develops_this_odoo": True})

    def test_the_flag_moves_once_cleared_on_the_project_that_has_it(self):
        self.project.develops_this_odoo = False
        self.other_project.develops_this_odoo = True

        self.assertEqual(self.Project.search([("develops_this_odoo", "=", True)]), self.other_project)

    def test_the_project_that_has_the_flag_is_named_to_a_user_who_cannot_read_it(self):
        other_company = self.env["res.company"].create({"name": "Other Company"})
        self.project.develops_this_odoo = False
        foreign_project = self._create_project(
            "Foreign Project", is_ai_driven=True, develops_this_odoo=True, company_id=other_company.id
        )
        self.assertNotIn(foreign_project, self.Project.with_user(self.settings_admin).search([]))

        with self.assertRefused(foreign_project.display_name):
            self.other_project.with_user(self.settings_admin).develops_this_odoo = True

    def test_a_project_not_ai_driven_is_refused_the_flag(self):
        self.project.develops_this_odoo = False

        with self.assertRefused("Only an AI-driven project develops this Odoo", self.plain_project.display_name):
            self.plain_project.develops_this_odoo = True

        with self.assertRefused("Only an AI-driven project develops this Odoo"):
            self._create_project("New Plain Project", develops_this_odoo=True)

    def test_the_project_that_develops_this_odoo_stays_ai_driven(self):
        with self.assertRefused(
            "Only an AI-driven project develops this Odoo",
            "have a Settings administrator clear Develops This Odoo on it or move it",
            self.project.display_name,
        ):
            self.project.with_user(self.manager).is_ai_driven = False

        self.project.write({"is_ai_driven": False, "develops_this_odoo": False})
        self.assertFalse(self.project.is_ai_driven)

    def test_a_copy_does_not_develop_this_odoo(self):
        project_copy = self.project.copy()

        self.assertTrue(project_copy.is_ai_driven)
        self.assertFalse(project_copy.develops_this_odoo)
        self.assertTrue(self.project.develops_this_odoo)

    def test_the_modules_of_a_task_stay_when_its_project_does_not_develop_this_odoo(self):
        self.task.module_ids = self.module_base
        self.project.develops_this_odoo = False

        self.assertEqual(self.task.module_ids, self.module_base)

        self.task.write({"module_ids": [Command.link(self.module_mail.id)]})
        other_task = self.env["project.task"].create(
            {
                "name": "Other Task",
                "project_id": self.other_project.id,
                "module_ids": [Command.set(self.module_mail.ids)],
            }
        )
        self.assertEqual(self.task.module_ids, self.module_base | self.module_mail)
        self.assertEqual(other_task.module_names, "mail")
