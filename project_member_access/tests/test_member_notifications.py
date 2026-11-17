from odoo.tests.common import tagged

from .common import MemberAccessCommon


@tagged("post_install", "-at_install")
class TestMemberNotifications(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Customer", "email": "customer@example.com"})
        (cls.manager | cls.outsider).notification_type = "inbox"

    def _notified_partners(self, record, followers, **post_values):
        record.message_subscribe(followers.ids)
        message = record.message_post(
            body="Update", message_type="comment", subtype_xmlid="mail.mt_comment", **post_values
        )
        return self.env["mail.notification"].search([("mail_message_id", "=", message.id)]).res_partner_id

    def test_private_task_notifies_only_its_readers(self):
        followers = (self.reader | self.outsider | self.manager | self.portal).partner_id | self.customer
        notified = self._notified_partners(self.private_task, followers)
        self.assertEqual(notified, (self.reader | self.manager).partner_id)
        self.assertEqual(self.private_task.message_partner_ids & followers, followers, "followers are kept")

    def test_private_task_skips_a_direct_recipient_without_access(self):
        recipients = (self.contributor | self.outsider).partner_id
        notified = self._notified_partners(self.private_task, self.env["res.partner"], partner_ids=recipients.ids)
        self.assertEqual(notified, self.contributor.partner_id)

    def test_private_task_checks_a_recipient_in_their_own_companies(self):
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.assertNotIn(other_company, self.reader.company_ids)
        task = self.private_task.with_context(allowed_company_ids=(self.env.company | other_company).ids)
        notified = self._notified_partners(task, (self.reader | self.outsider).partner_id)
        self.assertEqual(notified, self.reader.partner_id)

    def test_public_task_skips_portal_followers(self):
        notified = self._notified_partners(self.public_task, (self.outsider | self.portal).partner_id)
        self.assertEqual(notified, self.outsider.partner_id)

    def test_standard_task_notifies_every_follower(self):
        followers = (self.outsider | self.portal).partner_id | self.customer
        self.assertEqual(self._notified_partners(self.standard_task, followers), followers)

    def test_task_without_project_notifies_every_follower(self):
        task = self.env["project.task"].create({"name": "No project"})
        followers = self.portal.partner_id | self.customer
        self.assertEqual(self._notified_partners(task, followers), followers)

    def test_private_project_notifies_only_its_readers(self):
        followers = (self.reader | self.outsider | self.portal).partner_id
        notified = self._notified_partners(self.private_project, followers)
        self.assertEqual(notified, self.reader.partner_id)

    def test_private_project_update_notifies_only_its_readers(self):
        update = self.env["project.update"].create(
            {"name": "Week 1", "project_id": self.private_project.id, "status": "on_track"}
        )
        notified = self._notified_partners(update, (self.reader | self.outsider | self.portal).partner_id)
        self.assertEqual(notified, self.reader.partner_id)

    def test_standard_project_update_notifies_every_follower(self):
        update = self.env["project.update"].create(
            {"name": "Week 1", "project_id": self.standard_project.id, "status": "on_track"}
        )
        followers = (self.outsider | self.portal).partner_id | self.customer
        self.assertEqual(self._notified_partners(update, followers), followers)
