from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
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

    def _create_timesheet(self, task, user, employee, unit_amount=0, date_time=None, logged_by=None, name=None):
        """A line with unit_amount 0 and a date_time is a running timer; `logged_by` creates it."""
        time_lines = self.env["account.analytic.line"]
        if logged_by:
            time_lines = time_lines.with_user(logged_by)
        return time_lines.create(
            {
                "task_id": task.id,
                "project_id": task.project_id.id,
                "employee_id": employee.id,
                "user_id": user.id,
                "name": name or task.name,
                "date_time": date_time or fields.Datetime.now(),
                "unit_amount": unit_amount,
            }
        )

    def _create_running_line_without_task(self, project, user, employee, name="/", date_time=None, logged_by=None):
        """A running timer on `project` with no task, as Start work on a project form leaves. `name`
        defaults to "/", which hr_timesheet writes on a line created without a description."""
        time_lines = self.env["account.analytic.line"]
        if logged_by:
            time_lines = time_lines.with_user(logged_by)
        return time_lines.create(
            {
                "project_id": project.id,
                "employee_id": employee.id,
                "user_id": user.id,
                "name": name,
                "date_time": date_time or fields.Datetime.now(),
                "unit_amount": 0,
            }
        )

    def _create_unfollowed_task(self, privacy_visibility):
        """A task of Dev One in a project of that visibility, which Dev One follows no longer."""
        project = self.env["project.project"].create(
            {
                "name": "Project Visible to %s" % privacy_visibility,
                "privacy_visibility": privacy_visibility,
                "allow_timesheets": True,
                "type_ids": [Command.set(self.stage_in_progress.ids)],
            }
        )
        task = self.env["project.task"].create(
            {
                "name": "Task Unfollowed by Its Assignee",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "user_ids": [Command.set(self.user_dev1.ids)],
            }
        )
        task.message_unsubscribe(self.user_dev1.partner_id.ids)
        self.assertNotIn(self.user_dev1.partner_id, task.message_partner_ids)
        return task

    def _call_route(self, user, route_name, **kwargs):
        user_request = SimpleNamespace(env=self.env(user=user), httprequest=SimpleNamespace(cookies={}))
        with patch.object(controller, "request", user_request):
            return getattr(controller.TimesheetSystrayController(), route_name)(**kwargs)

    def _skip_without_keys(self):
        if "key" not in self.env["project.project"]._fields:
            self.skipTest("no module of this database declares project and task keys")
