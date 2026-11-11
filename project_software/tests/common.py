from odoo import Command
from odoo.tests.common import TransactionCase


class ProjectSoftwareCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_lead = cls._create_project_user("Project Lead", "project_software_lead", "lead@example.com")
        cls.user_dev = cls._create_project_user("Developer", "project_software_dev", "dev@example.com")
        cls.project = cls.env["project.project"].create(
            {"name": "Software Project", "is_software_project": True, "user_id": cls.user_lead.id},
        )
        cls.task = cls.env["project.task"].create({"name": "Software Task", "project_id": cls.project.id})

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
