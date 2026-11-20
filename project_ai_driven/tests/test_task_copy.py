from datetime import datetime
from unittest.mock import patch

from freezegun import freeze_time

from odoo import Command
from odoo.tests.common import new_test_user

from .common import ProjectAiDrivenCommon

EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
LINE_FIELDS = {
    "checklist_item_ids": ["name", "state", "user_id", "reviewer_id", "deadline"],
    "testcase_ids": [
        "sequence",
        "name",
        "state",
        "steps_to_execute",
        "expected_results",
        "comments",
        "verified_by_id",
        "verified_on",
        "verification_kind",
        "test_reference",
    ],
    "question_ids": [
        "sequence",
        "round",
        "question",
        "addressed_to_id",
        "answer",
        "state",
        "answered_by_id",
        "answered_on",
    ],
}
# What a line of a copy from or into a template holds in place of the values of its source.
VALUES_FROM_ZERO = {
    "checklist_item_ids": {"state": "todo", "deadline": False},
    "testcase_ids": {
        "state": "not_run",
        "comments": False,
        "verified_by_id": False,
        "verified_on": False,
        "verification_kind": False,
    },
    "question_ids": {"state": "open", "answer": False, "answered_by_id": False, "answered_on": False},
}


class TestTaskCopyLines(ProjectAiDrivenCommon):
    """A task and its sub-task, each with checklist items, test cases and
    question rows in every kind of state.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.subtask = cls._create_task_with_lines("Sub-task", parent_id=cls.task.id)
        cls._create_lines(cls.task, "Task")

    @classmethod
    def _create_task_with_lines(cls, name, **values):
        task = cls.env["project.task"].create({"name": name, "project_id": cls.project.id, **values})
        cls._create_lines(task, name)
        return task

    @classmethod
    def _create_lines(cls, task, label):
        with freeze_time(EARLIER):
            cls.env["project.task.checklist.item"].with_context(mail_auto_subscribe_no_notify=True).create(
                [
                    {
                        "name": "%s item done" % label,
                        "task_id": task.id,
                        "state": "done",
                        "user_id": cls.user_dev.id,
                        "reviewer_id": cls.user_lead.id,
                        "deadline": EARLIER,
                    },
                    {"name": "%s item waiting" % label, "task_id": task.id, "state": "waiting"},
                    {"name": "%s item todo" % label, "task_id": task.id},
                ],
            )
            cls.env["project.task.testcase"].with_user(cls.user_dev).create(
                [
                    {
                        "name": "%s case passed" % label,
                        "task_id": task.id,
                        "state": "passed",
                        "steps_to_execute": "<p>Run it</p>",
                        "expected_results": "<p>It passes</p>",
                        "comments": "<p>Passed on the second run</p>",
                        "verification_kind": "automated",
                        "test_reference": "/module:TestClass.test_method",
                    },
                    {"name": "%s case failed" % label, "task_id": task.id, "state": "failed", "sequence": 5},
                    {"name": "%s case not run" % label, "task_id": task.id},
                ],
            )
            cls.env["project.task.question"].with_user(cls.user_dev).create(
                [
                    {
                        "round": label,
                        "question": "<p>Who?</p>",
                        "answer": "<p>The lead.</p>",
                        "addressed_to_id": cls.user_lead.id,
                        "task_id": task.id,
                    },
                    {"round": label, "question": "<p>When?</p>", "state": "dropped", "task_id": task.id},
                    {"round": label, "question": "<p>Where?</p>", "task_id": task.id, "sequence": 5},
                ],
            )

    def _line_values(self, tasks):
        """The lines of each task as they show, without their ids and task."""
        self.env.invalidate_all()
        return [
            {
                field_name: [
                    {name: value for name, value in line_values.items() if name != "id"}
                    for line_values in task[field_name].read(line_fields, load=None)
                ]
                for field_name, line_fields in LINE_FIELDS.items()
            }
            for task in tasks
        ]

    def assertLinesCopied(self, copied_tasks, source_values, source_tasks, copy_values=None):
        self.assertEqual(self._line_values(copied_tasks), copy_values or source_values)
        self.assertEqual(self._line_values(source_tasks), source_values, "the sources keep their lines")
        for field_name in LINE_FIELDS:
            self.assertFalse(copied_tasks[field_name] & source_tasks[field_name], field_name)

    def assertLinesCopiedFromZero(self, copied_tasks, source_values, source_tasks):
        values_from_zero = [
            {
                field_name: [{**line_values, **VALUES_FROM_ZERO[field_name]} for line_values in lines_values]
                for field_name, lines_values in task_values.items()
            }
            for task_values in source_values
        ]
        self.assertLinesCopied(copied_tasks, source_values, source_tasks, copy_values=values_from_zero)

    def test_a_duplicate_takes_the_lines_of_its_task_and_subtask_with_their_states(self):
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)
        self.assertEqual(
            {field_name: [line["state"] for line in source_values[1][field_name]] for field_name in LINE_FIELDS},
            {
                "checklist_item_ids": ["done", "waiting", "todo"],
                "testcase_ids": ["passed", "failed", "not_run"],
                "question_ids": ["open", "answered", "dropped"],
            },
            "the sub-task holds lines in every kind of state",
        )

        with freeze_time(LATER):
            task_copy = self.task.copy()

        self.assertLinesCopied(task_copy | task_copy.child_ids, source_values, source_tasks)
        comments = (task_copy | task_copy.child_ids).message_ids.filtered(
            lambda message: message.message_type == "comment"
        )
        self.assertFalse(comments, "a copied item is posted to no one")

    def test_a_project_user_duplicating_a_task_takes_its_lines_and_those_of_its_subtask(self):
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            task_copy = self.task.with_user(self.user_dev).copy()

        self.assertLinesCopied(task_copy | task_copy.child_ids, source_values, source_tasks)

    def test_a_user_who_may_not_read_the_lines_copies_none_of_them(self):
        employee = self._create_user("Employee", "project_ai_driven_employee", "employee@example.com")
        todo = self._create_task_with_lines(
            "To-do", project_id=False, user_ids=[Command.set((employee | self.user_dev).ids)]
        )
        self.assertFalse(employee.has_group("project.group_project_user"))

        # core fails such a user's whole copy of a to-do on its own project roles: copy_data is this module's part
        (employee_values,) = todo.with_user(employee).copy_data()
        (project_user_values,) = todo.with_user(self.user_dev).copy_data()

        self.assertFalse(employee_values.keys() & LINE_FIELDS.keys(), "no line field the user may not read")
        self.assertEqual(
            {field_name: len(project_user_values[field_name]) for field_name in LINE_FIELDS},
            dict.fromkeys(LINE_FIELDS, 3),
            "a project user's copy of the same to-do takes its lines",
        )

    def test_several_tasks_duplicated_together_each_take_their_own_lines(self):
        other_task = self._create_task_with_lines("Other task")
        other_subtask = self._create_task_with_lines("Other sub-task", parent_id=other_task.id)
        source_tasks = self.task | self.subtask | other_task | other_subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            task_copy, other_copy = (self.task | other_task).copy()

        self.assertLinesCopied(
            task_copy | task_copy.child_ids | other_copy | other_copy.child_ids, source_values, source_tasks
        )

    def test_a_duplicated_project_takes_the_lines_of_its_tasks_and_subtasks(self):
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            project_copy = self.project.copy()

        task_copy = project_copy.tasks.filtered(lambda task: not task.parent_id)
        self.assertLinesCopied(task_copy | task_copy.child_ids, source_values, source_tasks)

    def test_a_duplicated_task_template_takes_its_lines_and_those_of_its_subtask_with_their_states(self):
        self.task.is_template = True
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            template_copy = self.task.copy()

        self.assertTrue(template_copy.is_template)
        self.assertLinesCopied(template_copy | template_copy.child_ids, source_values, source_tasks)

    def test_a_duplicated_project_template_takes_the_lines_of_its_tasks_and_subtasks_with_their_states(self):
        self.project.is_template = True
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            project_copy = self.project.copy()

        self.assertTrue(project_copy.is_template)
        task_copy = project_copy.tasks.filtered(lambda task: not task.parent_id)
        self.assertLinesCopied(task_copy | task_copy.child_ids, source_values, source_tasks)

    def test_a_task_created_from_a_template_takes_its_lines_and_those_of_its_subtask_from_zero(self):
        self.task.is_template = True
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            task = self.env["project.task"].browse(self.task.action_create_from_template())

        self.assertFalse(task.is_template)
        self.assertLinesCopiedFromZero(task | task.child_ids, source_values, source_tasks)

    def test_a_project_created_from_a_template_takes_the_lines_of_its_tasks_and_subtasks_from_zero(self):
        self.project.is_template = True
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            project = self.project.action_create_from_template({"name": "Project From Template"})

        task = project.tasks.filtered(lambda task: not task.parent_id)
        self.assertLinesCopiedFromZero(task | task.child_ids, source_values, source_tasks)

    def test_a_template_created_from_a_project_takes_the_lines_of_its_tasks_and_subtasks_from_zero(self):
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        with freeze_time(LATER):
            action = self.project.action_create_template_from_project()

        project_template = self.env["project.project"].browse(action["params"]["project_id"])
        task = project_template.tasks.filtered(lambda task: not task.parent_id)
        self.assertLinesCopiedFromZero(task | task.child_ids, source_values, source_tasks)

    def test_a_task_converted_to_a_template_keeps_its_lines_and_those_of_its_subtask(self):
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)

        self.task.action_convert_to_template()

        self.assertTrue(self.task.is_template)
        self.assertEqual(self._line_values(source_tasks), source_values)

    def test_the_next_occurrence_of_a_recurring_task_and_its_subtask_take_items_and_cases_from_zero_and_no_rows(self):
        self.project.allow_recurring_tasks = True
        self.task.write({"recurring_task": True, "repeat_type": "forever"})
        self.assertTrue(self.task.recurrence_id)
        source_tasks = self.task | self.subtask
        source_values = self._line_values(source_tasks)
        occurrence_values = [
            {
                **{
                    field_name: [
                        {**line_values, **VALUES_FROM_ZERO[field_name]} for line_values in task_values[field_name]
                    ]
                    for field_name in ("checklist_item_ids", "testcase_ids")
                },
                "question_ids": [],
            }
            for task_values in source_values
        ]

        self.task.state = "1_done"

        next_task = self.task.recurrence_id.task_ids - self.task
        self.assertEqual(next_task.child_ids.name, self.subtask.name)
        self.assertLinesCopied(next_task | next_task.child_ids, source_values, source_tasks, occurrence_values)

    def test_the_items_of_the_next_occurrence_are_posted_to_their_assignee_and_reviewer(self):
        self.project.allow_recurring_tasks = True
        self.task.write({"recurring_task": True, "repeat_type": "forever"})

        self.task.state = "1_done"

        next_task = self.task.recurrence_id.task_ids - self.task
        item_posts = next_task.message_ids.filtered(
            lambda message: message.subtype_id == self.env.ref("project_ai_driven.checklist_subtype")
        )
        self.assertEqual(item_posts.partner_ids, (self.user_dev | self.user_lead).partner_id)

    def test_a_portal_collaborator_closing_the_task_gives_the_next_occurrence_its_items_and_cases(self):
        portal_user = new_test_user(self.env, "project_ai_driven_portal", groups="base.group_portal")
        self.project.write(
            {
                "allow_recurring_tasks": True,
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": portal_user.partner_id.id, "access_mode": "edit"})],
            }
        )
        self.task.write({"recurring_task": True, "repeat_type": "forever"})

        self.task.with_user(portal_user).state = "1_done"

        next_task = self.task.recurrence_id.task_ids - self.task
        self.assertEqual(
            [[len(task[field_name]) for field_name in LINE_FIELDS] for task in next_task | next_task.child_ids],
            [[3, 3, 0]] * 2,
        )

    def test_the_next_occurrence_of_a_recurring_task_builds_its_line_values_from_zero_only(self):
        self.project.allow_recurring_tasks = True
        self.task.write({"recurring_task": True, "repeat_type": "forever"})
        task_model = type(self.env["project.task"])

        with patch.object(
            task_model, "_get_line_copy_values", autospec=True, side_effect=task_model._get_line_copy_values
        ) as line_copy_values:
            self.task.state = "1_done"

        self.assertTrue(self.task.recurrence_id.task_ids - self.task, "the next occurrence")
        self.assertCountEqual(
            [call.args for call in line_copy_values.call_args_list],
            [(task, False, ("checklist_item_ids", "testcase_ids")) for task in self.task | self.subtask],
            "once per task, without progress or question rows",
        )

    def test_a_copy_given_lines_takes_those(self):
        task_copy = self.subtask.copy({"checklist_item_ids": [Command.create({"name": "Given item"})]})

        self.assertEqual(task_copy.checklist_item_ids.mapped("name"), ["Given item"])
        self.assertEqual(len(task_copy.testcase_ids), 3)
