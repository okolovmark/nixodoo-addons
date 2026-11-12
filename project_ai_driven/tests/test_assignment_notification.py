from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import ProjectAiDrivenCommon


@tagged("post_install", "-at_install")
class TestAssignmentNotification(ProjectAiDrivenCommon):
    """Core project notifies a new assignee straight from create() and write(),
    without the mail_auto_subscribe_no_notify check mail.thread makes, so a user
    with no email cannot assign a task at all.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_no_email = cls._create_project_user("Service Account", "project_ai_driven_service", False)
        cls.service_project = cls.env["project.project"].create(
            {"name": "Service Project", "user_id": cls.user_no_email.id},
        )
        cls.service_task = cls.env["project.task"].create(
            {"name": "Service Task", "project_id": cls.service_project.id},
        )

    def _assignment_notifications(self, task):
        return self.env["mail.message"].search(
            [("model", "=", "project.task"), ("res_id", "=", task.id), ("message_type", "=", "user_notification")],
        )

    def test_assigning_without_email_fails_without_the_flag(self):
        with self.assertRaises(UserError):
            self.service_task.with_user(self.user_no_email).write({"user_ids": [Command.set(self.user_dev.ids)]})

    def test_assigning_without_email_works_with_the_flag(self):
        self.service_task.with_user(self.user_no_email).with_context(mail_auto_subscribe_no_notify=True).write(
            {"user_ids": [Command.set(self.user_dev.ids)]},
        )
        self.assertEqual(self.service_task.user_ids, self.user_dev)
        self.assertFalse(self._assignment_notifications(self.service_task))

    def test_creating_assigned_without_email_works_with_the_flag(self):
        task = (
            self.env["project.task"]
            .with_user(self.user_no_email)
            .with_context(mail_auto_subscribe_no_notify=True)
            .create(
                {
                    "name": "Created Assigned",
                    "project_id": self.service_project.id,
                    "user_ids": [Command.set(self.user_dev.ids)],
                },
            )
        )
        self.assertEqual(task.user_ids, self.user_dev)
        self.assertFalse(self._assignment_notifications(task))

    def test_assignment_still_notifies_without_the_flag(self):
        self.task.with_user(self.user_lead).write({"user_ids": [Command.set(self.user_dev.ids)]})
        self.assertEqual(self.task.user_ids, self.user_dev)
        self.assertTrue(self._assignment_notifications(self.task))
