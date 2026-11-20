from odoo import Command
from odoo.tests.common import HttpCase, tagged

from .common import MemberAccessCommon


def planted(**vals):
    # a new dict each time: core blanks the token in the vals of a project write
    return {"access_token": "planted", **vals}


@tagged("post_install", "-at_install")
class TestAccessTokens(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.portal_project = cls.env["project.project"].create(
            {"name": "Shared", "privacy_visibility": "portal", "partner_id": cls.portal.partner_id.id}
        )
        cls.portal_task = cls._create_task(cls.portal_project)
        cls.archived_task = cls._create_task(cls.portal_project, active=False)
        cls.shared_records = (cls.portal_project, cls.portal_task, cls.archived_task)
        for record in cls.shared_records:
            record._portal_ensure_token()

    def _tokens(self):
        return [record.access_token for record in self.shared_records]

    def test_a_project_becoming_public_or_private_revokes_its_tokens_and_its_tasks(self):
        self.assertTrue(all(self._tokens()))
        self.portal_project.write(
            {"access_mode": "public", "member_ids": [Command.create({"user_id": self.admin.id, "role": "admin"})]}
        )
        self.assertFalse(any(self._tokens()))

    def test_a_project_created_private_holds_no_token(self):
        project = self.env["project.project"].create(
            {
                "name": "Born private",
                "access_mode": "private",
                "access_token": "planted",
                "member_ids": [Command.create({"user_id": self.admin.id, "role": "admin"})],
            }
        )
        self.assertFalse(project.access_token)

    def test_a_task_moved_in_loses_its_token(self):
        self.portal_task.with_user(self.admin).write({"project_id": self.public_project.id})
        self.assertFalse(self.portal_task.access_token)
        self.assertTrue(self.archived_task.access_token, "left in its project")

    def test_no_token_is_created_inside(self):
        for record in (self.public_project, self.public_task, self.private_project, self.private_task):
            self.assertEqual(record._portal_ensure_token(), "")
            record._get_share_url()
            record.get_portal_url()
            self.assertFalse(record.access_token, record.display_name)
        self.assertTrue(self.standard_task._portal_ensure_token(), "outside, as core does")

    def _written(self, record, vals):
        record.write(vals)
        return record

    def _planted_without_write(self, record):
        self.env.flush_all()
        self.env.cr.execute("UPDATE %s SET access_token = %%s WHERE id = %%s" % record._table, ["planted", record.id])
        record.invalidate_recordset(["access_token"])
        return record

    def test_a_token_filled_inside_is_cleared(self):
        # Portal: core keeps a token written on a project only under that visibility
        (self.public_project | self.private_project).privacy_visibility = "portal"
        admin_member = [Command.create({"user_id": self.admin.id, "role": "admin"})]
        fills = {
            "public project": lambda: self._written(self.public_project, planted()),
            "private project": lambda: self._written(self.private_project, planted()),
            "private task": lambda: self._written(self.private_task, planted()),
            "private task, other values": lambda: self._written(
                self._planted_without_write(self.private_task), {"name": "Renamed"}
            ),
            "private project, other values": lambda: self._written(
                self._planted_without_write(self.private_project), {"name": "Renamed"}
            ),
            "task created inside": lambda: self._create_task(self.private_project, **planted()),
            "task created inside, context default": lambda: self.env["project.task"]
            .with_context(default_project_id=self.private_project.id, default_access_token="planted")
            .create({"name": "Defaulted"}),
            "project created private, context default": lambda: self.env["project.project"]
            .with_context(default_access_token="planted")
            .create({"name": "Defaulted", "access_mode": "private", "member_ids": admin_member}),
            "task copied inside": lambda: self.private_task.copy(planted()),
            "task moved in": lambda: self._written(self.portal_task, planted(project_id=self.public_project.id)),
            "project made public": lambda: self._written(
                self.portal_project, planted(access_mode="public", member_ids=admin_member)
            ),
        }
        for fill_name, fill in fills.items():
            with self.subTest(fill=fill_name):
                self.assertFalse(fill().access_token)
        self.standard_task.write({"access_token": "outside"})
        self.standard_task.write({"name": "Renamed"})
        self.assertEqual(self.standard_task.access_token, "outside")

    def test_a_token_written_on_a_recurrence_is_cleared_inside(self):
        recurring_tasks = self._create_task(self.private_project) | self._create_task(self.private_project)
        self.env["project.task.recurrence"].create({"mon": True, "task_ids": [Command.set(recurring_tasks.ids)]})
        for recurrence_update in ("all", "subsequent"):
            with self.subTest(recurrence_update=recurrence_update):
                recurring_tasks[0].write(planted(recurrence_update=recurrence_update))
                self.assertEqual(recurring_tasks.mapped("access_token"), [False, False])

    def test_a_token_written_on_a_recurrence_with_recurrent_unticked_is_cleared_inside(self):
        for recurrence_update in ("all", "subsequent"):
            with self.subTest(recurrence_update=recurrence_update):
                recurring_tasks = self._create_task(self.private_project) | self._create_task(self.private_project)
                self.env["project.task.recurrence"].create(
                    {"mon": True, "task_ids": [Command.set(recurring_tasks.ids)]}
                )
                recurring_tasks[0].write(planted(recurrence_update=recurrence_update, recurring_task=False))
                self.assertEqual(recurring_tasks.mapped("access_token"), [False, False])

    def test_an_attachment_token_survives_the_project_going_private(self):
        attachments = self.env["ir.attachment"].create(
            [
                {"name": "pasted.png", "raw": b"pasted", "res_model": record._name, "res_id": record.id}
                for record in self.shared_records
            ]
        )
        tokens = attachments.generate_access_token()
        self.portal_project.write(
            {"access_mode": "private", "member_ids": [Command.create({"user_id": self.admin.id, "role": "admin"})]}
        )
        self.assertFalse(any(self._tokens()))
        self.assertEqual(attachments.mapped("access_token"), tokens)


@tagged("post_install", "-at_install")
class TestAccessTokenRoutes(HttpCase):
    def setUp(self):
        super().setUp()
        self.portal_partner = self.env["res.partner"].create({"name": "Customer"})
        self.admin_user = self.env["res.users"].create(
            {
                "name": "Admin of the project",
                "login": "member_access_token_admin",
                "groups_id": [Command.set(self.env.ref("project.group_project_user").ids)],
            }
        )
        self.project = self.env["project.project"].create(
            {"name": "Shared", "privacy_visibility": "portal", "partner_id": self.portal_partner.id}
        )
        self.task = self.env["project.task"].create({"name": "Token Opened Task", "project_id": self.project.id})
        self.token = self.task._portal_ensure_token()

    def _opens_the_task(self):
        self.env.flush_all()
        response = self.url_open("/my/tasks/%s?access_token=%s" % (self.task.id, self.token), timeout=60)
        return response.status_code == 200 and self.task.name in response.text

    def test_a_token_stops_opening_the_task_once_the_project_is_public(self):
        self.assertTrue(self._opens_the_task())
        self.project.write(
            {"access_mode": "public", "member_ids": [Command.create({"user_id": self.admin_user.id, "role": "admin"})]}
        )
        self.assertFalse(self._opens_the_task())
