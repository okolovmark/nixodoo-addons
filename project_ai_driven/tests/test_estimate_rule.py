from lxml import etree

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form

from .common import ProjectAiDrivenCommon

NO_TYPE = "Choose a Task Type"
NO_ESTIMATE = "Enter an Estimate above zero hours"
NOT_A_NUMBER = "The Estimate is a finite number of hours"
NO_RESOLUTION = "Choose a Resolution"


class TestEstimateRule(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_backlog = cls._create_stage("Backlog", 1)
        cls.stage_progress = cls._create_stage("In Progress", 2)
        cls.stage_cancelled = cls._create_stage("Cancelled", 3, fold=True)
        cls.project.estimate_required_states = cls.stage_progress
        cls.task.stage_id = cls.stage_backlog

    def _create_task(self, **values):
        return self.env["project.task"].create(
            {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_backlog.id, **values},
        )

    def _put_in_the_marked_stage(self, task):
        """As a task that sat in the stage before the project marked it."""
        self.project.estimate_required_states = False
        task.stage_id = self.stage_progress
        self.project.estimate_required_states = self.stage_progress

    def test_stage_not_marked_takes_a_task_without_type_or_estimate(self):
        task = self._create_task()
        self.assertRecordValues(task, [{"stage_id": self.stage_backlog.id, "task_type": False, "allocated_hours": 0}])

    def test_move_to_a_stage_not_marked_is_not_checked(self):
        self.task.stage_id = self.stage_cancelled
        self.assertEqual(self.task.stage_id, self.stage_cancelled)

        self._put_in_the_marked_stage(self.task)
        self.task.stage_id = self.stage_cancelled
        self.assertRecordValues(
            self.task,
            [{"stage_id": self.stage_cancelled.id, "task_type": False, "allocated_hours": 0}],
        )

    def test_marked_stage_takes_a_task_only_with_type_and_estimate(self):
        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.task.stage_id = self.stage_progress
        self.assertEqual(self.task.stage_id, self.stage_backlog)

        self.task.write({"task_type": "bug", "allocated_hours": 0.5})
        self.task.stage_id = self.stage_progress
        self.assertEqual(self.task.stage_id, self.stage_progress)

    def test_refusal_names_only_what_is_missing(self):
        self.task.task_type = "bug"
        with self.assertRaises(ValidationError) as refusal:
            self.task.stage_id = self.stage_progress
        self.assertIn(NO_ESTIMATE, refusal.exception.args[0])
        self.assertNotIn(NO_TYPE, refusal.exception.args[0])

        self.task.write({"task_type": False, "allocated_hours": 3})
        with self.assertRaises(ValidationError) as refusal:
            self.task.stage_id = self.stage_progress
        self.assertIn(NO_TYPE, refusal.exception.args[0])
        self.assertNotIn(NO_ESTIMATE, refusal.exception.args[0])

    def test_estimate_of_zero_or_less_is_refused(self):
        self.task.task_type = "bug"
        for hours in (0, -2):
            self.task.allocated_hours = hours
            with self.assertRefused(NO_ESTIMATE, self.task.name):
                self.task.stage_id = self.stage_progress

    def test_estimate_stored_as_nan_is_refused(self):
        """Stored by SQL, which the constraint does not see: a NaN is not above zero."""
        self.task.task_type = "bug"
        # tracked before the SQL: the tracking of the hours cannot format a NaN
        self._flush_tracking()
        self.env.cr.execute("UPDATE project_task SET allocated_hours = 'NaN' WHERE id = %s", (self.task.id,))
        self.task.invalidate_recordset(["allocated_hours"])
        with self.assertRefused(NO_ESTIMATE, self.task.name):
            self.task.stage_id = self.stage_progress

    def test_estimate_must_be_a_number_in_any_project(self):
        plain_project = self.env["project.project"].create({"name": "Plain Project", "type_ids": [Command.clear()]})
        for hours in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(hours=hours):
                with self.assertRefused(NOT_A_NUMBER, self.task.name):
                    self.task.allocated_hours = hours
                with self.assertRefused(NOT_A_NUMBER, "Plain Task"):
                    self.env["project.task"].create(
                        {"name": "Plain Task", "project_id": plain_project.id, "allocated_hours": hours},
                    )

    def test_type_and_estimate_arrive_with_the_stage(self):
        with self.assertRefused(NO_TYPE):
            self.task.write({"stage_id": self.stage_progress.id, "allocated_hours": 3})
        self.task.write({"stage_id": self.stage_progress.id, "task_type": "feature", "allocated_hours": 3})
        self.assertRecordValues(self.task, [{"stage_id": self.stage_progress.id, "tshirt_estimate": "s"}])

    def test_create_in_a_marked_stage(self):
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            self._create_task(stage_id=self.stage_progress.id)
        with self.assertRefused(NO_ESTIMATE):
            self._create_task(stage_id=self.stage_progress.id, task_type="bug", tshirt_estimate="m")
        task = self._create_task(
            stage_id=self.stage_progress.id, task_type="bug", allocated_hours=3, tshirt_estimate="xxl"
        )
        self.assertRecordValues(task, [{"stage_id": self.stage_progress.id, "tshirt_estimate": "s"}])

    def test_create_with_an_estimate_and_no_type_names_the_type_only(self):
        with self.assertRaises(ValidationError) as refusal:
            self._create_task(stage_id=self.stage_progress.id, allocated_hours=3)
        self.assertIn(NO_TYPE, refusal.exception.args[0])
        self.assertNotIn(NO_ESTIMATE, refusal.exception.args[0])

    def test_stage_marked_for_both_rules_refuses_by_the_closing_rule_first(self):
        self.project.pr_required_states = self.stage_progress
        with self.assertRefused(NO_RESOLUTION):
            self._create_task(stage_id=self.stage_progress.id)
        with self.assertRefused(NO_RESOLUTION, self.task.name):
            self.task.stage_id = self.stage_progress

    def test_create_without_a_stage_in_a_project_whose_default_stage_is_marked(self):
        self.project.estimate_required_states = self.stage_backlog
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            self.env["project.task"].create({"name": "Task", "project_id": self.project.id})
        task = self.env["project.task"].create(
            {"name": "Task", "project_id": self.project.id, "task_type": "bug", "allocated_hours": 3},
        )
        self.assertEqual(task.stage_id, self.stage_backlog)

    def test_next_task_of_a_recurrence_takes_the_type_into_a_marked_first_stage(self):
        """The next occurrence is built from a copy of the task it repeats,
        which carries the type and the estimate.
        """
        self.project.write({"allow_recurring_tasks": True, "estimate_required_states": self.stage_backlog.ids})
        recurring_task = self._create_task(
            name="Recurring Task",
            task_type="support",
            allocated_hours=3,
            recurring_task=True,
            repeat_type="forever",
        )

        recurring_task.state = "1_done"

        next_task = recurring_task.recurrence_id.task_ids - recurring_task
        self.assertRecordValues(
            next_task,
            [{"stage_id": self.stage_backlog.id, "task_type": "support", "allocated_hours": 3, "tshirt_estimate": "s"}],
        )

    def test_recurring_task_set_done_is_refused_when_its_next_task_misses_the_type_or_estimate(self):
        """The next occurrence starts in the first stage, which is marked, with
        what the task it repeats has: the rule refuses it, and the task stays open.
        """
        self.project.write({"allow_recurring_tasks": True, "estimate_required_states": self.stage_backlog.ids})
        for missing_part, values in ((NO_ESTIMATE, {"task_type": "support"}), (NO_TYPE, {"allocated_hours": 3})):
            with self.subTest(missing_part=missing_part):
                recurring_task = self._create_task(
                    stage_id=self.stage_progress.id, recurring_task=True, repeat_type="forever", **values
                )

                with self.assertRefused(missing_part):
                    recurring_task.state = "1_done"

                self.assertRecordValues(
                    recurring_task, [{"stage_id": self.stage_progress.id, "state": "01_in_progress"}]
                )
                self.assertEqual(recurring_task.recurrence_id.task_ids, recurring_task)

    def test_project_change_that_lands_in_a_marked_stage(self):
        other_project = self.env["project.project"].create(
            {"name": "Other AI-driven Project", "is_ai_driven": True, "type_ids": [Command.clear()]},
        )
        other_project.estimate_required_states = self._create_stage("Development", 1, project=other_project)

        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.task.project_id = other_project
        self.task.write({"task_type": "bug", "allocated_hours": 3})
        self.task.project_id = other_project

        self.assertEqual(self.task.stage_id, other_project.estimate_required_states)

    def test_edit_of_a_task_that_sits_in_a_marked_stage_is_saved(self):
        self._put_in_the_marked_stage(self.task)

        self.task.write({"name": "Renamed", "stage_id": self.stage_progress.id})
        self.task.priority_level = "3"

        self.assertRecordValues(
            self.task,
            [{"name": "Renamed", "priority_level": "3", "task_type": False, "allocated_hours": 0}],
        )

    def test_project_not_flagged_is_not_checked(self):
        self.project.is_ai_driven = False
        self.task.stage_id = self.stage_progress
        self.assertRecordValues(self.task, [{"stage_id": self.stage_progress.id, "task_type": False}])

    def test_several_tasks_in_one_write(self):
        estimated_task = self._create_task(name="Estimated Task", task_type="bug", allocated_hours=3)
        typed_task = self._create_task(name="Typed Task", task_type="bug")
        with self.assertRaises(ValidationError) as refusal:
            (self.task | estimated_task | typed_task).write({"stage_id": self.stage_progress.id})
        expected_refusal = (
            "Choose a Task Type: a task enters this stage only with one.\n"
            "Tasks: %(task)s\n\n"
            "Enter an Estimate above zero hours: a task enters this stage only with one.\n"
            "Tasks: %(task)s, %(typed_task)s"
        )
        self.assertEqual(
            refusal.exception.args[0],
            expected_refusal % {"task": self.task.display_name, "typed_task": typed_task.display_name},
        )

    def test_one_write_over_tasks_of_two_projects_names_those_of_the_flagged_one(self):
        plain_project = self.env["project.project"].create(
            {
                "name": "Plain Project",
                "is_ai_driven": False,
                "type_ids": [Command.set((self.stage_backlog | self.stage_progress).ids)],
                # marked too: only the flag keeps its task out of the rule
                "estimate_required_states": [Command.set(self.stage_progress.ids)],
            },
        )
        plain_task = self._create_task(name="Task of the plain project", project_id=plain_project.id)
        tasks = self.task | plain_task

        with self.assertRaises(ValidationError) as refusal:
            tasks.write({"stage_id": self.stage_progress.id})
        self.assertIn(self.task.display_name, refusal.exception.args[0])
        self.assertNotIn(plain_task.display_name, refusal.exception.args[0])

        self.task.write({"task_type": "bug", "allocated_hours": 3})
        tasks.write({"stage_id": self.stage_progress.id})
        self.assertEqual(tasks.stage_id, self.stage_progress)
        self.assertRecordValues(plain_task, [{"task_type": False, "allocated_hours": 0}])

    def test_user_who_cannot_read_the_project(self):
        """The rule is called as the write of a task calls it. The write is left
        out: a module that restricts who moves a task may read the project.
        """
        self._put_in_the_marked_stage(self.task)
        self.project.privacy_visibility = "followers"
        self.task.user_ids = self.user_dev
        task = self.task.with_user(self.user_dev)
        task.check_access("write")
        self.assertFalse(task.project_id.has_access("read"))

        # read again as the user: what the test itself read is in a cache he shares
        self.env.invalidate_all()
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            task._apply_estimate_rule()
        self.task.write({"task_type": "bug", "allocated_hours": 3})
        self.env.invalidate_all()
        task._apply_estimate_rule()

    def test_context_does_not_skip_the_rule(self):
        task = self.task.sudo().with_context(tracking_disable=True, mail_notrack=True, install_mode=True)
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            task.stage_id = self.stage_progress

    def test_duplicated_project_keeps_what_let_its_tasks_in(self):
        self._create_task(name="Estimated Task", stage_id=self.stage_progress.id, task_type="bug", allocated_hours=3)

        duplicated_project = self.project.copy()

        self.assertEqual(duplicated_project.estimate_required_states, self.stage_progress)
        duplicated_task = duplicated_project.tasks.filtered(lambda task: task.name == "Estimated Task")
        self.assertRecordValues(
            duplicated_task,
            [{"stage_id": self.stage_progress.id, "task_type": "bug", "allocated_hours": 3, "tshirt_estimate": "s"}],
        )

    def test_duplicated_project_refuses_a_task_without_type_or_estimate(self):
        self._put_in_the_marked_stage(self.task)

        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.project.copy()

    def test_template_tasks_enter_a_marked_stage_without_type_or_estimate(self):
        project_template = self.env["project.project"].create(
            {
                "name": "AI-driven Project Template",
                "is_template": True,
                "is_ai_driven": True,
                "type_ids": [Command.set((self.stage_backlog | self.stage_progress).ids)],
                "estimate_required_states": [Command.set(self.stage_progress.ids)],
            },
        )
        template_tasks = self._create_template_tasks(self.stage_progress, project_template)

        template_tasks.stage_id = self.stage_backlog
        template_tasks.stage_id = self.stage_progress

        self.assertRecordValues(
            template_tasks,
            [{"stage_id": self.stage_progress.id, "task_type": False, "allocated_hours": 0}] * 3,
        )

    def test_project_form_shows_the_stages_on_flagged_projects_only(self):
        # never saved: the project form may require fields of other modules
        project_form = Form(self.project)
        project_form.estimate_required_states.add(self.stage_backlog)
        self.assertEqual(project_form.estimate_required_states[:], self.stage_progress | self.stage_backlog)
        self.project.is_ai_driven = False
        with self.assertRaises(AssertionError):
            Form(self.project).estimate_required_states.add(self.stage_cancelled)

    def test_project_form_shows_the_stages_as_it_shows_those_of_the_closing_rule(self):
        arch = etree.fromstring(self.env["project.project"].get_view(view_type="form")["arch"])
        closing_rule_setting = arch.xpath("//setting[@id='pr_required_states_setting']")[0]
        estimate_rule_setting = closing_rule_setting.getnext()
        self.assertEqual(estimate_rule_setting.get("id"), "estimate_required_states_setting")
        self.assertEqual(estimate_rule_setting.getparent().get("name"), "group_tasks_managment")
        self.assertEqual(estimate_rule_setting.get("invisible"), closing_rule_setting.get("invisible"))
        closing_rule_node = closing_rule_setting.xpath("field[@name='pr_required_states']")[0]
        estimate_rule_node = estimate_rule_setting.xpath("field[@name='estimate_required_states']")[0]
        for attribute in ("widget", "domain", "options"):
            self.assertEqual(estimate_rule_node.get(attribute), closing_rule_node.get(attribute), attribute)
