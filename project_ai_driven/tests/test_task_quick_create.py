from odoo import Command
from odoo.tests import Form
from odoo.tests.common import tagged

from .common import NO_ESTIMATE, ProjectAiDrivenCommon

QUICK_CREATE_VIEW = "project.quick_create_task_form"


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
