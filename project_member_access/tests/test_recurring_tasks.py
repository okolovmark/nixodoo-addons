from odoo import Command, fields
from odoo.tests.common import tagged

from .common import MemberAccessCommon


@tagged("post_install", "-at_install")
class TestRecurringTasks(MemberAccessCommon):
    def _run_cron_on(self, recurrences):
        """Run the recurrence cron with recurrences due, and no other recurrence of the database."""
        self.env.user.groups_id += self.env.ref("project.group_project_recurring_tasks")
        today = fields.Date.today()
        recurrences.next_recurrence_date = today
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task_recurrence SET next_recurrence_date = NULL"
            " WHERE next_recurrence_date <= %s AND id NOT IN %s",
            (today, tuple(recurrences.ids)),
        )
        self.env["project.task.recurrence"]._cron_create_recurring_tasks()

    def test_the_next_task_in_a_private_project_takes_the_assignees_still_members(self):
        assignees = self.reader | self.editor
        recurring_tasks = (
            self._create_task(self.private_project, user_ids=[Command.set(assignees.ids)])
            | self._create_task(self.public_project, user_ids=[Command.set((assignees | self.outsider).ids)])
            | self._create_task(self.standard_project, user_ids=[Command.set((assignees | self.outsider).ids)])
        )
        self._create_task(
            self.private_project, parent_id=recurring_tasks[0].id, user_ids=[Command.set(self.reader.ids)]
        )
        recurring_tasks.write({"recurring_task": True, "repeat_unit": "week", "mon": True})
        self.private_project.member_ids.filtered(lambda member: member.user_id == self.reader).unlink()

        self._run_cron_on(recurring_tasks.recurrence_id)

        next_tasks = recurring_tasks.recurrence_id.task_ids - recurring_tasks
        self.assertEqual(next_tasks.project_id, self.projects)
        self.assertEqual(
            [next_tasks.filtered(lambda task: task.project_id == project).user_ids for project in self.projects],
            [assignees | self.outsider, assignees | self.outsider, self.editor],
        )
        private_next_task = next_tasks.filtered(lambda task: task.project_id == self.private_project)
        self.assertEqual(len(private_next_task.child_ids), 1)
        self.assertFalse(private_next_task.child_ids.user_ids)
        self.assertEqual(recurring_tasks[0].user_ids, assignees, "the task repeated keeps its assignees")
