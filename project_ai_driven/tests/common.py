from contextlib import contextmanager
from unittest import SkipTest

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase

PULL_REQUEST_MODULE = "project_task_pull_request"


class ProjectAiDrivenCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_lead = cls._create_project_user("Project Lead", "project_ai_driven_lead", "lead@example.com")
        cls.user_dev = cls._create_project_user("Developer", "project_ai_driven_dev", "dev@example.com")
        cls.project = cls.env["project.project"].create(
            {"name": "AI-driven Project", "is_ai_driven": True, "user_id": cls.user_lead.id},
        )
        cls.task = cls.env["project.task"].create({"name": "AI-driven Task", "project_id": cls.project.id})

    @classmethod
    def _skip_next_to_pull_request_module(cls):
        """For the setUpClass of a class whose tasks enter a marked stage without
        a pull request: while project_task_pull_request is installed its
        constraint refuses them first, with a message of its own.
        """
        domain = [("name", "=", PULL_REQUEST_MODULE), ("state", "=", "installed")]
        if cls.env["ir.module.module"].search_count(domain):
            raise SkipTest(
                "%s is installed: its constraint refuses a task without pr_uri in a marked stage"
                " before the closing rule runs, with its own message" % PULL_REQUEST_MODULE
            )

    @classmethod
    def _create_user(cls, name, login, email, extra_group=None):
        groups = cls.env.ref("base.group_user")
        if extra_group:
            groups |= cls.env.ref(extra_group)
        return (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create({"name": name, "login": login, "email": email, "groups_id": [Command.set(groups.ids)]})
        )

    @classmethod
    def _create_project_user(cls, name, login, email):
        return cls._create_user(name, login, email, extra_group="project.group_project_user")

    def _flush_tracking(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    @contextmanager
    def assertRefused(self, *message_parts):
        # assertRaises rolls the refused write back; assertRaisesRegex leaves it in the cache
        with self.assertRaises(ValidationError) as refusal:
            yield
        for message_part in message_parts:
            self.assertIn(message_part, refusal.exception.args[0])
