from contextlib import contextmanager

from lxml import html

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user


class ProjectAiDrivenCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_lead = cls._create_project_user("Project Lead", "project_ai_driven_lead", "lead@example.com")
        cls.user_dev = cls._create_project_user("Developer", "project_ai_driven_dev", "dev@example.com")
        # no stage: a new project gets default ones
        cls.project = cls.env["project.project"].create(
            {
                "name": "AI-driven Project",
                "is_ai_driven": True,
                "user_id": cls.user_lead.id,
                "type_ids": [Command.clear()],
            },
        )
        cls.task = cls.env["project.task"].create({"name": "AI-driven Task", "project_id": cls.project.id})

    @classmethod
    def _create_user(cls, name, login, email, extra_group=None):
        groups = "base.group_user"
        if extra_group:
            groups = f"{groups},{extra_group}"
        return new_test_user(
            cls.env,
            login,
            groups=groups,
            context={"no_reset_password": True},
            name=name,
            email=email,
        )

    @classmethod
    def _create_project_user(cls, name, login, email):
        return cls._create_user(name, login, email, extra_group="project.group_project_user")

    @classmethod
    def _create_stage(cls, name, sequence, project=None, **values):
        return cls.env["project.task.type"].create(
            {"name": name, "sequence": sequence, "project_ids": [Command.link((project or cls.project).id)], **values},
        )

    def _flush_tracking(self):
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _tracking_texts(self, record):
        """Return the text of each tracking message of record, oldest first.
        The body holds the tracked values: mail.tracking.value belongs to the
        module mail_tracking, which this one does not depend on.
        """
        self._flush_tracking()
        tracking_messages = record.message_ids.filtered(lambda message: message.message_type == "tracking")
        return [
            " ".join(html.fromstring(message.body).text_content().split()) for message in tracking_messages.sorted("id")
        ]

    @contextmanager
    def assertRefused(self, *message_parts):
        # assertRaises rolls the refused write back; assertRaisesRegex leaves it in the cache
        with self.assertRaises(ValidationError) as refusal:
            yield
        for message_part in message_parts:
            self.assertIn(message_part, refusal.exception.args[0])
