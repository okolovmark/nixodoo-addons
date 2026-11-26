from ast import literal_eval
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import NO_RESOLUTION, NO_TYPE, PULL_REQUEST_MODULE, ProjectAiDrivenCommon

NO_ACTUAL_HOURS = "Enter Actual Hours above zero"
NOT_A_NUMBER = "Actual Hours are a finite number of hours, zero or more"
NOT_A_COEFFICIENT = "The Estimate Coefficient is a finite number, zero or more"
# The timesheet hours of hr_timesheet, which the module does not depend on: patched in, installed or not.
TIMESHEET_HOURS_FIELDS = ("effective_hours", "subtask_effective_hours", "total_hours_spent")


@tagged("post_install", "-at_install")
class TestActualHoursRule(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_backlog = cls._create_stage("Backlog", 1)
        cls.stage_progress = cls._create_stage("In Progress", 2)
        cls.stage_done = cls._create_stage("Done", 3, fold=True)
        cls.stage_cancelled = cls._create_stage("Canceled", 4, fold=True)
        cls.project.actual_hours_required_states = cls.stage_done
        cls.task.stage_id = cls.stage_backlog

    def _create_task(self, **values):
        return self.env["project.task"].create(
            {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_backlog.id, **values},
        )

    def _put_in_the_listed_stage(self, task):
        """As a task that sat in the stage before the project listed it."""
        self.project.actual_hours_required_states = False
        task.stage_id = self.stage_done
        self.project.actual_hours_required_states = self.stage_done

    def test_listed_stage_takes_a_task_with_actual_hours(self):
        self.task.actual_hours = 2.5
        self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "actual_hours": 2.5}])

    def test_listed_stage_refuses_a_task_without_actual_hours(self):
        with self.assertRefused(NO_ACTUAL_HOURS, "Tasks: %s" % self.task.display_name):
            self.task.stage_id = self.stage_done
        self.assertEqual(self.task.stage_id, self.stage_backlog)

    def test_timesheet_hours_and_the_estimate_do_not_count(self):
        self.task.planned_hours = 3
        # flushed before the patch: a recomputation of the timesheet fields would read the patched values
        self.env.flush_all()
        with patch.multiple(type(self.task), create=True, **dict.fromkeys(TIMESHEET_HOURS_FIELDS, 3.0)):
            with self.assertRefused(NO_ACTUAL_HOURS, self.task.name):
                self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_backlog.id, "actual_hours": 0}])

    def test_folded_stage_not_listed_takes_a_task_without_actual_hours(self):
        self.task.stage_id = self.stage_cancelled
        self.assertRecordValues(self.task, [{"stage_id": self.stage_cancelled.id, "actual_hours": 0}])

    def test_stages_not_listed_are_not_checked(self):
        self.task.stage_id = self.stage_progress
        self.assertEqual(self.task.stage_id, self.stage_progress)
        self._put_in_the_listed_stage(self.task)
        self.task.stage_id = self.stage_backlog
        self.assertRecordValues(self.task, [{"stage_id": self.stage_backlog.id, "actual_hours": 0}])

    def test_create_in_the_listed_stage(self):
        with self.assertRefused(NO_ACTUAL_HOURS, "Tasks: Created into Done"):
            self._create_task(name="Created into Done", stage_id=self.stage_done.id)
        task = self._create_task(name="Created into Done", stage_id=self.stage_done.id, actual_hours=1)
        self.assertRecordValues(task, [{"stage_id": self.stage_done.id, "actual_hours": 1}])

    def test_project_change_that_lands_in_the_listed_stage(self):
        other_project = self.env["project.project"].create(
            {"name": "Other AI-driven Project", "is_ai_driven": True, "type_ids": [Command.clear()]},
        )
        other_project.actual_hours_required_states = self._create_stage("Done", 1, project=other_project)

        with self.assertRefused(NO_ACTUAL_HOURS, self.task.name):
            self.task.project_id = other_project
        self.assertEqual(self.task.project_id, self.project)
        self.task.actual_hours = 1
        self.task.project_id = other_project

        self.assertRecordValues(
            self.task, [{"project_id": other_project.id, "stage_id": other_project.actual_hours_required_states.id}]
        )

    def test_project_change_between_flagged_projects_that_keeps_a_listed_stage(self):
        other_project = self.env["project.project"].create(
            {
                "name": "Other AI-driven Project",
                "is_ai_driven": True,
                "type_ids": [Command.set((self.stage_backlog | self.stage_done).ids)],
                "actual_hours_required_states": [Command.set(self.stage_done.ids)],
            },
        )
        timed_task = self._create_task(name="Timed Task", stage_id=self.stage_done.id, actual_hours=2)
        self._put_in_the_listed_stage(self.task)

        timed_task.project_id = other_project
        with self.assertRefused(NO_ACTUAL_HOURS, self.task.name):
            self.task.project_id = other_project

        self.assertRecordValues(
            timed_task | self.task,
            [
                {"project_id": other_project.id, "stage_id": self.stage_done.id, "actual_hours": 2},
                {"project_id": self.project.id, "stage_id": self.stage_done.id, "actual_hours": 0},
            ],
        )

    def test_project_change_to_a_project_not_flagged_is_not_checked(self):
        # listed too: only the flag keeps the task out of the rule
        plain_project = self.env["project.project"].create(
            {
                "name": "Plain Project",
                "type_ids": [Command.set((self.stage_backlog | self.stage_done).ids)],
                "actual_hours_required_states": [Command.set(self.stage_done.ids)],
            },
        )
        self._put_in_the_listed_stage(self.task)

        self.task.project_id = plain_project

        self.assertRecordValues(
            self.task, [{"project_id": plain_project.id, "stage_id": self.stage_done.id, "actual_hours": 0}]
        )

    def test_batch_create_and_write_name_only_the_task_refused(self):
        names = ["Batch Task Done after an hour", "Batch Task Done without hours"]
        with self.assertRefused(NO_ACTUAL_HOURS, "Tasks: %s" % names[1]) as refusal:
            self.env["project.task"].create(
                [
                    {
                        "name": names[0],
                        "project_id": self.project.id,
                        "stage_id": self.stage_done.id,
                        "actual_hours": 1,
                    },
                    {"name": names[1], "project_id": self.project.id, "stage_id": self.stage_done.id},
                ]
            )
        self.assertNotIn(names[0], refusal.exception.args[0])

        timed_task = self._create_task(name="Timed Task", actual_hours=1)
        with self.assertRefused(NO_ACTUAL_HOURS, "Tasks: %s" % self.task.display_name) as refusal:
            (self.task | timed_task).write({"stage_id": self.stage_done.id})
        self.assertNotIn(timed_task.display_name, refusal.exception.args[0])
        self.assertEqual((self.task | timed_task).stage_id, self.stage_backlog)

    def test_a_refused_create_names_the_task_without_the_key_it_drew(self):
        keyed_env = self.env(context=dict(self.env.context, test_project_key=True))
        keyed_project = keyed_env["project.project"].create(
            {"name": "Keyed", "key": "ZQHOURS", "is_ai_driven": True, "type_ids": [Command.clear()]}
        )
        keyed_project.actual_hours_required_states = self._create_stage("Done", 1, project=keyed_project)
        refused_creates = {
            NO_ACTUAL_HOURS: {"name": "Closed without actual hours"},
            NOT_A_NUMBER: {"name": "Closed after -1 hours", "actual_hours": -1},
            NOT_A_COEFFICIENT: {
                "name": "Estimated with a negative coefficient",
                "actual_hours": 1,
                "estimate_coefficient": -1,
            },
        }
        for requirement, vals in refused_creates.items():
            with self.subTest(requirement=requirement):
                with self.assertRefused(requirement) as refusal:
                    keyed_env["project.task"].create({"project_id": keyed_project.id, **vals})
                self.assertIn("Tasks: %s" % vals["name"], refusal.exception.args[0])

        task = keyed_env["project.task"].create({"name": "Saved", "project_id": keyed_project.id, "actual_hours": 1})
        self.assertTrue(task.key.startswith("ZQHOURS-"), "keys are on, so the refusals had a key to leave out")

    def test_edit_of_a_task_that_sits_in_the_listed_stage_is_saved(self):
        self._put_in_the_listed_stage(self.task)

        self.task.write({"name": "Renamed", "stage_id": self.stage_done.id})
        self.task.planned_hours = 4

        self.assertRecordValues(
            self.task, [{"name": "Renamed", "stage_id": self.stage_done.id, "planned_hours": 4, "actual_hours": 0}]
        )

    def test_task_moved_back_into_the_listed_stage_is_checked_again(self):
        self.task.actual_hours = 3
        self.task.stage_id = self.stage_done
        self.task.stage_id = self.stage_progress
        self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "actual_hours": 3}])

        self.task.actual_hours = 0
        self.task.stage_id = self.stage_progress
        with self.assertRefused(NO_ACTUAL_HOURS, self.task.name):
            self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_progress.id, "actual_hours": 0}])

    def test_actual_hours_cleared_in_the_listed_stage_are_saved(self):
        self.task.actual_hours = 2
        self.task.stage_id = self.stage_done
        self.task.actual_hours = 0
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "actual_hours": 0}])

    def test_actual_hours_are_a_number_zero_or_more_in_any_project(self):
        plain_project = self.env["project.project"].create({"name": "Plain Project", "type_ids": [Command.clear()]})
        for hours in (-1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(hours=hours):
                with self.assertRefused(NOT_A_NUMBER, self.task.name):
                    self.task.actual_hours = hours
                with self.assertRefused(NOT_A_NUMBER, "Plain Task"):
                    self.env["project.task"].create(
                        {"name": "Plain Task", "project_id": plain_project.id, "actual_hours": hours},
                    )

    def test_actual_hours_stored_as_nan_are_refused(self):
        """Stored by SQL, past the constraint: a NaN is not above zero."""
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET actual_hours = 'NaN' WHERE id = %s", (self.task.id,))
        self.task.invalidate_recordset(["actual_hours"])
        with self.assertRefused(NO_ACTUAL_HOURS, self.task.name):
            self.task.stage_id = self.stage_done

    def test_project_listing_no_stage_requires_nothing(self):
        self.project.actual_hours_required_states = False
        self.task.stage_id = self.stage_done
        created_task = self._create_task(name="Created into Done", stage_id=self.stage_done.id)
        self.assertRecordValues(
            self.task | created_task,
            [{"stage_id": self.stage_done.id, "actual_hours": 0, "is_actual_hours_required": False}] * 2,
        )

    def test_stage_listed_by_every_rule_refuses_for_the_actual_hours_last(self):
        if self._is_pull_request_module_installed():
            self.skipTest("%s refuses a task without pr_uri first, with its own message" % PULL_REQUEST_MODULE)
        self.project.write(
            {
                "pr_required_states": [Command.set(self.stage_done.ids)],
                "estimate_required_states": [Command.set(self.stage_done.ids)],
            }
        )
        for earlier_part, values in (
            (NO_RESOLUTION, {"resolution": "no_change"}),
            (NO_TYPE, {"task_type": "bug", "planned_hours": 2}),
        ):
            with self.subTest(earlier_part=earlier_part):
                with self.assertRefused(earlier_part) as refusal:
                    self.task.stage_id = self.stage_done
                self.assertNotIn(NO_ACTUAL_HOURS, refusal.exception.args[0])
                self.task.write(values)
        with self.assertRefused(NO_ACTUAL_HOURS):
            self.task.stage_id = self.stage_done
        self.task.actual_hours = 1
        self.task.stage_id = self.stage_done
        self.assertEqual(self.task.stage_id, self.stage_done)

    def test_project_not_flagged_requires_nothing(self):
        # listed too: only the flag keeps its tasks out of the rule
        plain_project = self.env["project.project"].create(
            {
                "name": "Plain Project",
                "type_ids": [Command.set((self.stage_backlog | self.stage_done).ids)],
                "actual_hours_required_states": [Command.set(self.stage_done.ids)],
            },
        )
        plain_task = self._create_task(name="Plain Task", project_id=plain_project.id)
        plain_task.stage_id = self.stage_done
        created_task = self._create_task(
            name="Created into Done", project_id=plain_project.id, stage_id=self.stage_done.id
        )
        self.assertRecordValues(
            plain_task | created_task,
            [{"stage_id": self.stage_done.id, "actual_hours": 0, "is_actual_hours_required": False}] * 2,
        )

    def test_is_actual_hours_required_in_the_listed_stages_of_a_flagged_project(self):
        self._put_in_the_listed_stage(self.task)
        backlog_task = self._create_task(name="Backlog Task")
        self.assertEqual((self.task | backlog_task).mapped("is_actual_hours_required"), [True, False])
        self.project.is_ai_driven = False
        self.assertFalse(self.task.is_actual_hours_required)

    def test_the_next_occurrence_and_its_sub_task_enter_a_listed_first_stage_without_actual_hours(self):
        self.project.write({"allow_recurring_tasks": True, "actual_hours_required_states": self.stage_backlog.ids})
        recurring_task = self._create_task(
            stage_id=self.stage_progress.id, actual_hours=2, recurring_task=True, repeat_unit="week", mon=True
        )
        self._create_task(name="Sub-task", parent_id=recurring_task.id, stage_id=self.stage_progress.id, actual_hours=1)

        recurring_task.recurrence_id._create_next_task()

        next_task = recurring_task.recurrence_id.task_ids - recurring_task
        self.assertEqual(next_task.child_ids.name, "Sub-task")
        self.assertRecordValues(
            next_task | next_task.child_ids, [{"stage_id": self.stage_backlog.id, "actual_hours": 0}] * 2
        )
        next_task.stage_id = self.stage_progress
        with self.assertRefused(NO_ACTUAL_HOURS):
            next_task.stage_id = self.stage_backlog

    def test_a_move_of_the_following_or_all_tasks_of_a_recurrence_checks_each_of_them(self):
        first_task = self._create_task(name="First occurrence", actual_hours=2)
        second_task = self._create_task(name="Second occurrence")
        tasks = first_task | second_task
        self.env["project.task.recurrence"].create({"mon": True, "task_ids": [Command.set(tasks.ids)]})

        for recurrence_update in ("subsequent", "all"):
            with (
                self.subTest(recurrence_update=recurrence_update),
                self.assertRefused(NO_ACTUAL_HOURS, "Tasks: %s" % second_task.display_name),
            ):
                first_task.write({"stage_id": self.stage_done.id, "recurrence_update": recurrence_update})
            self.assertEqual(tasks.stage_id, self.stage_backlog)

        second_task.actual_hours = 1
        for recurrence_update in ("subsequent", "all"):
            with self.subTest(recurrence_update=recurrence_update):
                tasks.stage_id = self.stage_backlog
                first_task.write({"stage_id": self.stage_done.id, "recurrence_update": recurrence_update})
                self.assertEqual(tasks.stage_id, self.stage_done)

    def test_copy_that_keeps_the_stage_takes_the_actual_hours_unless_its_default_sets_them(self):
        closed_task = self._create_task(name="Closed Task", stage_id=self.stage_done.id, actual_hours=2)

        copied_task = closed_task.copy({"stage_id": self.stage_done.id})
        set_copy = closed_task.copy({"stage_id": self.stage_done.id, "actual_hours": 0.5})
        plain_copy = closed_task.copy()

        self.assertRecordValues(
            copied_task | set_copy | plain_copy,
            [
                {"stage_id": self.stage_done.id, "actual_hours": 2},
                {"stage_id": self.stage_done.id, "actual_hours": 0.5},
                {"stage_id": self.stage_backlog.id, "actual_hours": 0},
            ],
        )

    def test_duplicated_project_keeps_the_actual_hours_that_let_its_tasks_in(self):
        self._create_task(name="Closed Task", stage_id=self.stage_done.id, actual_hours=2)

        duplicated_project = self.project.copy()

        self.assertEqual(duplicated_project.actual_hours_required_states, self.stage_done)
        duplicated_task = duplicated_project.tasks.filtered(lambda task: task.name == "Closed Task")
        self.assertRecordValues(duplicated_task, [{"stage_id": self.stage_done.id, "actual_hours": 2}])

    def test_duplicated_project_refuses_a_task_without_actual_hours(self):
        self._put_in_the_listed_stage(self.task)

        with self.assertRefused(NO_ACTUAL_HOURS, self.task.name):
            self.project.copy()

    def test_user_who_cannot_read_the_project(self):
        """The rule is called as the write of a task calls it. The write is left
        out: a module that restricts who moves a task may read the project.
        """
        self._put_in_the_listed_stage(self.task)
        self.project.privacy_visibility = "followers"
        self.task.user_ids = self.user_dev
        task = self.task.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            task.project_id.check_access_rule("read")

        # read again as the user: what the test itself read is in a cache he shares
        self.env.invalidate_all()
        self.assertTrue(task.is_actual_hours_required)
        with self.assertRefused(NO_ACTUAL_HOURS):
            task._apply_actual_hours_rule()
        self.task.actual_hours = 1
        self.env.invalidate_all()
        task._apply_actual_hours_rule()

    def test_estimate_coefficient_is_empty_unless_set_and_a_finite_number_zero_or_more(self):
        priced_task = self._create_task(name="Priced Task", estimate_coefficient=1.4)
        self.assertRecordValues(self.task | priced_task, [{"estimate_coefficient": 0}, {"estimate_coefficient": 1.4}])
        for coefficient in (-1, float("nan"), float("inf"), float("-inf")):
            with self.subTest(coefficient=coefficient):
                with self.assertRefused(NOT_A_COEFFICIENT, priced_task.name):
                    priced_task.estimate_coefficient = coefficient
                with self.assertRefused(NOT_A_COEFFICIENT, "Tasks: Refused Task"):
                    self._create_task(name="Refused Task", estimate_coefficient=coefficient)
        for empty_value in (0, False):
            with self.subTest(empty_value=empty_value):
                priced_task.estimate_coefficient = 1.4
                priced_task.estimate_coefficient = empty_value
                self.assertEqual(priced_task.estimate_coefficient, 0)

    def test_task_form_shows_the_actual_hours_on_flagged_projects_and_requires_them_in_listed_stages(self):
        arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (actual_hours_node,) = arch.xpath("//field[@name='actual_hours']")
        self.assertEqual(actual_hours_node.get("widget"), "float_time")
        self.assertEqual(
            literal_eval(actual_hours_node.get("attrs")),
            {"invisible": [("is_ai_driven", "=", False)], "required": [("is_actual_hours_required", "=", True)]},
        )
        self.assertTrue(arch.xpath("//field[@name='is_actual_hours_required'][@invisible='1']"))
        self.assertFalse(arch.xpath("//field[@name='estimate_coefficient']"))

    def test_project_form_shows_the_stages_as_it_shows_those_of_the_estimate_rule(self):
        arch = self.env.ref("project.edit_project")._get_combined_arch()
        estimate_rule_node = arch.xpath("//field[@name='estimate_required_states']")[0]
        actual_hours_rule_node = estimate_rule_node.getnext()
        self.assertEqual(actual_hours_rule_node.get("name"), "actual_hours_required_states")
        for attribute in ("widget", "domain", "options", "attrs"):
            self.assertEqual(actual_hours_rule_node.get(attribute), estimate_rule_node.get(attribute), attribute)
