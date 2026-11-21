from odoo import _, fields, http
from odoo.http import request

CARD_DESCRIPTION_MAX_LENGTH = 200


def _card_description(line_name):
    """The line's description as the Timelog card shows it: none for "/", which hr_timesheet writes on a
    line created without one; past the limit, cut to fit it with an ellipsis, no space before that."""
    if line_name in (False, "/"):
        return ""
    if len(line_name) <= CARD_DESCRIPTION_MAX_LENGTH:
        return line_name
    return line_name[: CARD_DESCRIPTION_MAX_LENGTH - 1].rstrip() + "…"


def _record_key(record):
    # `key` is declared outside this module's dependencies (OCA project_key): a database may have none.
    return (record.key or "") if "key" in record._fields else ""


def _project_label(project):
    # Sudo: the user reads the task, not always its project (one they do not follow, say).
    project_sudo = project.sudo()
    project_key = _record_key(project_sudo)
    return "%s - %s" % (project_key, project_sudo.name) if project_key else project_sudo.name


def _parse_company_ids(company_ids):
    """`company_ids`, a list or the comma-separated `cids` cookie, as integers; none where malformed."""
    if isinstance(company_ids, str):
        company_ids = company_ids.split(",")
    try:
        return [int(company_id) for company_id in company_ids or []]
    except (TypeError, ValueError):
        return []


def _web_client_company_ids(tab_company_ids=None):
    """The companies a web client tab opens a form with: `tab_company_ids`, which the Timelog sends
    from the tab, else the `cids` cookie's, shared by the browser's tabs; read as the 16.0 company
    service reads them, the user's current company where they name none or one the user may not use.
    """
    user = request.env.user
    cookie_company_ids = request.httprequest.cookies.get("cids")
    for company_ids in (_parse_company_ids(tab_company_ids), _parse_company_ids(cookie_company_ids)):
        if company_ids and set(company_ids) <= set(user.company_ids.ids):
            return company_ids
    return user.company_id.ids


class TimesheetSystrayController(http.Controller):
    @http.route("/project_timesheet_systray/systray", type="json", auth="user", methods=["POST"])
    def get_systray_timesheet(self, limit=5, company_ids=None, **kwargs):
        """The user's running timer, the latest started, on a task or on a project only, or when
        none runs their tasks in unfolded stages of projects with Timesheets on, the most recently
        logged first; `limit` 0 returns them all.
        `available` is false for a user without Timesheets rights: the header then shows no
        Timelog item. `company_ids` are those of the web client tab, in which the running
        timer's task opens or not; not a context, which would narrow the task list.
        """
        TimesheetLine = request.env["account.analytic.line"].sudo(False)
        user = request.env.user
        # Not a read check on timesheet lines: Analytic Accounting gives that to every user.
        if not user.has_group("hr_timesheet.group_hr_timesheet_user"):
            return {
                "available": False,
                "running": False,
                "timesheet": False,
                "tasks": [],
                "total_tasks": 0,
                "message": "",
            }

        running = TimesheetLine._get_own_running_lines()[:1]
        if running:
            return {
                "running": True,
                "timesheet": self._format_running_timesheet(running, company_ids),
                "tasks": [],
                "total_tasks": 0,
                "message": "",
            }

        in_progress_tasks = request.env["project.task"].search(
            TimesheetLine._resolve_project_terms_in_sudo(
                [
                    ("user_ids", "in", [user.id]),
                    ("stage_id.fold", "=", False),
                    ("project_id.allow_timesheets", "=", True),
                ]
            )
        )
        if not in_progress_tasks:
            return {
                "running": False,
                "timesheet": False,
                "tasks": [],
                "total_tasks": 0,
                "message": _("No tasks in progress"),
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
    def start_timer(self, task_id, company_ids=None, **kwargs):
        """Stop the user's running timers and start one on the task, for the user's employee in
        the task's company; the user follows the task again where the time line needs it.
        """
        TimesheetLine = request.env["account.analytic.line"]
        user = request.env.user

        task = request.env["project.task"].browse(task_id)
        if not task.exists():
            return {"error": _("Task not found")}
        task_company = task.company_id or task.project_id.company_id
        # The task list leaves such a task out; one loaded before Timesheets went off still offers it.
        # After the read above, which refuses a task the user cannot read.
        if not task.allow_timesheets:
            return {"error": _("Timesheets are turned off in the project of this task")}
        employee = (
            request.env["hr.employee"]
            .sudo()
            .search([("user_id", "=", user.id), ("company_id", "=", task_company.id)], limit=1)
        )
        if not employee:
            return {"error": _("No employee linked to your user")}

        line_vals = {
            "task_id": task.id,
            "project_id": task.project_id.id,
            "employee_id": employee.id,
            "user_id": user.id,
            "name": task.name,
            "date_time": fields.Datetime.now(),
            "unit_amount": 0,
        }
        # A failed create puts the stopped timers back and takes the follow back.
        with request.env.cr.savepoint():
            TimesheetLine._get_own_running_lines().button_end_work()
            new_line = TimesheetLine._create_with_task_follow(task, lambda: TimesheetLine.create(line_vals))
        return {
            "running": True,
            "timesheet": self._format_running_timesheet(new_line, company_ids),
            "tasks": [],
            "total_tasks": 0,
            "message": "",
        }

    @http.route("/project_timesheet_systray/stop", type="json", auth="user", methods=["POST"])
    def stop_timer(self, timesheet_id, company_ids=None, **kwargs):
        """Stop the user's running timer and answer as the systray route does: the timer still
        running, else the task list."""
        running_line = (
            request.env["account.analytic.line"]._get_own_running_lines().filtered(lambda line: line.id == timesheet_id)
        )
        if not running_line:
            return {"error": _("Timer is not running")}
        running_line.button_end_work()
        return self.get_systray_timesheet(company_ids=company_ids)

    def _format_running_timesheet(self, line, company_ids=None):
        # Sudo: the line is the user's own, its task or project may no longer be readable to them (a
        # timesheet manager taken off a task of a followers-only project while the timer runs).
        task_sudo = line.task_id.sudo()
        project_sudo = task_sudo.project_id or line.project_id.sudo()
        task = line.task_id.sudo(False).with_context(allowed_company_ids=_web_client_company_ids(company_ids))
        is_task_readable = task.check_access_rights("read", raise_exception=False) and bool(
            task._filter_access_rules("read")
        )
        elapsed = 0
        if line.date_time:
            elapsed = (fields.Datetime.now() - line.date_time).total_seconds()

        return {
            "id": line.id,
            "task_id": task_sudo.id,
            "task_name": task_sudo.name,
            "task_key": _record_key(task_sudo),
            "is_task_readable": is_task_readable,
            "project_id": project_sudo.id,
            "project_name": _project_label(project_sudo),
            "description": _card_description(line.name),
            "date_time": str(line.date_time) if line.date_time else "",
            "unit_amount": line.unit_amount,
            "elapsed_seconds": elapsed,
        }
