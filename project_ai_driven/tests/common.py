from odoo.tests.common import TransactionCase, new_test_user


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
