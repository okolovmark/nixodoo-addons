import unittest
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError, RedirectWarning
from odoo.tests import Form
from odoo.tests.common import HttpCase, tagged
from odoo.tools import mute_logger

from .common import NO_ESTIMATE, NO_TYPE, NOT_AI_DRIVEN, SHARING_FORM_VIEW, ProjectAiDrivenCommon

QUICK_CREATE_VIEW = "project.quick_create_task_form"
SHARING_QUICK_CREATE_VIEW = "project.project_sharing_quick_create_task_form"


@tagged("post_install", "-at_install")
class TestTaskQuickCreate(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_backlog = cls._create_stage("Backlog", 1)
        cls.stage_development = cls._create_stage("Development", 2)
        cls.stage_delivered = cls._create_stage("Delivered", 3)
        cls.stage_canceled = cls._create_stage("Canceled", 4, fold=True)
        cls.project.write(
            {
                "estimate_required_states": [Command.set((cls.stage_development | cls.stage_delivered).ids)],
                "pr_required_states": [Command.set(cls.stage_delivered.ids)],
            }
        )

    def _open_quick_create(self, project, stage, user=None):
        """As a kanban column opens it: the project and the column's stage come as defaults."""
        context = {"default_stage_id": stage.id}
        if project:
            context["default_project_id"] = project.id
        task_model = self.env["project.task"].with_context(**context)
        if user:
            task_model = task_model.with_user(user)
        return Form(task_model, view=QUICK_CREATE_VIEW)

    def assertShown(self, task_form, is_required):
        for field_name in ("task_type", "allocated_hours"):
            self.assertFalse(task_form._get_modifier(field_name, "invisible"), field_name)
            self.assertEqual(task_form._get_modifier(field_name, "required"), is_required, field_name)

    def test_marked_column_requires_the_type_and_the_estimate(self):
        task_form = self._open_quick_create(self.project, self.stage_development)
        task_form.display_name = "Quick Task"
        self.assertShown(task_form, is_required=True)
        with self.assertRaises(AssertionError) as missing_field:
            task_form.save()
        self.assertIn("task_type", str(missing_field.exception))

        # the web client of Odoo 20 asks for no float, required or not: the rule refuses zero
        task_form.task_type = "bug"
        with self.assertRefused(NO_ESTIMATE):
            task_form.save()

        task_form.allocated_hours = 1.5
        task = task_form.save()
        self.assertRecordValues(
            task,
            [
                {
                    "name": "Quick Task",
                    "stage_id": self.stage_development.id,
                    "task_type": "bug",
                    "allocated_hours": 1.5,
                    "tshirt_estimate": "xs",
                }
            ],
        )

    def test_column_not_marked_leaves_them_optional(self):
        for stage in (self.stage_backlog, self.stage_canceled):
            with self.subTest(stage=stage.name):
                task_form = self._open_quick_create(self.project, stage)
                task_form.display_name = "Quick Task"
                self.assertShown(task_form, is_required=False)
                task = task_form.save()
                self.assertRecordValues(task, [{"stage_id": stage.id, "task_type": False, "allocated_hours": 0}])

    def test_column_marked_for_a_resolution_too_refuses_the_save(self):
        task_form = self._open_quick_create(self.project, self.stage_delivered)
        task_form.display_name = "Quick Task"
        self.assertShown(task_form, is_required=True)
        task_form.task_type = "bug"
        task_form.allocated_hours = 2
        with self.assertRefused("Choose a Resolution"):
            task_form.save()

    def test_hidden_on_a_project_not_flagged_and_on_a_task_without_a_project(self):
        # marked too: only the flag keeps the fields hidden
        plain_project = self.env["project.project"].create(
            {
                "name": "Plain Project",
                "type_ids": [Command.set(self.stage_development.ids)],
                "estimate_required_states": [Command.set(self.stage_development.ids)],
            }
        )
        for project in (plain_project, self.env["project.project"]):
            with self.subTest(project=project.name):
                task_form = self._open_quick_create(project, self.stage_development)
                for field_name in ("task_type", "allocated_hours"):
                    self.assertTrue(task_form._get_modifier(field_name, "invisible"), field_name)
                    self.assertFalse(task_form._get_modifier(field_name, "required"), field_name)
                with self.assertRaises(AssertionError):
                    task_form.task_type = "bug"

        task_form = self._open_quick_create(plain_project, self.stage_development)
        task_form.display_name = "Quick Task"
        self.assertRecordValues(task_form.save(), [{"stage_id": self.stage_development.id, "task_type": False}])

    def test_quick_create_of_my_tasks_follows_the_project_picked(self):
        """As My Tasks opens core's quick create, its columns being personal
        stages: no project in the context, so the form shows the project field,
        and the task takes the first stage of the project picked.
        """
        self.project.estimate_required_states = [Command.link(self.stage_backlog.id)]
        my_tasks_context = {"my_tasks": 1, "default_user_ids": [Command.link(self.env.uid)]}
        task_form = Form(self.env["project.task"].with_context(**my_tasks_context), view=QUICK_CREATE_VIEW)
        self.assertFalse(task_form._get_modifier("project_id", "invisible"))
        task_form.display_name = "Quick Task"
        for field_name in ("task_type", "allocated_hours"):
            self.assertTrue(task_form._get_modifier(field_name, "invisible"), field_name)

        task_form.project_id = self.project

        self.assertEqual(task_form.stage_id, self.stage_backlog)
        self.assertShown(task_form, is_required=True)
        task_form.task_type = "bug"
        task_form.allocated_hours = 1
        self.assertRecordValues(
            task_form.save(), [{"project_id": self.project.id, "stage_id": self.stage_backlog.id, "task_type": "bug"}]
        )

    def test_project_user_quick_creates_in_a_marked_column(self):
        task_form = self._open_quick_create(self.project, self.stage_development, user=self.user_dev)
        task_form.display_name = "Quick Task of a Developer"
        self.assertShown(task_form, is_required=True)
        task_form.task_type = "feature"
        task_form.allocated_hours = 4
        task = task_form.save()
        self.assertRecordValues(
            task,
            [
                {
                    "create_uid": self.user_dev.id,
                    "stage_id": self.stage_development.id,
                    "task_type": "feature",
                    "allocated_hours": 4,
                }
            ],
        )

    def _quick_create_titled(self, title, stage, **values):
        task_form = self._open_quick_create(self.project, stage)
        task_form.display_name = title
        for field_name, value in values.items():
            setattr(task_form, field_name, value)
        return task_form.save()

    def test_an_estimate_given_wins_over_hours_a_title_gives(self):
        """As hr_timesheet reads "Fix 2h", in the inverse of display_name: a stand-in, the module does not depend on it."""
        model_cls = type(self.env["project.task"])
        inverse_display_name = model_cls._inverse_display_name

        def read_hours_from_title(tasks):
            inverse_display_name(tasks)
            for task in tasks.filtered(lambda task: task.name.endswith(" 2h")):
                task.write({"name": task.name.removesuffix(" 2h"), "allocated_hours": 2})

        with patch.object(model_cls, "_inverse_display_name", read_hours_from_title):
            given_task = self._quick_create_titled("Fix 2h", self.stage_development, task_type="bug", allocated_hours=3)
            titled_task = self._quick_create_titled("Fix 2h", self.stage_backlog)
            given_task.write({"display_name": "Fix 2h", "allocated_hours": 5})

        self.assertRecordValues(
            given_task | titled_task, [{"name": "Fix", "allocated_hours": 5}, {"name": "Fix", "allocated_hours": 2}]
        )

    def test_an_estimate_given_wins_over_the_title_syntax_of_hr_timesheet(self):
        if "allow_timesheets" not in self.env["project.project"]._fields:
            self.skipTest("hr_timesheet is not installed: the stand-in of the test above covers its title syntax")
        self.project.allow_timesheets = True

        given_task = self._quick_create_titled("Fix 2h", self.stage_development, task_type="bug", allocated_hours=3)
        titled_task = self._quick_create_titled("Fix 2h", self.stage_backlog)

        self.assertRecordValues(
            given_task | titled_task, [{"name": "Fix", "allocated_hours": 3}, {"name": "Fix", "allocated_hours": 2}]
        )

    def test_estimate_required_follows_the_stage_the_flag_and_the_marked_stages_of_a_saved_task(self):
        task = self.env["project.task"].create(
            {"name": "Saved Task", "project_id": self.project.id, "stage_id": self.stage_backlog.id}
        )
        self.assertFalse(task.is_estimate_required)
        task.write({"task_type": "bug", "allocated_hours": 2, "stage_id": self.stage_development.id})
        self.assertTrue(task.is_estimate_required)

        self.project.is_ai_driven = False
        self.assertFalse(task.is_estimate_required)
        self.project.is_ai_driven = True
        self.assertTrue(task.is_estimate_required)

        self.project.estimate_required_states = [Command.unlink(self.stage_development.id)]
        self.assertFalse(task.is_estimate_required)
        self.project.estimate_required_states = [Command.link(self.stage_development.id)]
        self.assertTrue(task.is_estimate_required)

    def test_estimate_not_required_on_templates(self):
        project_template = self.env["project.project"].create(
            {
                "name": "AI-driven Project Template",
                "is_template": True,
                "is_ai_driven": True,
                "type_ids": [Command.set(self.stage_development.ids)],
                "estimate_required_states": [Command.set(self.stage_development.ids)],
            },
        )
        template_tasks = self._create_template_tasks(self.stage_development, project_template)
        self.assertEqual(template_tasks.mapped("is_estimate_required"), [False] * 3)


@tagged("post_install", "-at_install")
class TestTaskQuickCreateProjectSharing(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_backlog = cls._create_stage("Backlog", 1)
        cls.stage_development = cls._create_stage("Development", 2)
        cls.project.write(
            {"privacy_visibility": "portal", "estimate_required_states": [Command.set(cls.stage_development.ids)]}
        )
        cls.plain_project = cls.env["project.project"].create(
            {"name": "Shared, not AI-driven", "privacy_visibility": "portal", "type_ids": [Command.clear()]}
        )
        cls.plain_stage = cls._create_stage("To Do", 1, project=cls.plain_project)
        # the kanban of project sharing offers its quick create to a collaborator with Advanced Edit only
        cls.user_editor = cls._create_collaborator(
            "project_ai_driven_sharing_editor", "advanced_edit", cls.project | cls.plain_project
        )
        cls.user_edit = cls._create_collaborator(
            "project_ai_driven_sharing_edit", "edit", cls.project | cls.plain_project
        )
        cls.user_viewer = cls._create_collaborator("project_ai_driven_sharing_viewer", "view", cls.project)

    def _open_quick_create(self, stage, project=None):
        """As a column of the kanban of project sharing opens it."""
        task_model = (
            self.env["project.task"]
            .with_user(self.user_editor)
            .with_context(default_project_id=(project or self.project).id, default_stage_id=stage.id)
        )
        return Form(task_model, view=SHARING_QUICK_CREATE_VIEW)

    def test_a_collaborator_quick_creates_in_a_marked_column_with_the_type_and_the_estimate(self):
        task_form = self._open_quick_create(self.stage_development)
        task_form.name = "Reported by the customer"
        for field_name in ("task_type", "allocated_hours"):
            self.assertFalse(task_form._get_modifier(field_name, "invisible"), field_name)
            self.assertTrue(task_form._get_modifier(field_name, "required"), field_name)
        with self.assertRaises(AssertionError) as missing_field:
            task_form.save()
        self.assertIn("task_type", str(missing_field.exception))
        task_form.task_type = "bug"
        with self.assertRefused(NO_ESTIMATE):
            task_form.save()

        task_form.allocated_hours = 2
        task = task_form.save()

        self.assertRecordValues(
            task.sudo(),
            [
                {
                    "create_uid": self.user_editor.id,
                    "project_id": self.project.id,
                    "stage_id": self.stage_development.id,
                    "task_type": "bug",
                    "allocated_hours": 2,
                }
            ],
        )

    def test_a_collaborator_s_task_without_them_is_refused_by_the_rule(self):
        task_model = (
            self.env["project.task"].with_user(self.user_editor).with_context(default_project_id=self.project.id)
        )
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            task_model.create({"name": "Reported by the customer", "stage_id": self.stage_development.id})

        task_form = self._open_quick_create(self.stage_backlog)
        task_form.name = "Reported later"
        self.assertFalse(task_form._get_modifier("task_type", "required"))
        self.assertRecordValues(task_form.save(), [{"stage_id": self.stage_backlog.id, "task_type": False}])

    def test_a_read_only_collaborator_still_cannot_write_them(self):
        self.task.with_user(self.user_editor).write({"task_type": "bug", "allocated_hours": 2})
        for vals in ({"task_type": "feature"}, {"allocated_hours": 3}):
            with self.subTest(vals=vals), unittest.TestCase.assertRaises(self, AccessError):
                self.task.with_user(self.user_viewer).write(vals)
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                self.env["project.task"].with_user(self.user_viewer).create(
                    {"name": "Read only", "project_id": self.project.id, **vals}
                )
        self.assertRecordValues(self.task, [{"task_type": "bug", "allocated_hours": 2}])

    def test_an_edit_collaborator_gives_them_on_an_ai_driven_project(self):
        """Odoo offers the quick create to Advanced Edit only: an Edit collaborator still writes them by a call."""
        self.task.with_user(self.user_edit).write({"task_type": "bug", "allocated_hours": 2})
        new_task = (
            self.env["project.task"]
            .with_user(self.user_edit)
            .with_context(default_project_id=self.project.id)
            .create({"name": "Reported by the customer", "task_type": "feature", "allocated_hours": 3})
        )
        self.assertRecordValues(
            self.task | new_task.sudo(),
            [{"task_type": "bug", "allocated_hours": 2}, {"task_type": "feature", "allocated_hours": 3}],
        )

    def test_no_collaborator_gives_them_on_a_project_not_ai_driven(self):
        plain_task = self.env["project.task"].create(
            {"name": "Plain Task", "project_id": self.plain_project.id, "task_type": "bug", "allocated_hours": 1}
        )
        for user in (self.user_edit, self.user_editor):
            task_model = self.env["project.task"].with_user(user).with_context(default_project_id=self.plain_project.id)
            refused_calls = {
                "write of the type": lambda: plain_task.with_user(user).write({"task_type": "feature"}),
                "write of the estimate": lambda: plain_task.with_user(user).write({"allocated_hours": 3}),
                "create with the type": lambda: task_model.create({"name": "Typed", "task_type": "feature"}),
                "create with the estimate": lambda: task_model.create({"name": "Estimated", "allocated_hours": 3}),
                "create with a default": lambda: task_model.with_context(default_allocated_hours=3).create(
                    {"name": "Estimated by default"}
                ),
                "create under a parent": lambda: task_model.with_context(default_project_id=False).create(
                    {"name": "Sub-task", "parent_id": plain_task.id, "allocated_hours": 3}
                ),
            }
            for call_name, refused_call in refused_calls.items():
                with self.subTest(user=user.login, call=call_name), self.assertRaises(AccessError) as refusal:
                    refused_call()
                self.assertIn(NOT_AI_DRIVEN, str(refusal.exception))
        self.assertRecordValues(plain_task, [{"task_type": "bug", "allocated_hours": 1}])
        self.assertEqual(self.env["project.task"].search([("project_id", "=", self.plain_project.id)]), plain_task)

    def test_the_quick_create_of_a_project_not_ai_driven_leaves_them_out(self):
        """Read-only there, so that the save, which would be refused with them, sends neither."""
        task_form = self._open_quick_create(self.plain_stage, self.plain_project)
        task_form.name = "Reported by the customer"
        for field_name in ("task_type", "allocated_hours"):
            self.assertTrue(task_form._get_modifier(field_name, "invisible"), field_name)
            self.assertTrue(task_form._get_modifier(field_name, "readonly"), field_name)
        self.assertRecordValues(
            task_form.save().sudo(),
            [{"project_id": self.plain_project.id, "task_type": False, "allocated_hours": 0}],
        )

    def test_the_sub_task_list_of_the_sharing_form_offers_the_estimate_on_an_ai_driven_project_only(self):
        if "allow_timesheets" not in self.env["project.project"]._fields:
            self.skipTest("hr_timesheet is not installed: no list of the sharing form shows the estimate")
        for project, is_ai_driven in ((self.project, True), (self.plain_project, False)):
            with self.subTest(project=project.name):
                project.allow_timesheets = True
                parent = self.env["project.task"].create({"name": "Parent", "project_id": project.id})
                with Form(parent.with_user(self.user_edit), view=SHARING_FORM_VIEW) as task_form:
                    with task_form.child_ids.new() as subtask_form:
                        subtask_form.name = "Sub-task"
                        self.assertEqual(subtask_form._get_modifier("allocated_hours", "readonly"), not is_ai_driven)
                        if is_ai_driven:
                            subtask_form.allocated_hours = 2
                self.assertRecordValues(
                    parent.child_ids, [{"name": "Sub-task", "allocated_hours": 2 if is_ai_driven else 0}]
                )


@tagged("post_install", "-at_install")
class TestTaskQuickCreateProjectSharingTour(ProjectAiDrivenCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # "New" opens the quick create in the first column: Development, as no task is left without a stage
        cls.stage_development = cls._create_stage("Development", 1)
        cls.task.stage_id = cls._create_stage("Backlog", 2)
        cls.project.privacy_visibility = "portal"
        cls.collaborator = cls._create_collaborator("project_ai_driven_sharing_tour", "advanced_edit", cls.project)

    def _start_sharing_tour(self, tour_name):
        # the refusals the tours provoke are logged by odoo.http
        with mute_logger("odoo.http"):
            self.start_tour(f"/my/projects/{self.project.id}/project_sharing", tour_name, login=self.collaborator.login)

    def test_a_refused_quick_create_shows_the_refusal_and_keeps_what_was_typed(self):
        self.project.estimate_required_states = [Command.set(self.stage_development.ids)]

        self._start_sharing_tour("project_ai_driven_sharing_quick_create_refusal")

        self.assertRecordValues(
            self.env["project.task"].search([("name", "=", "Reported without an estimate")]),
            [
                {
                    "stage_id": self.stage_development.id,
                    "task_type": "bug",
                    "allocated_hours": 2,
                    "create_uid": self.collaborator.id,
                }
            ],
        )

    def test_a_quick_create_refused_with_a_redirect_shows_its_button(self):
        projects_action = self.env.ref("project.open_view_project_all")

        def refuse_with_a_redirect(tasks, **kwargs):
            raise RedirectWarning("Pick the project in the backend.", projects_action.id, "Open the Projects")

        with patch.object(self.registry["project.task"], "_apply_estimate_rule", refuse_with_a_redirect):
            self._start_sharing_tour("project_ai_driven_sharing_quick_create_redirect")

        self.assertFalse(self.env["project.task"].search([("name", "=", "Reported elsewhere")]))

    def test_a_quick_create_failing_on_the_server_shows_a_portal_user_its_message_alone(self):
        def fail_on_the_server(tasks, **kwargs):
            raise RuntimeError("The estimate check is down.")

        with patch.object(self.registry["project.task"], "_apply_estimate_rule", fail_on_the_server):
            self._start_sharing_tour("project_ai_driven_sharing_quick_create_server_error")

        self.assertFalse(self.env["project.task"].search([("name", "=", "Reported while the server fails")]))


@tagged("post_install", "-at_install")
class TestTaskQuickCreateProjectSharingTourMobile(TestTaskQuickCreateProjectSharingTour):
    browser_size = "375x667"
    touch_enabled = True

    def test_a_refused_quick_create_shows_the_refusal_and_keeps_what_was_typed(self):
        """Declared again to run here: Odoo's test loader takes only the test methods a class
        declares itself, not those it inherits.
        """
        super().test_a_refused_quick_create_shows_the_refusal_and_keeps_what_was_typed()
