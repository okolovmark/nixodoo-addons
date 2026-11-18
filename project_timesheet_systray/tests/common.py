from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command
from odoo.tests.common import TransactionCase

from odoo.addons.project_timesheet_systray.controllers import project_timesheet_systray as controller


class TimesheetSystrayCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Stage = cls.env["project.task.type"]
        cls.stage_backlog = Stage.create({"name": "Backlog", "sequence": 1})
        cls.stage_in_progress = Stage.create({"name": "In Progress", "sequence": 2})
        cls.stage_done = Stage.create({"name": "Done", "fold": True, "sequence": 3})
        cls.stage_other = Stage.create({"name": "Review", "sequence": 4})
        cls.project = cls.env["project.project"].create(
            {
                "name": "Test Timesheet Project",
                "allow_timesheets": True,
                "type_ids": [
                    Command.set((cls.stage_backlog + cls.stage_in_progress + cls.stage_done + cls.stage_other).ids)
                ],
            }
        )
        cls.user_dev1, cls.user_dev2 = cls.env["res.users"].create(
            [
                cls._project_user_vals("Dev One", "timesheet_systray_dev1"),
                cls._project_user_vals("Dev Two", "timesheet_systray_dev2"),
            ]
        )
        cls.employee_dev1, cls.employee_dev2 = cls.env["hr.employee"].create(
            [
                {"name": "Dev One", "user_id": cls.user_dev1.id, "company_id": cls.env.company.id},
                {"name": "Dev Two", "user_id": cls.user_dev2.id, "company_id": cls.env.company.id},
            ]
        )

    @classmethod
    def _project_user_vals(cls, name, login):
        groups = cls.env.ref("project.group_project_user") | cls.env.ref("hr_timesheet.group_hr_timesheet_user")
        return {
            "name": name,
            "login": login,
            "email": "%s@example.com" % login,
            "groups_id": [Command.set((cls.env.ref("base.group_user") | groups).ids)],
        }

    @classmethod
    def _create_task(cls, name, stage, user, **context):
        return (
            cls.env["project.task"]
            .with_context(**context)
            .create(
                {
                    "name": name,
                    "project_id": cls.project.id,
                    "stage_id": stage.id,
                    "user_ids": [Command.set(user.ids)],
                }
            )
        )

    def _create_timesheet(self, task, user, employee, unit_amount=0, date_time=None):
        """A line with unit_amount 0 and a date_time is a running timer."""
        return self.env["account.analytic.line"].create(
            {
                "task_id": task.id,
                "project_id": task.project_id.id,
                "employee_id": employee.id,
                "user_id": user.id,
                "name": task.name,
                "date_time": date_time or datetime.now(),
                "unit_amount": unit_amount,
            }
        )

    def _call_route(self, user, route_name, **kwargs):
        with patch.object(controller, "request", SimpleNamespace(env=self.env(user=user))):
            return getattr(controller.TimesheetSystrayController(), route_name)(**kwargs)

    def _skip_without_keys(self):
        if "key" not in self.env["project.project"]._fields:
            self.skipTest("no module of this database declares project and task keys")
