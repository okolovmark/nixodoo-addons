from datetime import datetime, timedelta

from odoo import Command
from odoo.tests.common import TransactionCase


class TeamDashboardCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.stage_backlog = cls.env["project.task.type"].create(
            {
                "name": "Backlog",
                "is_backlog": True,
                "is_in_progress": False,
                "fold": False,
                "sequence": 1,
            },
        )
        cls.stage_in_progress = cls.env["project.task.type"].create(
            {
                "name": "In Progress",
                "is_backlog": False,
                "is_in_progress": True,
                "fold": False,
                "sequence": 2,
            },
        )
        cls.stage_done = cls.env["project.task.type"].create(
            {
                "name": "Done",
                "is_backlog": False,
                "is_in_progress": False,
                "fold": True,
                "sequence": 3,
            },
        )
        cls.stage_other = cls.env["project.task.type"].create(
            {
                "name": "Review",
                "is_backlog": False,
                "is_in_progress": False,
                "fold": False,
                "sequence": 4,
            },
        )

        cls.project = cls.env["project.project"].create(
            {
                "name": "Test AI-driven Project",
                "is_ai_driven": True,
                "type_ids": [
                    Command.set(
                        [
                            cls.stage_backlog.id,
                            cls.stage_in_progress.id,
                            cls.stage_done.id,
                            cls.stage_other.id,
                        ],
                    ),
                ],
            },
        )

        cls.project_plain = cls.env["project.project"].create(
            {
                "name": "Plain Project",
                "is_ai_driven": False,
            },
        )

        cls.user_dev1 = cls.env["res.users"].create(
            {
                "name": "Dev One",
                "login": "dev1_test_sw",
                "email": "dev1@test.com",
                "groups_id": [
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("project.group_project_user").id,
                        ],
                    ),
                ],
            },
        )
        cls.user_dev2 = cls.env["res.users"].create(
            {
                "name": "Dev Two",
                "login": "dev2_test_sw",
                "email": "dev2@test.com",
                "groups_id": [
                    Command.set(
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref("project.group_project_user").id,
                        ],
                    ),
                ],
            },
        )

        # timesheet lines need an employee
        cls.employee_dev1 = cls.env["hr.employee"].create(
            {
                "name": "Dev One",
                "user_id": cls.user_dev1.id,
                "company_id": cls.env.company.id,
            },
        )
        cls.employee_dev2 = cls.env["hr.employee"].create(
            {
                "name": "Dev Two",
                "user_id": cls.user_dev2.id,
                "company_id": cls.env.company.id,
            },
        )

    def _stagger_create_dates(self, tasks):
        """Back-date the creation of the tasks a day apart, in their order: a transaction creates them
        all at its own time."""
        self.env.flush_all()
        for day_offset, task in enumerate(tasks):
            self.env.cr.execute(
                "UPDATE project_task SET create_date = %s WHERE id = %s",
                (datetime(2020, 1, 1) + timedelta(days=day_offset), task.id),
            )
        tasks.invalidate_recordset(["create_date"])

    @classmethod
    def _user_vals(cls, name, group_xmlids):
        return {
            "name": name,
            "login": "dashboard_access_%s" % name.lower().replace(" ", "_"),
            "groups_id": [Command.set([cls.env.ref(xmlid).id for xmlid in group_xmlids])],
        }
