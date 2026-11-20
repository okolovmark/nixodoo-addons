from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import Form, tagged

from ..models.project_task_recurrence import is_next_occurrence_creation
from .common import NO_ESTIMATE, NO_TYPE, ProjectAiDrivenCommon

NOT_A_NUMBER = "The Estimate is a finite number of hours"


@tagged("post_install", "-at_install")
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
        self.assertRecordValues(task, [{"stage_id": self.stage_backlog.id, "task_type": False, "planned_hours": 0}])

    def test_move_to_a_stage_not_marked_is_not_checked(self):
        self.task.stage_id = self.stage_cancelled
        self.assertEqual(self.task.stage_id, self.stage_cancelled)

        self._put_in_the_marked_stage(self.task)
        self.task.stage_id = self.stage_cancelled
        self.assertRecordValues(
            self.task,
            [{"stage_id": self.stage_cancelled.id, "task_type": False, "planned_hours": 0}],
        )

    def test_marked_stage_takes_a_task_only_with_type_and_estimate(self):
        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.task.stage_id = self.stage_progress
        self.assertEqual(self.task.stage_id, self.stage_backlog)

        self.task.write({"task_type": "bug", "planned_hours": 0.5})
        self.task.stage_id = self.stage_progress
        self.assertEqual(self.task.stage_id, self.stage_progress)

    def test_refusal_names_only_what_is_missing(self):
        self.task.task_type = "bug"
        with self.assertRaises(ValidationError) as refusal:
            self.task.stage_id = self.stage_progress
        self.assertIn(NO_ESTIMATE, refusal.exception.args[0])
        self.assertNotIn(NO_TYPE, refusal.exception.args[0])

        self.task.write({"task_type": False, "planned_hours": 3})
        with self.assertRaises(ValidationError) as refusal:
            self.task.stage_id = self.stage_progress
        self.assertIn(NO_TYPE, refusal.exception.args[0])
        self.assertNotIn(NO_ESTIMATE, refusal.exception.args[0])

    def test_estimate_of_zero_or_less_is_refused(self):
        self.task.task_type = "bug"
        for hours in (0, -2):
            self.task.planned_hours = hours
            with self.assertRefused(NO_ESTIMATE, self.task.name):
                self.task.stage_id = self.stage_progress

    def test_estimate_stored_as_nan_is_refused(self):
        """Stored by SQL, as before the Estimate had to be a number: a NaN is not above zero."""
        self.task.task_type = "bug"
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET planned_hours = 'NaN' WHERE id = %s", (self.task.id,))
        self.task.invalidate_recordset(["planned_hours"])
        with self.assertRefused(NO_ESTIMATE, self.task.name):
            self.task.stage_id = self.stage_progress

    def test_estimate_must_be_a_number_in_any_project(self):
        plain_project = self.env["project.project"].create({"name": "Plain Project", "type_ids": [Command.clear()]})
        for hours in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(hours=hours):
                with self.assertRefused(NOT_A_NUMBER, self.task.name):
                    self.task.planned_hours = hours
                with self.assertRefused(NOT_A_NUMBER, "Plain Task"):
                    self.env["project.task"].create(
                        {"name": "Plain Task", "project_id": plain_project.id, "planned_hours": hours},
                    )

    def test_type_and_estimate_arrive_with_the_stage(self):
        with self.assertRefused(NO_TYPE):
            self.task.write({"stage_id": self.stage_progress.id, "planned_hours": 3})
        self.task.write({"stage_id": self.stage_progress.id, "task_type": "feature", "planned_hours": 3})
        self.assertRecordValues(self.task, [{"stage_id": self.stage_progress.id, "tshirt_estimate": "s"}])

    def test_create_in_a_marked_stage(self):
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            self._create_task(stage_id=self.stage_progress.id)
        with self.assertRefused(NO_ESTIMATE):
            self._create_task(stage_id=self.stage_progress.id, task_type="bug", tshirt_estimate="m")
        task = self._create_task(
            stage_id=self.stage_progress.id, task_type="bug", planned_hours=3, tshirt_estimate="xxl"
        )
        self.assertRecordValues(task, [{"stage_id": self.stage_progress.id, "tshirt_estimate": "s"}])

    def test_create_with_an_estimate_and_no_type_names_the_type_only(self):
        with self.assertRaises(ValidationError) as refusal:
            self._create_task(stage_id=self.stage_progress.id, planned_hours=3)
        self.assertIn(NO_TYPE, refusal.exception.args[0])
        self.assertNotIn(NO_ESTIMATE, refusal.exception.args[0])

    def test_create_without_a_stage_in_a_project_whose_default_stage_is_marked(self):
        self.project.estimate_required_states = self.stage_backlog
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            self.env["project.task"].create({"name": "Task", "project_id": self.project.id})
        task = self.env["project.task"].create(
            {"name": "Task", "project_id": self.project.id, "task_type": "bug", "planned_hours": 3},
        )
        self.assertEqual(task.stage_id, self.stage_backlog)

    def test_next_task_of_a_recurrence_takes_the_type_into_a_marked_first_stage(self):
        self.project.write({"allow_recurring_tasks": True, "estimate_required_states": self.stage_backlog.ids})
        recurring_task = self._create_task(
            name="Recurring Task",
            task_type="support",
            planned_hours=3,
            recurring_task=True,
            repeat_unit="week",
            mon=True,
        )

        recurring_task.recurrence_id._create_next_task()

        next_task = recurring_task.recurrence_id.task_ids - recurring_task
        self.assertRecordValues(
            next_task,
            [{"stage_id": self.stage_backlog.id, "task_type": "support", "planned_hours": 3, "tshirt_estimate": "s"}],
        )

    def test_the_next_occurrence_and_its_sub_task_enter_a_marked_first_stage_without_the_type_or_estimate(self):
        self.project.write({"allow_recurring_tasks": True, "estimate_required_states": self.stage_backlog.ids})
        for missing_part, values in ((NO_ESTIMATE, {"task_type": "support"}), (NO_TYPE, {"planned_hours": 3})):
            with self.subTest(missing_part=missing_part):
                recurring_task = self._create_task(
                    stage_id=self.stage_progress.id, recurring_task=True, repeat_unit="week", mon=True, **values
                )
                self._create_task(name="Sub-task", parent_id=recurring_task.id, stage_id=self.stage_progress.id)

                recurring_task.recurrence_id._create_next_task()

                next_task = recurring_task.recurrence_id.task_ids - recurring_task
                self.assertRecordValues(
                    next_task | next_task.child_ids,
                    [
                        {"stage_id": self.stage_backlog.id, "task_type": False, "planned_hours": 0, **values},
                        {"stage_id": self.stage_backlog.id, "task_type": False, "planned_hours": 0},
                    ],
                )
                next_task.stage_id = self.stage_progress
                with self.assertRefused(missing_part):
                    next_task.stage_id = self.stage_backlog

    def test_the_cron_creates_each_due_occurrence_and_moves_its_date_outside_the_exemption(self):
        self.env.user.groups_id += self.env.ref("project.group_project_recurring_tasks")
        self.project.write({"allow_recurring_tasks": True, "estimate_required_states": self.stage_backlog.ids})
        recurring_tasks = self._create_task(
            name="Estimated", task_type="support", planned_hours=3, stage_id=self.stage_progress.id
        ) | self._create_task(name="Not estimated", stage_id=self.stage_progress.id)
        recurring_tasks.write({"recurring_task": True, "repeat_unit": "week", "mon": True})
        recurrences = recurring_tasks.recurrence_id
        today = fields.Date.today()
        recurrences.next_recurrence_date = today
        self.env.flush_all()
        # the cron runs every due recurrence of the database: these two only
        self.env.cr.execute(
            "UPDATE project_task_recurrence SET next_recurrence_date = NULL"
            " WHERE next_recurrence_date <= %s AND id NOT IN %s",
            (today, tuple(recurrences.ids)),
        )
        recurrence_model = type(self.env["project.task.recurrence"])

        with patch.object(
            recurrence_model,
            "_set_next_recurrence_date",
            autospec=True,
            side_effect=recurrence_model._set_next_recurrence_date,
        ) as next_date_setting:
            self.env["project.task.recurrence"]._cron_create_recurring_tasks()

        self.assertEqual([len(recurrence.task_ids) for recurrence in recurrences], [2, 2])
        self.assertTrue(all(recurrence.next_recurrence_date > today for recurrence in recurrences))
        self.assertEqual(
            [is_next_occurrence_creation(call.args[0].env) for call in next_date_setting.call_args_list], [False]
        )

    def test_no_value_a_caller_can_send_exempts_a_task_from_the_rule(self):
        for value in (True, 1, "1"):
            with self.subTest(value=value), self.assertRefused(NO_TYPE, NO_ESTIMATE):
                self.env["project.task"].with_context(project_ai_driven_next_occurrence=value).create(
                    {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_progress.id}
                )

    def test_a_move_of_all_tasks_of_a_recurrence_checks_each_of_them(self):
        first_task = self._create_task(name="First occurrence", task_type="support", planned_hours=3)
        second_task = self._create_task(name="Second occurrence")
        self.env["project.task.recurrence"].create(
            {"mon": True, "task_ids": [Command.set((first_task | second_task).ids)]}
        )

        with self.assertRefused(NO_TYPE, NO_ESTIMATE, "Tasks: %s" % second_task.display_name):
            first_task.write({"stage_id": self.stage_progress.id, "recurrence_update": "all"})
        self.assertEqual((first_task | second_task).stage_id, self.stage_backlog)

        second_task.write({"task_type": "bug", "planned_hours": 2})
        first_task.write({"stage_id": self.stage_progress.id, "recurrence_update": "all"})
        self.assertEqual((first_task | second_task).stage_id, self.stage_progress)

    def test_project_change_that_lands_in_a_marked_stage(self):
        other_project = self.env["project.project"].create(
            {"name": "Other AI-driven Project", "is_ai_driven": True, "type_ids": [Command.clear()]},
        )
        other_project.estimate_required_states = self._create_stage("Development", 1, project=other_project)

        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.task.project_id = other_project
        self.task.write({"task_type": "bug", "planned_hours": 3})
        self.task.project_id = other_project

        self.assertEqual(self.task.stage_id, other_project.estimate_required_states)

    def test_project_change_into_a_stage_the_new_project_marks(self):
        plain_project = self.env["project.project"].create(
            {"name": "Plain Project", "type_ids": [Command.set((self.stage_backlog | self.stage_progress).ids)]},
        )
        task = self._create_task(name="Plain Task", project_id=plain_project.id, stage_id=self.stage_progress.id)

        with self.assertRefused(NO_TYPE, NO_ESTIMATE, "Plain Task"):
            task.project_id = self.project
        self.assertEqual(task.project_id, plain_project)
        task.write({"task_type": "bug", "planned_hours": 3})
        task.project_id = self.project

        self.assertRecordValues(task, [{"project_id": self.project.id, "stage_id": self.stage_progress.id}])

    def test_project_change_between_flagged_projects_that_keeps_a_marked_stage(self):
        other_project = self.env["project.project"].create(
            {
                "name": "Other AI-driven Project",
                "is_ai_driven": True,
                "type_ids": [Command.set((self.stage_backlog | self.stage_progress).ids)],
                "estimate_required_states": [Command.set(self.stage_progress.ids)],
            },
        )
        estimated_task = self._create_task(
            name="Estimated Task", stage_id=self.stage_progress.id, task_type="bug", planned_hours=3
        )
        self._put_in_the_marked_stage(self.task)

        estimated_task.project_id = other_project
        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.task.project_id = other_project

        self.assertRecordValues(
            estimated_task | self.task,
            [
                {"project_id": other_project.id, "stage_id": self.stage_progress.id},
                {"project_id": self.project.id, "stage_id": self.stage_progress.id},
            ],
        )

    def test_project_change_to_a_project_not_flagged_is_not_checked(self):
        # marked too: only the flag keeps the task out of the rule
        plain_project = self.env["project.project"].create(
            {
                "name": "Plain Project",
                "type_ids": [Command.set((self.stage_backlog | self.stage_progress).ids)],
                "estimate_required_states": [Command.set(self.stage_progress.ids)],
            },
        )
        self._put_in_the_marked_stage(self.task)

        self.task.project_id = plain_project

        self.assertRecordValues(
            self.task,
            [{"project_id": plain_project.id, "stage_id": self.stage_progress.id, "task_type": False}],
        )

    def test_project_written_with_the_same_project_is_not_checked(self):
        self._put_in_the_marked_stage(self.task)

        self.task.write({"project_id": self.project.id})
        self.task.write({"project_id": self.project.id, "stage_id": self.stage_progress.id, "name": "Renamed"})

        self.assertRecordValues(
            self.task, [{"name": "Renamed", "stage_id": self.stage_progress.id, "task_type": False, "planned_hours": 0}]
        )

    def test_edit_of_a_task_that_sits_in_a_marked_stage_is_saved(self):
        self._put_in_the_marked_stage(self.task)

        self.task.write({"name": "Renamed", "stage_id": self.stage_progress.id})
        self.task.priority_level = "3"

        self.assertRecordValues(
            self.task,
            [{"name": "Renamed", "priority_level": "3", "task_type": False, "planned_hours": 0}],
        )

    def test_project_not_flagged_is_not_checked(self):
        self.project.is_ai_driven = False
        self.task.stage_id = self.stage_progress
        self.assertRecordValues(self.task, [{"stage_id": self.stage_progress.id, "task_type": False}])

    def test_several_tasks_in_one_write(self):
        estimated_task = self._create_task(name="Estimated Task", task_type="bug", planned_hours=3)
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

        self.task.write({"task_type": "bug", "planned_hours": 3})
        tasks.write({"stage_id": self.stage_progress.id})
        self.assertEqual(tasks.stage_id, self.stage_progress)
        self.assertRecordValues(plain_task, [{"task_type": False, "planned_hours": 0}])

    def test_user_who_cannot_read_the_project(self):
        """The rule is called as the write of a task calls it. The write is left
        out: a module that restricts who moves a task may read the project.
        """
        self._put_in_the_marked_stage(self.task)
        self.project.privacy_visibility = "followers"
        self.task.user_ids = self.user_dev
        task = self.task.with_user(self.user_dev)
        task.check_access_rights("write")
        task.check_access_rule("write")
        with self.assertRaises(AccessError):
            task.project_id.check_access_rule("read")

        # read again as the user: what the test itself read is in a cache he shares
        self.env.invalidate_all()
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            task._apply_estimate_rule()
        self.task.write({"task_type": "bug", "planned_hours": 3})
        self.env.invalidate_all()
        task._apply_estimate_rule()

    def test_context_does_not_skip_the_rule(self):
        task = self.task.sudo().with_context(tracking_disable=True, mail_notrack=True, install_mode=True)
        with self.assertRefused(NO_TYPE, NO_ESTIMATE):
            task.stage_id = self.stage_progress

    def test_duplicated_project_keeps_what_let_its_tasks_in(self):
        self._create_task(name="Estimated Task", stage_id=self.stage_progress.id, task_type="bug", planned_hours=3)

        duplicated_project = self.project.copy()

        self.assertEqual(duplicated_project.estimate_required_states, self.stage_progress)
        duplicated_task = duplicated_project.tasks.filtered(lambda task: task.name == "Estimated Task")
        self.assertRecordValues(
            duplicated_task,
            [{"stage_id": self.stage_progress.id, "task_type": "bug", "planned_hours": 3, "tshirt_estimate": "s"}],
        )

    def test_duplicated_project_refuses_a_task_without_type_or_estimate(self):
        self._put_in_the_marked_stage(self.task)

        with self.assertRefused(NO_TYPE, NO_ESTIMATE, self.task.name):
            self.project.copy()

    def test_project_form_shows_the_stages_on_flagged_projects_only(self):
        # never saved: the project form may require fields of other modules
        project_form = Form(self.project)
        project_form.estimate_required_states.add(self.stage_backlog)
        self.assertEqual(project_form.estimate_required_states[:], self.stage_progress | self.stage_backlog)
        self.project.is_ai_driven = False
        with self.assertRaises(AssertionError):
            Form(self.project).estimate_required_states.add(self.stage_cancelled)

    def test_project_form_shows_the_stages_as_it_shows_those_of_the_closing_rule(self):
        arch = self.env.ref("project.edit_project")._get_combined_arch()
        closing_rule_node = arch.xpath("//field[@name='pr_required_states']")[0]
        estimate_rule_node = closing_rule_node.getnext()
        self.assertEqual(estimate_rule_node.get("name"), "estimate_required_states")
        for attribute in ("widget", "domain", "options", "attrs"):
            self.assertEqual(estimate_rule_node.get(attribute), closing_rule_node.get(attribute), attribute)
