import unittest
from contextlib import contextmanager
from functools import partial

from odoo import Command
from odoo.fields import Domain
from odoo.tests.common import TransactionCase, new_test_user

MODULE = "project_member_access"
PROJECT_USER = "project.group_project_user"
ROLE_LOGINS = ("reader", "contributor", "editor", "admin")


class MemberAccessCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        for login in ROLE_LOGINS + ("outsider",):
            setattr(cls, login, cls._create_user(login, PROJECT_USER))
        cls.manager = cls._create_user("manager", "project.group_project_manager")
        cls.sysadmin = cls._create_user("sysadmin", "base.group_system", PROJECT_USER)
        cls.employee = cls._create_user("employee", "base.group_user")
        cls.portal = cls._create_user("portal", "base.group_portal")

        members = [Command.create({"user_id": getattr(cls, login).id, "role": login}) for login in ROLE_LOGINS]
        Project = cls.env["project.project"]
        cls.standard_project = Project.create({"name": "Standard", "privacy_visibility": "employees"})
        # Core visibilities that would decide otherwise: only the Access mode decides inside.
        cls.public_project = Project.create(
            {"name": "Public", "access_mode": "public", "privacy_visibility": "followers", "member_ids": members}
        )
        cls.private_project = Project.create(
            {"name": "Private", "access_mode": "private", "privacy_visibility": "employees", "member_ids": members}
        )
        cls.projects = cls.standard_project | cls.public_project | cls.private_project
        Stage = cls.env["project.task.type"]
        cls.stage_todo = Stage.create({"name": "To Do", "sequence": 1, "project_ids": [Command.set(cls.projects.ids)]})
        cls.stage_done = Stage.create({"name": "Done", "sequence": 2, "project_ids": [Command.set(cls.projects.ids)]})

        cls.private_reader_task = cls._create_task(cls.private_project, user_ids=[Command.set(cls.reader.ids)])
        cls.private_task = cls._create_task(cls.private_project)
        cls.public_task = cls._create_task(cls.public_project)
        cls.standard_task = cls._create_task(cls.standard_project)

    @classmethod
    def _create_task(cls, project, user=None, **values):
        """Create a task as the views of a project do, with the project in the context."""
        Task = cls.env["project.task"].with_context(default_project_id=project.id)
        if user:
            Task = Task.with_user(user)
        return Task.create({"name": "%s task" % project.name, **values})

    @classmethod
    def _create_user(cls, login, *group_xmlids):
        return new_test_user(
            cls.env,
            "member_access_%s" % login,
            groups=",".join(group_xmlids),
            context={"no_reset_password": True},
            name=login.capitalize(),
            email="%s@example.com" % login,
        )

    @classmethod
    def _lead(cls, project, user):
        """Make user the core Project Manager of project: modules that keep stage and
        assignee changes to it then let the call reach the checks under test.
        """
        project.user_id = user

    def _allowed(self, records, user, operation):
        """Whether user may apply operation to all of records, by the access rows alone.
        Both evaluations of the access domain must agree: in Python and in SQL.
        """
        self.env.transaction.invalidate_access_cache()
        as_user = records.with_user(user)
        by_python = as_user.has_access(operation)
        access_domain = as_user._access_domain(operation)
        found = as_user.sudo().with_context(active_test=False).search(Domain("id", "in", records.ids) & access_domain)
        by_sql = set(found.ids) == set(records.ids)
        self.assertEqual(by_sql, by_python, "%s %s %s" % (user.login, operation, records))
        return by_python

    def _allowed_matrix(self, records, users, operations=("read", "write", "create", "unlink")):
        return {
            user.login: tuple(self._allowed(records, user, operation) for operation in operations) for user in users
        }

    @contextmanager
    def assertRefused(self, exception_class, *message_parts, in_place=False):
        """With in_place, the refused operation is not rolled back to a savepoint, as Odoo's
        assertRaises does: what it left behind can be asserted after.
        """
        raises = partial(unittest.TestCase.assertRaises, self) if in_place else self.assertRaises
        with raises(exception_class) as refusal:
            yield
        for message_part in message_parts:
            self.assertIn(message_part, str(refusal.exception))
