from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged

from .common import MemberAccessCommon


@tagged("post_install", "-at_install")
class TestRecurringTasks(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.projects.allow_recurring_tasks = True

    def _create_recurring_task(self, project, assignees, **values):
        return self._create_task(
            project, user_ids=[Command.set(assignees.ids)], recurring_task=True, repeat_type="forever", **values
        )

    def _leave_private_project(self, users):
        self.private_project.member_ids.filtered(lambda member: member.user_id in users).unlink()

    def _close(self, task):
        """Set the task Done as an assignee who stays a member, and return its next occurrence."""
        task.with_user(self.editor).state = "1_done"
        return task.recurrence_id.task_ids - task

    def _close_copying_uat_users(self, task):
        """Close the task as _close does, with the UAT users among the fields its recurrence
        copies to the next occurrence, as a module adding them would: core copies none.
        """
        recurrence_model = type(self.env["project.task.recurrence"])
        fields_to_copy = recurrence_model._get_recurring_fields_to_copy
        with patch.object(
            recurrence_model,
            "_get_recurring_fields_to_copy",
            lambda recurrence: [*fields_to_copy(recurrence), "uat_user_ids"],
        ):
            return self._close(task)

    def _assert_the_next_occurrence_takes_every_assignee(self, project):
        assignees = self.reader | self.editor | self.outsider
        task = self._create_recurring_task(project, assignees)

        next_task = self._close(task)

        self.assertEqual(task.state, "1_done")
        self.assertEqual(next_task.user_ids, assignees)

    def test_the_next_occurrence_in_a_private_project_takes_the_assignees_still_members(self):
        assignees = self.reader | self.editor
        private_task = self._create_recurring_task(
            self.private_project, assignees, uat_user_ids=[Command.set((self.contributor | self.admin).ids)]
        )
        self._create_task(self.private_project, parent_id=private_task.id, user_ids=[Command.set(self.reader.ids)])
        self._leave_private_project(self.reader | self.contributor)

        next_task = self._close(private_task)

        self.assertEqual(private_task.state, "1_done")
        self.assertEqual(next_task.user_ids, self.editor)
        self.assertEqual(len(next_task.child_ids), 1)
        self.assertFalse(next_task.child_ids.user_ids)
        self.assertRecordValues(
            private_task, [{"user_ids": assignees.ids, "uat_user_ids": (self.contributor | self.admin).ids}]
        )

    def test_the_next_occurrence_in_a_public_project_takes_every_assignee(self):
        self._assert_the_next_occurrence_takes_every_assignee(self.public_project)

    def test_the_next_occurrence_in_a_standard_project_takes_every_assignee(self):
        self._assert_the_next_occurrence_takes_every_assignee(self.standard_project)

    def test_the_uat_users_another_module_copies_to_the_next_occurrence_are_kept_while_members(self):
        private_task = self._create_recurring_task(
            self.private_project, self.editor, uat_user_ids=[Command.set((self.contributor | self.admin).ids)]
        )
        self._leave_private_project(self.contributor)

        next_task = self._close_copying_uat_users(private_task)

        self.assertEqual(private_task.state, "1_done")
        self.assertEqual(next_task.uat_user_ids, self.admin)

    def test_an_archived_member_stays_a_uat_user_of_the_next_occurrence(self):
        private_task = self._create_recurring_task(
            self.private_project, self.editor, uat_user_ids=[Command.set((self.contributor | self.admin).ids)]
        )
        self.contributor.active = False

        next_task = self._close_copying_uat_users(private_task)

        self.assertEqual(private_task.state, "1_done")
        self.assertEqual(next_task.uat_user_ids, self.contributor | self.admin)
