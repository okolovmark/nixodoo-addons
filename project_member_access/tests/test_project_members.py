from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from ..models.project_project import ROLE_USER_FIELDS
from .common import MODULE, PROJECT_USER, MemberAccessCommon

SHOWN_IN_STANDARD_ONLY = "access_mode != 'standard'"


@tagged("post_install", "-at_install")
class TestProjectMembers(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_row = cls.private_project.member_ids.filtered(lambda member: member.role == "admin")

    def test_role_user_sets_hold_the_role_and_above(self):
        project = self.private_project
        self.assertEqual(project.member_user_ids, self.reader | self.contributor | self.editor | self.admin)
        self.assertEqual(project.contributor_user_ids, self.contributor | self.editor | self.admin)
        self.assertEqual(project.editor_user_ids, self.editor | self.admin)
        self.assertEqual(project.admin_user_ids, self.admin)
        project.member_ids.filtered(lambda member: member.user_id == self.reader).role = "editor"
        self.assertEqual(project.editor_user_ids, self.reader | self.editor | self.admin)

    def test_the_last_admin_is_neither_removed_nor_lowered(self):
        with self.assertRefused(ValidationError, "Admin is the last Admin of Private", "before removing them"):
            self.admin_row.unlink()
        with self.assertRefused(ValidationError, "Admin is the last Admin of Private", "before removing them"):
            self.private_project.write({"member_ids": [Command.delete(self.admin_row.id)]})
        with self.assertRefused(ValidationError, "Admin is the last Admin of Private", "before lowering theirs"):
            self.admin_row.role = "editor"

    def test_an_archived_admin_is_not_another_admin(self):
        archived_user = self._create_user("archived", PROJECT_USER)
        self.env["project.member"].create(
            {"project_id": self.private_project.id, "user_id": archived_user.id, "role": "admin"}
        )
        archived_user.active = False
        with self.assertRefused(ValidationError, "Admin is the last Admin of Private", "before lowering theirs"):
            self.admin_row.role = "editor"
        with self.assertRefused(ValidationError, "Admin is the last Admin of Private", "before removing them"):
            self.admin_row.unlink()

    def test_the_users_of_each_role_follow_the_members_only(self):
        for field_name in ROLE_USER_FIELDS:
            with self.subTest(field=field_name):
                with self.assertRefused(UserError, "change its members instead", in_place=True):
                    self.public_project.write({field_name: [Command.link(self.outsider.id)]})
                with self.assertRefused(UserError, "change its members instead"):
                    self.env["project.project"].create(
                        {"name": "Forged", "access_mode": "standard", field_name: [Command.set(self.outsider.ids)]}
                    )
        self.assertNotIn(self.outsider, self.public_project.member_user_ids)

    def test_an_admin_leaves_once_another_member_is_admin(self):
        self.private_project.member_ids.filtered(lambda member: member.user_id == self.editor).role = "admin"
        self.admin_row.role = "reader"
        self.admin_row.unlink()
        self.assertEqual(self.private_project.admin_user_ids, self.editor)

    def test_an_archived_admin_is_no_admin_of_the_project(self):
        archived_user = self._create_user("archived", PROJECT_USER)
        archived_user.active = False
        archived_admins = [Command.create({"user_id": archived_user.id, "role": "admin"})]
        with self.assertRefused(ValidationError, "needs a member with the role Admin, an active user"):
            self.standard_project.write({"access_mode": "public", "member_ids": archived_admins})
        with self.assertRefused(ValidationError, "needs a member with the role Admin, an active user"):
            self.env["project.project"].create(
                {"name": "Archived", "access_mode": "private", "member_ids": archived_admins}
            )

    def test_a_public_or_private_project_has_an_admin(self):
        with self.assertRefused(ValidationError, "needs a member with the role Admin", "Standard"):
            self.standard_project.access_mode = "public"
        with self.assertRefused(ValidationError, "needs a member with the role Admin"):
            self.env["project.project"].create({"name": "Empty", "access_mode": "private"})
        self.standard_project.write(
            {"access_mode": "private", "member_ids": [Command.create({"user_id": self.admin.id, "role": "admin"})]}
        )
        self.assertEqual(self.standard_project.access_mode, "private")

    def test_a_member_is_an_internal_user_once_per_project(self):
        Member = self.env["project.member"]
        with self.assertRefused(ValidationError, "Only an internal user", "Portal"):
            Member.create({"project_id": self.public_project.id, "user_id": self.portal.id})
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
            Member.create({"project_id": self.public_project.id, "user_id": self.reader.id})

    def test_the_project_that_develops_this_odoo_is_not_private(self):
        Project = self.env["project.project"].with_context(active_test=False)
        Project.search([("develops_this_odoo", "=", True)]).develops_this_odoo = False
        self.private_project.is_ai_driven = True
        with self.assertRefused(ValidationError, "cannot be Private"):
            self.private_project.develops_this_odoo = True
        self.public_project.write({"is_ai_driven": True, "develops_this_odoo": True})
        with self.assertRefused(ValidationError, "cannot be Private"):
            self.public_project.access_mode = "private"

    def test_people_of_a_private_task_are_members(self):
        with self.assertRefused(ValidationError, "only its members", "Not members: Outsider"):
            self.private_task.user_ids = self.outsider
        with self.assertRefused(ValidationError, "Not members: Outsider"):
            self.private_task.uat_user_ids = self.outsider
        with self.assertRefused(ValidationError, "Not members: Outsider"):
            self.env["project.task"].create(
                {
                    "name": "Assigned",
                    "project_id": self.private_project.id,
                    "user_ids": [Command.set(self.outsider.ids)],
                }
            )
        self.private_task.write(
            {"user_ids": [Command.set(self.editor.ids)], "uat_user_ids": [Command.set(self.reader.ids)]}
        )
        self.public_task.user_ids = self.outsider

    def test_an_archived_member_stays_on_the_tasks_of_a_private_project(self):
        """The Assignees and the UAT users hold archived users, so the members read them too."""
        archived_user = self._create_user("archived", PROJECT_USER)
        self.env["project.member"].create(
            {"project_id": self.private_project.id, "user_id": archived_user.id, "role": "reader"}
        )
        uat_task = self._create_task(self.private_project, uat_user_ids=[Command.set(archived_user.ids)])
        assigned_task = self._create_task(self.private_project, user_ids=[Command.set(archived_user.ids)])
        archived_user.active = False
        uat_task.uat_user_ids += self.reader
        assigned_task.user_ids += self.editor
        self.assertEqual(uat_task.uat_user_ids, archived_user | self.reader)
        self.assertEqual(assigned_task.user_ids, archived_user | self.editor)

    def test_a_project_turning_private_keeps_the_people_of_its_tasks(self):
        self.standard_task.user_ids = self.outsider
        self.standard_project.write(
            {"access_mode": "private", "member_ids": [Command.create({"user_id": self.admin.id, "role": "admin"})]}
        )
        self.assertEqual(self.standard_task.user_ids, self.outsider)

    def test_a_member_keeps_their_project_and_user(self):
        """The write restriction reads the row before the write: a row taken to a project
        where its writer is no Admin would carry a role there.
        """
        other_project = self.env["project.project"].create(
            {
                "name": "Other",
                "access_mode": "public",
                "member_ids": [Command.create({"user_id": self.editor.id, "role": "admin"})],
            }
        )
        reader_row = self.private_project.member_ids.filtered(lambda member: member.user_id == self.reader)
        as_admin = reader_row.with_user(self.admin)
        for row in (as_admin, reader_row):
            with self.assertRefused(ValidationError, "keeps their project and their user"):
                row.write({"project_id": other_project.id, "role": "admin"})
        with self.assertRefused(ValidationError, "keeps their project and their user"):
            as_admin.user_id = self.outsider
        with self.assertRaises(AccessError):
            self.env["project.member"].with_user(self.admin).create(
                {"project_id": other_project.id, "user_id": self.admin.id, "role": "admin"}
            )
        as_admin.write({"project_id": self.private_project.id, "user_id": self.reader.id, "role": "contributor"})
        self.assertEqual(other_project.admin_user_ids, self.editor)
        self.assertIn(self.reader, self.private_project.contributor_user_ids)

    def test_members_are_kept_by_the_admins(self):
        Member = self.env["project.member"]
        new_row = Member.with_user(self.admin).create(
            {"project_id": self.private_project.id, "user_id": self.outsider.id}
        )
        new_row.with_user(self.admin).role = "contributor"
        self.assertIn(self.outsider, self.private_project.contributor_user_ids)
        for user in self.editor | self.manager:
            with self.assertRaises(AccessError):
                Member.with_user(user).create({"project_id": self.private_project.id, "user_id": self.employee.id})
        Member.with_user(self.manager).create({"project_id": self.standard_project.id, "user_id": self.employee.id})
        new_row.with_user(self.admin).unlink()
        self.assertNotIn(self.outsider, self.private_project.member_user_ids)

    def test_a_settings_administrator_adds_members_anywhere(self):
        row = (
            self.env["project.member"]
            .with_user(self.sysadmin)
            .create({"project_id": self.private_project.id, "user_id": self.outsider.id, "role": "admin"})
        )
        self.assertEqual(row.project_id, self.private_project)

    def test_nobody_makes_themselves_admin(self):
        """The create restriction sees the new row: an Admin row would admit its own creator."""
        Member = self.env["project.member"]
        for project in self.standard_project | self.public_project:
            with self.assertRefused(AccessError, "Only an Admin of %s adds its members" % project.name):
                Member.with_user(self.outsider).create(
                    {"project_id": project.id, "user_id": self.outsider.id, "role": "admin"}
                )
        with self.assertRaises(AccessError):
            Member.with_user(self.manager).create(
                {"project_id": self.public_project.id, "user_id": self.manager.id, "role": "admin"}
            )
        self.standard_project.with_user(self.manager).write(
            {
                "access_mode": "private",
                "member_ids": [
                    Command.create({"user_id": self.manager.id, "role": "admin"}),
                    Command.create({"user_id": self.reader.id, "role": "admin"}),
                ],
            }
        )
        self.assertEqual(self.standard_project.admin_user_ids, self.manager | self.reader)

    def test_a_project_administrator_names_another_user_as_the_first_admin(self):
        """The rows are created after the project turned Public or Private, the manager not among its Admins."""
        Project = self.env["project.project"].with_user(self.manager)
        first_admins = [Command.create({"user_id": self.reader.id, "role": "admin"})]
        for access_mode in ("public", "private"):
            with self.subTest(access_mode=access_mode):
                created_project = Project.create(
                    {"name": "Created", "access_mode": access_mode, "member_ids": first_admins}
                )
                switched_project = Project.create({"name": "Switched"})
                switched_project.write({"access_mode": access_mode, "member_ids": first_admins})
                self.assertEqual((created_project | switched_project).admin_user_ids, self.reader)

    def test_only_a_project_administrator_names_the_first_admin(self):
        Member = self.env["project.member"]
        for user in self.editor | self.outsider:
            with self.subTest(user=user.login), self.assertRaises(AccessError):
                Member.with_user(user).create(
                    {"project_id": self.standard_project.id, "user_id": self.reader.id, "role": "admin"}
                )
        with self.assertRefused(AccessError, "Only an Admin of Public adds its members"):
            Member.with_user(self.manager).create(
                {"project_id": self.public_project.id, "user_id": self.outsider.id, "role": "admin"}
            )

    def test_the_form_shows_the_mode_and_the_members(self):
        arch = self.env.ref("project.edit_project")._get_combined_arch()
        visibility = arch.xpath("//field[@name='privacy_visibility']")[0]
        self.assertIn("access_mode", [node.get("name") for node in visibility.itersiblings(preceding=True)])
        self.assertEqual(visibility.get("invisible"), SHOWN_IN_STANDARD_ONLY)
        members_page = arch.xpath("//page[@name='members']")[0]
        self.assertEqual(members_page.get("invisible"), "access_mode == 'standard'")
        self.assertEqual(members_page.getnext().get("name"), "settings")
        members_list = members_page.xpath("./field[@name='member_ids']/list")[0]
        self.assertEqual(members_list.get("editable"), "bottom")
        self.assertEqual([field.get("name") for field in members_list.iter("field")], ["user_id", "role"])
        for member_user in members_page.xpath(
            "./field[@name='member_ids']/*[self::list or self::form]//field[@name='user_id']"
        ):
            self.assertEqual(member_user.get("readonly"), "id")
        card_user = members_page.xpath("./field[@name='member_ids']/kanban//field[@name='user_id']")
        self.assertTrue(card_user, "cards on a phone")
        self.assertEqual(card_user[0].get("options"), "{'display_avatar_name': True}", "the card names the user")

    def test_sharing_and_team_members_are_hidden_inside(self):
        form_arch = self._arch_with_this_module(
            "project.edit_project", "project_project_view_form_inherit_member_access"
        )
        visibility_group = form_arch.xpath("//group[@name='visibility']")[0]
        hidden_inside = (
            visibility_group.xpath("./div[field[@name='allowed_internal_user_ids']]")
            + visibility_group.xpath("./button[@name='%s']" % self.env.ref("project.project_share_wizard_action").id)
            + visibility_group.xpath("./div[field[@name='access_instruction_message']]")
        )
        self.assertEqual(len(hidden_inside), 3)
        for node in hidden_inside:
            self.assertTrue(node.get("invisible").endswith(" or (%s)" % SHOWN_IN_STANDARD_ONLY), node.get("invisible"))
        card_arch = self._arch_with_this_module(
            "project.view_project_card", "project_project_view_kanban_inherit_member_access"
        )
        share_item = card_arch.xpath("//a[@name='action_open_share_project_wizard']")[0]
        self.assertTrue(share_item.get("t-if").endswith(" and (record.access_mode.raw_value == 'standard')"))
        self.assertTrue(card_arch.xpath("./field[@name='access_mode']"))
        task_card_arch = self._arch_with_this_module(
            "project.view_task_card", "project_task_view_card_inherit_member_access"
        )
        task_share_item = task_card_arch.xpath("//a[@name='%s']" % self.env.ref("project.portal_share_action").id)[0]
        self.assertTrue(
            task_share_item.get("t-if").endswith(" and (record.project_access_mode.raw_value == 'standard')")
        )
        self.assertTrue(task_card_arch.xpath("./field[@name='project_access_mode']"))

    def test_the_task_card_reads_the_access_of_a_project_the_user_does_not(self):
        self.standard_project.privacy_visibility = "followers"
        self.standard_task.message_subscribe(self.outsider.partner_id.ids)
        task = self.standard_task.with_user(self.outsider)
        self.assertFalse(self.standard_project.with_user(self.outsider).has_access("read"))
        self.assertEqual(task.project_access_mode, "standard")

    def _arch_with_this_module(self, view_xmlid, inheriting_view_name):
        """The core view with the view of this module alone applied: other modules may
        rebuild the parts it changes.
        """
        return self.env["ir.ui.view"].apply_inheritance_specs(
            etree.fromstring(self.env.ref(view_xmlid).arch),
            etree.fromstring(self.env.ref("%s.%s" % (MODULE, inheriting_view_name)).arch),
        )

    def test_the_form_loads_with_its_members(self):
        views = self.env["project.project"].get_views([(self.env.ref("project.edit_project").id, "form")])
        self.assertIn("access_mode", views["models"]["project.project"]["fields"])
        self.assertTrue({"user_id", "role"} <= set(views["models"]["project.member"]["fields"]))
