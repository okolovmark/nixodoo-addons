from odoo import Command
from odoo.tests.common import Form, tagged

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
        for field_name in ("task_type", "planned_hours"):
            self.assertFalse(task_form._get_modifier(field_name, "invisible"), field_name)
            self.assertEqual(task_form._get_modifier(field_name, "required"), is_required, field_name)

    def test_marked_column_requires_the_type_and_the_estimate(self):
        task_form = self._open_quick_create(self.project, self.stage_development)
        task_form.name = "Quick Task"
        self.assertShown(task_form, is_required=True)
        with self.assertRaises(AssertionError) as missing_field:
            task_form.save()
        self.assertIn("task_type", str(missing_field.exception))

        # a required float takes zero in the web client: the rule refuses it
        task_form.task_type = "bug"
        with self.assertRefused(NO_ESTIMATE):
            task_form.save()

        task_form.planned_hours = 1.5
        task = task_form.save()
        self.assertRecordValues(
            task,
            [
                {
                    "stage_id": self.stage_development.id,
                    "task_type": "bug",
                    "planned_hours": 1.5,
                    "tshirt_estimate": "xs",
                }
            ],
        )

    def test_column_not_marked_leaves_them_optional(self):
        for stage in (self.stage_backlog, self.stage_canceled):
            with self.subTest(stage=stage.name):
                task_form = self._open_quick_create(self.project, stage)
                task_form.name = "Quick Task"
                self.assertShown(task_form, is_required=False)
                task = task_form.save()
                self.assertRecordValues(task, [{"stage_id": stage.id, "task_type": False, "planned_hours": 0}])

    def test_column_marked_for_a_resolution_too_refuses_the_save(self):
        self._skip_next_to_pull_request_module()
        task_form = self._open_quick_create(self.project, self.stage_delivered)
        task_form.name = "Quick Task"
        self.assertShown(task_form, is_required=True)
        task_form.task_type = "bug"
        task_form.planned_hours = 2
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
                for field_name in ("task_type", "planned_hours"):
                    self.assertTrue(task_form._get_modifier(field_name, "invisible"), field_name)
                    # the web client of 16 asks for a required field also while it is invisible
                    self.assertFalse(task_form._get_modifier(field_name, "required"), field_name)
                with self.assertRaises(AssertionError):
                    task_form.task_type = "bug"

        task_form = self._open_quick_create(plain_project, self.stage_development)
        task_form.name = "Quick Task"
        self.assertRecordValues(task_form.save(), [{"stage_id": self.stage_development.id, "task_type": False}])

    def test_project_user_quick_creates_in_a_marked_column(self):
        task_form = self._open_quick_create(self.project, self.stage_development, user=self.user_dev)
        task_form.name = "Quick Task of a Developer"
        self.assertShown(task_form, is_required=True)
        task_form.task_type = "feature"
        task_form.planned_hours = 4
        task = task_form.save()
        self.assertRecordValues(
            task,
            [
                {
                    "create_uid": self.user_dev.id,
                    "stage_id": self.stage_development.id,
                    "task_type": "feature",
                    "planned_hours": 4,
                }
            ],
        )

    def test_estimate_required_follows_the_stage_the_flag_and_the_marked_stages_of_a_saved_task(self):
        task = self.env["project.task"].create(
            {"name": "Saved Task", "project_id": self.project.id, "stage_id": self.stage_backlog.id}
        )
        self.assertFalse(task.is_estimate_required)
        task.write({"task_type": "bug", "planned_hours": 2, "stage_id": self.stage_development.id})
        self.assertTrue(task.is_estimate_required)

        self.project.is_ai_driven = False
        self.assertFalse(task.is_estimate_required)
        self.project.is_ai_driven = True
        self.assertTrue(task.is_estimate_required)

        self.project.estimate_required_states = [Command.unlink(self.stage_development.id)]
        self.assertFalse(task.is_estimate_required)
        self.project.estimate_required_states = [Command.link(self.stage_development.id)]
        self.assertTrue(task.is_estimate_required)
