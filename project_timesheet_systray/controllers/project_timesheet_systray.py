from odoo import fields, http
from odoo.http import request


def _record_key(record):
    # `key` is declared outside this module's dependencies (OCA project_key): a database may have none.
    return (record.key or "") if "key" in record._fields else ""


def _project_label(project):
    project_key = _record_key(project)
    return "%s - %s" % (project_key, project.name) if project_key else project.name


class TimesheetSystrayController(http.Controller):
    @http.route("/project_timesheet_systray/systray", type="json", auth="user", methods=["POST"])
    def get_systray_timesheet(self, limit=5, **kwargs):
        """The user's running timer, or when none runs their tasks in unfolded stages, the
        most recently logged first; `limit` 0 returns them all.
        """
        TimesheetLine = request.env["account.analytic.line"].sudo(False)
        user = request.env.user

        running_domain = [
            ("date_time", "!=", False),
            ("user_id", "=", user.id),
            ("project_id.allow_timesheets", "=", True),
            ("unit_amount", "=", 0),
            ("task_id", "!=", False),
        ]
        running = TimesheetLine.search(running_domain, limit=1, order="date_time desc")
        if running:
            return {
                "running": True,
                "timesheet": self._format_running_timesheet(running),
                "tasks": [],
                "total_tasks": 0,
                "message": "",
            }

        in_progress_tasks = request.env["project.task"].search(
            [
                ("user_ids", "in", [user.id]),
                ("stage_id.fold", "=", False),
            ]
        )
        if not in_progress_tasks:
            return {
                "running": False,
                "timesheet": False,
                "tasks": [],
                "total_tasks": 0,
                "message": "No tasks in progress",
            }

        timelog_groups = TimesheetLine.read_group(
            [
                ("user_id", "=", user.id),
                ("task_id", "in", in_progress_tasks.ids),
                ("unit_amount", ">", 0),
            ],
            ["task_id", "date_time:max"],
            ["task_id"],
        )
        last_timelog_map = {group["task_id"][0]: group["date_time"] for group in timelog_groups if group["task_id"]}

        def sort_key(task):
            last_timelog = last_timelog_map.get(task.id)
            return (1 if last_timelog else 0, last_timelog or "", task.id)

        sorted_tasks = sorted(in_progress_tasks, key=sort_key, reverse=True)
        total_tasks = len(sorted_tasks)
        limited_tasks = sorted_tasks[:limit] if limit else sorted_tasks

        tasks_data = []
        for task in limited_tasks:
            last_timelog = last_timelog_map.get(task.id, "")
            tasks_data.append(
                {
                    "task_id": task.id,
                    "task_name": task.name,
                    "task_key": _record_key(task),
                    "project_id": task.project_id.id,
                    "project_name": _project_label(task.project_id),
                    "stage_name": task.stage_id.name,
                    "last_timelog": str(last_timelog) if last_timelog else "",
                },
            )

        return {
            "running": False,
            "timesheet": False,
            "tasks": tasks_data,
            "total_tasks": total_tasks,
            "message": "",
        }

    @http.route("/project_timesheet_systray/start", type="json", auth="user", methods=["POST"])
    def start_timer(self, task_id, **kwargs):
        """Stop the user's running timers and start one on the task, for the user's employee in
        the task's company.
        """
        TimesheetLine = request.env["account.analytic.line"]
        user = request.env.user

        task = request.env["project.task"].browse(task_id)
        if not task.exists():
            return {"error": "Task not found"}

        task_company = task.company_id or task.project_id.company_id
        employee = (
            request.env["hr.employee"]
            .sudo()
            .search([("user_id", "=", user.id), ("company_id", "=", task_company.id)], limit=1)
        )
        if not employee:
            return {"error": "No employee linked to your user"}

        # A failed create puts the stopped timers back.
        with request.env.cr.savepoint():
            running = TimesheetLine.search(TimesheetLine._running_domain())
            if running:
                running.button_end_work()
            new_line = TimesheetLine.create(
                {
                    "task_id": task.id,
                    "project_id": task.project_id.id,
                    "employee_id": employee.id,
                    "user_id": user.id,
                    "name": task.name,
                    "date_time": fields.Datetime.now(),
                    "unit_amount": 0,
                }
            )
        return {
            "running": True,
            "timesheet": self._format_running_timesheet(new_line),
            "tasks": [],
            "total_tasks": 0,
            "message": "",
        }

    @http.route("/project_timesheet_systray/stop", type="json", auth="user", methods=["POST"])
    def stop_timer(self, timesheet_id, **kwargs):
        """Stop the running timer and answer with the task list, as the systray route does."""
        line = request.env["account.analytic.line"].browse(timesheet_id)
        if not line.exists():
            return {"error": "Timesheet line not found"}
        if line.unit_amount:
            return {"error": "Timer is not running"}
        line.button_end_work()
        return self.get_systray_timesheet()

    def _format_running_timesheet(self, line):
        task = line.task_id
        elapsed = 0
        if line.date_time:
            elapsed = (fields.Datetime.now() - line.date_time).total_seconds()

        return {
            "id": line.id,
            "task_id": task.id,
            "task_name": task.name,
            "task_key": _record_key(task),
            "project_id": task.project_id.id,
            "project_name": _project_label(task.project_id),
            "date_time": str(line.date_time) if line.date_time else "",
            "unit_amount": line.unit_amount,
            "elapsed_seconds": elapsed,
        }
