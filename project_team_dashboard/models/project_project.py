import base64
import io
import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import xlsxwriter

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from odoo.tools import format_date, get_lang

# Their holders read every timesheet or every employee anyway: they may open any manager or department.
EVERY_TEAM_GROUPS = ("hr_timesheet.group_hr_timesheet_approver", "hr.group_hr_user")


class ProjectProject(models.Model):
    _inherit = "project.project"

    baseline_id = fields.Many2one(
        "project.baseline",
        string="Active Baseline",
        copy=False,
        help="Saved plan the Gantt chart compares the current plan against.",
    )

    @api.constrains("baseline_id")
    def _check_baseline_project(self):
        for record in self:
            if record.baseline_id and record.baseline_id.project_id != record:
                raise ValidationError(
                    self.env._("The active baseline of project '%s' belongs to another project.", record.name)
                )

    def save_gantt_baselines(self):
        """Store the planned dates and deadline of the open tasks of these projects as each project's
        active baseline, deleting the baselines it replaces. Project Administrators only, with write
        access to the projects."""
        if not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(self.env._("Only Project Managers can save a baseline."))
        projects = self.browse(list(dict.fromkeys(self.ids))).exists()
        if not projects:
            return True
        projects.check_access("write")
        projects = projects.sudo()
        self.env.cr.execute("SELECT id FROM project_project WHERE id IN %s FOR UPDATE", [tuple(projects.ids)])
        Baseline = projects.env["project.baseline"]
        open_tasks = projects.env["project.task"].search(
            [
                ("project_id", "in", projects.ids),
                ("has_template_ancestor", "=", False),
                ("is_closed", "=", False),
                "|",
                "|",
                ("planned_date_start", "!=", False),
                ("planned_date_end", "!=", False),
                ("date_deadline", "!=", False),
            ],
        )
        new_baselines = Baseline.create(
            [
                {
                    "project_id": project.id,
                    "line_ids": [
                        Command.create(
                            {
                                "task_id": task.id,
                                "planned_date_start": task.planned_date_start,
                                "planned_date_end": task.planned_date_end,
                                "date_deadline": task.date_deadline,
                            },
                        )
                        for task in open_tasks
                        if task.project_id == project
                    ],
                }
                for project in projects
            ],
        )
        for project, new_baseline in zip(projects, new_baselines, strict=True):
            project.baseline_id = new_baseline
        Baseline.search([("project_id", "in", projects.ids), ("id", "not in", new_baselines.ids)]).unlink()
        return True

    @api.model
    def get_team_dashboard_data(self, project_id, date_start=None, date_end=None):
        """Return the workload of the project's team: a card per assignee of its open tasks, and one
        for the open tasks nobody is assigned to.

        date_start and date_end ("YYYY-MM-DD") bound the period whose logged hours a card shows;
        the current week, from the first day of the week of the user's language, by default.
        """
        # archived projects, tasks, users and employees stay out whatever the caller sends
        return self.with_context(active_test=True)._get_team_dashboard_data(project_id, date_start, date_end)

    @api.model
    def _get_team_dashboard_data(self, project_id, date_start, date_end):
        self.check_access("read")
        project = self.search([("id", "=", project_id)], limit=1)
        if not project:
            return {"error": self.env._("Project not found")}

        Timesheet = self.env["account.analytic.line"]
        today = fields.Date.context_today(self)
        period_start, period_end = self._get_dashboard_period(date_start, date_end, today)
        open_tasks = self.env["project.task"].search(
            [("project_id", "=", project_id), ("has_template_ancestor", "=", False), ("is_closed", "=", False)]
        )
        period_hours_by_user = self._get_logged_hours_by_user(
            Timesheet, [("project_id", "=", project_id), ("date", ">=", period_start), ("date", "<=", period_end)]
        )
        member_today_by_user = self._get_member_today_by_user(open_tasks.user_ids)
        today_hours_by_user = self._get_today_hours_by_user(
            Timesheet, [("project_id", "=", project_id)], member_today_by_user
        )

        members_data = [
            self._get_member_data(
                user,
                open_tasks.filtered(lambda task, user=user: user in task.user_ids),
                period_hours_by_user.get(user.id, 0.0),
                today_hours_by_user.get(user.id, 0.0),
                today,
            )
            for user in open_tasks.user_ids.sorted(key=lambda user: user.name)
        ]

        unassigned_tasks = open_tasks.filtered(lambda task: not task.user_ids)
        if unassigned_tasks:
            unassigned_in_progress = unassigned_tasks.filtered(lambda task: task.stage_id.is_in_progress)
            unassigned_backlog = unassigned_tasks.filtered(lambda task: task.stage_id.is_backlog)
            unassigned_other = unassigned_tasks - unassigned_in_progress - unassigned_backlog
            members_data.append(
                {
                    "user_id": False,
                    "user_name": self.env._("Unassigned"),
                    "user_avatar_url": False,
                    "current_task": False,
                    "queue": [self._task_to_dict(task, today) for task in unassigned_in_progress.sorted("sequence")],
                    "backlog": [self._task_to_dict(task, today) for task in unassigned_backlog.sorted("sequence")],
                    "other": [self._task_to_dict(task, today) for task in unassigned_other.sorted("sequence")],
                    "total_open_tasks": len(unassigned_tasks),
                    "total_planned_hours": sum(unassigned_tasks.mapped("allocated_hours")),
                    "total_remaining_hours": sum(unassigned_tasks.mapped("remaining_hours")),
                    "hours_logged_period": 0,
                    "hours_logged_today": 0,
                    "estimated_completion": False,
                },
            )

        return {
            "project_id": project.id,
            "project_name": project.name,
            "project_key": project.key or "",
            "period_start": fields.Date.to_string(period_start),
            "period_end": fields.Date.to_string(period_end),
            "members": members_data,
        }

    @api.model
    def get_gantt_chart_data(
        self, project_id=None, show_completed=False, project_ids=None, include_stage_history=False
    ):
        """Return the Gantt chart of the projects project_ids, or of project_id alone.

        A task is on the chart with a planned date or a deadline, and its ancestors with it; the
        open tasks without either are listed under "unscheduled". show_completed adds the closed
        tasks, and include_stage_history fills each task's stage_history with its stage visits.
        """
        self.check_access("read")
        # the day of the user, as the deadline dates of the payload are
        today = fields.Date.context_today(self)
        result = {
            "tasks": [],
            "today": fields.Date.to_string(today),
            "tasks_without_dates": 0,
            # The timezone the deadline dates of the payload are in.
            "user_tz": str(self.env.tz),
            "columns": self.env.user._get_gantt_columns(),
            "is_project_manager": self.env.user.has_group("project.group_project_manager"),
            "unscheduled": [],
        }

        if project_ids:
            pids = project_ids
        elif project_id:
            pids = [project_id]
        else:
            return result

        projects = self.search([("id", "in", pids)])
        if not projects:
            return result

        is_multi = len(pids) > 1

        Task = self.env["project.task"]

        # not the requested ids: a follower of a task reads it in a project they cannot read
        domain = [
            ("project_id", "in", projects.ids),
            ("has_template_ancestor", "=", False),
            "|",
            "|",
            ("planned_date_start", "!=", False),
            ("planned_date_end", "!=", False),
            ("date_deadline", "!=", False),
        ]
        if not show_completed:
            domain.insert(1, ("is_closed", "=", False))
        all_tasks = Task.search(domain)
        task_ids_set = set(all_tasks.ids)

        # the chart shows no task of a project the user cannot read, a parent they follow there included
        ancestors_needed = Task
        visited = set(task_ids_set)
        queue = all_tasks
        while queue:
            next_queue = Task
            parents = queue.parent_id._filtered_access("read")
            parent_projects = parents.project_id._filtered_access("read")
            readable_parents = parents.filtered(
                lambda parent: not parent.project_id or parent.project_id in parent_projects
            )
            for task in queue:
                parent = task.parent_id
                if parent and parent.id not in visited and parent in readable_parents:
                    if not parent.is_closed or show_completed:
                        visited.add(parent.id)
                        ancestors_needed |= parent
                        next_queue |= parent
            queue = next_queue

        all_tasks |= ancestors_needed
        task_ids_set.update(ancestors_needed.ids)

        children_map = {
            task.id: [child.id for child in task.child_ids if child.id in task_ids_set] for task in all_tasks
        }

        unscheduled_domain = [
            ("project_id", "in", projects.ids),
            ("has_template_ancestor", "=", False),
            ("id", "not in", list(task_ids_set)),
            ("planned_date_start", "=", False),
            ("planned_date_end", "=", False),
            ("date_deadline", "=", False),
        ]
        if not show_completed:
            unscheduled_domain.append(("is_closed", "=", False))
        unscheduled_tasks = Task.search(unscheduled_domain)

        writable_ids = set()
        if Task.has_access("write"):
            writable_ids = set((all_tasks | unscheduled_tasks)._filtered_access("write").ids)

        lags_by_task = {}
        lags = (
            self.env["project.task.dependency.lag"]
            .sudo()
            .search(
                [("task_id", "in", all_tasks.ids), ("depends_on_id", "in", all_tasks.ids)],
            )
        )
        for lag in lags:
            lags_by_task.setdefault(lag.task_id.id, {})[lag.depends_on_id.id] = lag.lag_days

        baseline_lines = (
            self.env["project.baseline.line"]
            .sudo()
            .search(
                [("baseline_id", "in", projects.sudo().baseline_id.ids), ("task_id", "in", all_tasks.ids)],
            )
        )
        baseline_by_task = {
            line.task_id.id: line for line in baseline_lines if line.baseline_id.project_id == line.task_id.project_id
        }

        stage_history_by_task = all_tasks._gantt_stage_history() if include_stage_history else {}

        tasks_data = []
        for task in all_tasks:
            date_start = task.planned_date_start
            date_end = task.planned_date_end
            has_planned_dates = bool(date_start and date_end)

            # a single planned date gives a one-day bar
            if date_start and not date_end:
                date_end = date_start + timedelta(days=1)
            elif date_end and not date_start:
                date_start = date_end - timedelta(days=1)

            is_deadline_only = not date_start and not date_end and bool(task.date_deadline)
            if is_deadline_only:
                date_start = date_end = datetime.combine(Task._gantt_local_date(task.date_deadline), time.min)

            if not date_start and not date_end:
                continue
            # a deadline-only bar already sits on its local day
            end_day = date_end.date() if is_deadline_only else Task._gantt_local_date(date_end)

            stage = task.stage_id
            parent_id = task.parent_id.id
            if parent_id not in task_ids_set:
                parent_id = False

            task_dict = {
                "id": task.id,
                "name": task.name,
                "key": task.key or "",
                "parent_id": parent_id,
                "child_ids": children_map[task.id],
                # the format of read(): a str() of a stored value keeps its microseconds
                "planned_date_start": fields.Datetime.to_string(date_start),
                "planned_date_end": fields.Datetime.to_string(date_end),
                # the bar's dates pad a missing one: a move writes only the dates the task has
                "has_planned_date_start": bool(task.planned_date_start),
                "has_planned_date_end": bool(task.planned_date_end),
                "stage_id": stage.id,
                "stage_name": stage.name,
                "stage_sequence": stage.sequence,
                "is_in_progress": stage.is_in_progress,
                "is_backlog": stage.is_backlog,
                "is_closed": task.is_closed,
                "is_overdue": end_day < today,
                "user_names": ", ".join(task.user_ids.mapped("name")),
                "user_list": [{"id": user.id, "name": user.name} for user in task.user_ids],
                "progress": self._get_progress_percentage(task),
                "planned_hours": task.allocated_hours,
                "remaining_hours": task.remaining_hours,
                "depend_on_ids": [
                    predecessor.id for predecessor in task.depend_on_ids if predecessor.id in task_ids_set
                ],
                "task_type": task.task_type or "",
            }
            task_dict.update(task._gantt_task_attributes(writable_ids, lags_by_task.get(task.id, {})))
            baseline_line = baseline_by_task.get(task.id, self.env["project.baseline.line"])
            baseline_end = Task._gantt_local_date(baseline_line.planned_date_end)
            plan_start = has_planned_dates and Task._gantt_local_date(task.planned_date_start)
            plan_end = has_planned_dates and Task._gantt_local_date(task.planned_date_end)
            task_dict.update(
                {
                    "is_deadline_only": is_deadline_only,
                    "plan_date_start": fields.Date.to_string(plan_start),
                    "plan_date_end": fields.Date.to_string(plan_end),
                    "baseline_date_start": fields.Datetime.to_string(baseline_line.planned_date_start),
                    "baseline_date_end": fields.Datetime.to_string(baseline_line.planned_date_end),
                    "baseline_shift_days": ((plan_end - baseline_end).days if plan_end and baseline_end else False),
                    "stage_history": stage_history_by_task.get(task.id, []),
                }
            )
            if is_multi:
                # a parent kept from a project not selected is labelled by its own
                task_dict["project_name"] = self._get_project_label(task.project_id)
            tasks_data.append(task_dict)

        unscheduled_data = []
        for task in unscheduled_tasks:
            task_dict = {
                "id": task.id,
                "name": task.name,
                "key": task.key or "",
                "stage_name": task.stage_id.name or "",
                "is_closed": task.is_closed,
                "user_names": ", ".join(task.user_ids.mapped("name")),
                "planned_hours": task.allocated_hours,
                "task_type": task.task_type or "",
            }
            task_dict.update(task._gantt_task_attributes(writable_ids, {}))
            if is_multi:
                task_dict["project_name"] = self._get_project_label(task.project_id)
            unscheduled_data.append(task_dict)

        result.update(
            {
                "tasks": tasks_data,
                "today": fields.Date.to_string(today),
                "tasks_without_dates": len(unscheduled_data),
                "unscheduled": unscheduled_data,
            }
        )
        return result

    @api.model
    def export_gantt_excel(self, project_ids):
        """Generate an Excel Gantt chart and return it as base64-encoded data."""
        self.check_access("read")
        if not project_ids:
            return {"error": self.env._("No projects selected")}

        gantt_data = self.get_gantt_chart_data(project_ids=project_ids, show_completed=False)
        tasks_with_dates = [
            task for task in gantt_data["tasks"] if task.get("planned_date_start") and task.get("planned_date_end")
        ]
        if not tasks_with_dates:
            return {"error": self.env._("No tasks with planned dates")}
        bar_ends_by_task_id = {task["id"]: self._get_gantt_bar_ends(task) for task in tasks_with_dates}
        tasks_with_dates.sort(key=lambda task: bar_ends_by_task_id[task["id"]])
        bar_days_by_task_id = {
            task_id: (bar_start.date(), bar_end.date()) for task_id, (bar_start, bar_end) in bar_ends_by_task_id.items()
        }

        min_date = min(start_day for start_day, _end_day in bar_days_by_task_id.values()) - timedelta(days=1)
        max_date = max(end_day for _start_day, end_day in bar_days_by_task_id.values()) + timedelta(days=1)

        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {"in_memory": True})
        worksheet = workbook.add_worksheet(self._get_gantt_sheet_title())

        header_fmt = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#875A7B",
                "font_color": "#FFFFFF",
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
                "align": "center",
                "font_size": 10,
            }
        )
        task_fmt = workbook.add_format({"border": 1, "text_wrap": True, "valign": "vcenter", "font_size": 9})
        date_fmt = workbook.add_format({"border": 1, "valign": "vcenter", "font_size": 9, "num_format": "dd mmm"})
        pct_fmt = workbook.add_format(
            {"border": 1, "valign": "vcenter", "font_size": 9, "num_format": "0%", "align": "center"}
        )
        bar_in_progress = workbook.add_format({"bg_color": "#2ecb71", "border": 1})
        bar_done = workbook.add_format({"bg_color": "#27ae60", "border": 1})
        bar_backlog = workbook.add_format({"bg_color": "#5dade2", "border": 1})
        bar_overdue = workbook.add_format({"bg_color": "#ec7063", "border": 1})
        bar_other = workbook.add_format({"bg_color": "#aab7b8", "border": 1})
        bar_milestone = workbook.add_format(
            {
                "bg_color": "#8e44ad",
                "font_color": "#FFFFFF",
                "border": 1,
                "bold": True,
                "align": "center",
                "font_size": 9,
            }
        )
        empty_fmt = workbook.add_format({"border": 0})
        day_header_values = {
            "bold": True,
            "border": 1,
            "font_size": 8,
            "align": "center",
            "valign": "vcenter",
            "rotation": 90,
        }
        date_header_fmt = workbook.add_format(dict(day_header_values, bg_color="#F0F0F0"))
        today_header_fmt = workbook.add_format(dict(day_header_values, bg_color="#FDEBD0"))

        fixed_columns = (
            (self.env._("Key"), 12),
            (self.env._("Task"), 35),
            (self.env._("Type"), 14),
            (self.env._("Assignee"), 18),
            (self.env._("Start"), 11),
            (self.env._("End"), 11),
            (self.env._("Progress"), 9),
            (self.env._("Stage"), 14),
        )
        fixed_count = len(fixed_columns)
        total_days = (max_date - min_date).days + 1

        worksheet.set_row(0, 60)
        for column_index, (title, width) in enumerate(fixed_columns):
            worksheet.set_column(column_index, column_index, width)
            worksheet.write(0, column_index, title, header_fmt)
        today = fields.Date.to_date(gantt_data["today"])
        for day_index in range(total_days):
            day = min_date + timedelta(days=day_index)
            worksheet.set_column(fixed_count + day_index, fixed_count + day_index, 3)
            worksheet.write(
                0,
                fixed_count + day_index,
                format_date(self.env, day, date_format="dd MMM"),
                today_header_fmt if day == today else date_header_fmt,
            )

        for row_index, task in enumerate(tasks_with_dates, start=1):
            worksheet.set_row(row_index, 22)
            start_date, end_date = bar_days_by_task_id[task["id"]]

            progress = task.get("progress", 0) or 0
            planned_hours = task.get("planned_hours", 0)
            remaining_hours = task.get("remaining_hours", 0)
            if not progress and planned_hours:
                progress = ((planned_hours - remaining_hours) / planned_hours) * 100
            progress = max(0, min(100, progress))

            task_type = (task.get("task_type", "") or "").replace("_", " ").title()
            is_milestone = task_type.lower() == "milestone" or task.get("is_deadline_only")

            worksheet.write(row_index, 0, task.get("key", ""), task_fmt)
            worksheet.write(row_index, 1, task.get("name", ""), task_fmt)
            worksheet.write(row_index, 2, task_type, task_fmt)
            worksheet.write(row_index, 3, task.get("user_names", ""), task_fmt)
            # the bar pads a missing date, which is not the task's: its cell stays empty
            for column_index, day, has_date in (
                (4, start_date, task["has_planned_date_start"]),
                (5, end_date, task["has_planned_date_end"]),
            ):
                if has_date:
                    worksheet.write_datetime(row_index, column_index, datetime.combine(day, time.min), date_fmt)
                else:
                    worksheet.write_blank(row_index, column_index, None, date_fmt)
            worksheet.write(row_index, 6, progress / 100.0, pct_fmt)
            worksheet.write(row_index, 7, task.get("stage_name", ""), task_fmt)

            if is_milestone:
                bar_fmt = bar_milestone
            elif task.get("is_closed"):
                bar_fmt = bar_done
            elif task.get("is_overdue"):
                bar_fmt = bar_overdue
            elif task.get("is_in_progress"):
                bar_fmt = bar_in_progress
            elif task.get("is_backlog"):
                bar_fmt = bar_backlog
            else:
                bar_fmt = bar_other

            is_end_only = task["has_planned_date_end"] and not task["has_planned_date_start"]
            milestone_date = end_date if is_end_only else start_date
            for day_index in range(total_days):
                day = min_date + timedelta(days=day_index)
                column_index = fixed_count + day_index
                if is_milestone:
                    if day == milestone_date:
                        worksheet.write(row_index, column_index, "◆", bar_fmt)
                    else:
                        worksheet.write(row_index, column_index, "", empty_fmt)
                elif start_date <= day <= end_date:
                    worksheet.write(row_index, column_index, "", bar_fmt)
                else:
                    worksheet.write(row_index, column_index, "", empty_fmt)

        worksheet.freeze_panes(1, fixed_count)
        workbook.close()
        output.seek(0)
        data = base64.b64encode(output.read()).decode("utf-8")

        first_readable_project = (self.browse(project_ids) & self.search([("id", "in", project_ids)]))[:1]
        project_name = first_readable_project.name or "Gantt"
        filename = "Gantt_%s.xlsx" % project_name.replace(" ", "_")[:30]
        return {"data": data, "filename": filename}

    @api.model
    def _get_gantt_sheet_title(self):
        """The translated title as Excel takes it: Odoo's patched add_worksheet drops the characters
        Excel refuses and cuts it to 31, but leaves the apostrophes Excel refuses at either end."""
        sheet_title = re.sub(r"[\[\]:*?/\\]", "", self.env._("Gantt Chart"))[:31].strip("'")
        return sheet_title or "Gantt Chart"

    @api.model
    def _get_gantt_bar_ends(self, task_data):
        """Return the start and end, naive in the user's timezone, of the bar of task_data, a task
        of the Gantt payload: the moments the chart draws it from and to."""
        bar_ends = [fields.Datetime.to_datetime(task_data[key]) for key in ("planned_date_start", "planned_date_end")]
        if task_data["is_deadline_only"]:
            # the payload puts a deadline-only bar at midnight of its local day already
            return tuple(bar_ends)
        return tuple(bar_end.replace(tzinfo=UTC).astimezone(self.env.tz).replace(tzinfo=None) for bar_end in bar_ends)

    @api.model
    def get_team_dashboard_by_manager(self, user_id, date_start=None, date_end=None, max_depth=0):
        """Return the cards of the employees below the manager user_id (the current user by
        default), down to max_depth levels (0: all), across every project they have open tasks in.

        Raises AccessError unless _check_manager_mode_access lets the user see that manager's team.
        """
        # the board's active_test, whatever the caller sends: without it, an archived employee opens a team
        Project = self.with_context(active_test=True)
        Project.check_access("read")
        manager_user = Project.env["res.users"].browse(user_id or Project.env.uid)
        return Project._get_manager_dashboard(
            manager_user, Project._get_openable_manager_users(), date_start, date_end, max_depth
        )

    @api.model
    def get_team_dashboard_by_department(self, department_id, date_start=None, date_end=None):
        """Return the cards of the members of the department, across every project they have open
        tasks in.

        Raises AccessError unless _check_department_mode_access lets the user see that department.
        """
        # the board's active_test, as get_team_dashboard_by_manager pins it
        Project = self.with_context(active_test=True)
        Project.check_access("read")
        # sudo: a project user may not read hr.department and hr.employee
        department = Project.env["hr.department"].sudo().browse(department_id).exists()
        return Project._get_department_dashboard(department, Project._get_openable_departments(), date_start, date_end)

    @api.model
    def _get_manager_dashboard(self, manager_user, openable_users, date_start=None, date_end=None, max_depth=0):
        """The Manager mode of manager_user for a user who opens the teams of openable_users
        (_get_openable_manager_users), which the board computes once for its rows."""
        is_openable = self._check_manager_mode_access(manager_user, openable_users)
        if not manager_user.exists():
            return {"error": self.env._("User not found")}
        # sudo: a project user may not read hr.employee
        manager_employees = self.env["hr.employee"].sudo().search([("user_id", "=", manager_user.id)])
        if not manager_employees:
            return {"error": self.env._("No employee record found for this user"), "manager_name": manager_user.name}
        if not is_openable:
            return {
                # an employee in a company the user cannot select stays unnamed
                "error": self.env._("No employee record found for this user in the selected companies")
                if manager_employees.company_id & self.env.user.company_ids
                else self.env._("No employee record found for this user"),
                "manager_name": manager_user.name,
            }

        subordinate_users = self._get_subordinate_employees(manager_employees, max_depth).user_id.filtered("active")
        if not subordinate_users:
            return {
                "manager_id": manager_user.id,
                "manager_name": manager_user.name,
                "max_depth": max_depth,
                "period_start": date_start or "",
                "period_end": date_end or "",
                "members": [],
            }

        return self._build_multiproject_dashboard(
            subordinate_users,
            date_start,
            date_end,
            label="manager_id",
            label_value=manager_user.id,
            extra={"manager_name": manager_user.name, "max_depth": max_depth},
        )

    @api.model
    def _get_department_dashboard(self, department, openable_departments, date_start=None, date_end=None):
        """The Department mode of department, read as superuser, for a user who opens
        openable_departments (_get_openable_departments), which the board computes once for its rows."""
        is_openable = self._check_department_mode_access(department, openable_departments)
        if not department:
            return {"error": self.env._("Department not found")}
        if not is_openable:
            # a department of a company the user cannot select stays unnamed
            if department.company_id and department.company_id not in self.env.user.company_ids:
                return {"error": self.env._("Department not found")}
            return {"error": self.env._("Department not in the selected companies")}

        employees = self.env["hr.employee"].sudo().search([("department_id", "=", department.id)])
        department_users = employees.user_id.filtered("active")
        if not department_users:
            return {
                "department_id": department.id,
                "department_name": department.complete_name or department.name,
                "period_start": date_start or "",
                "period_end": date_end or "",
                "members": [],
            }

        return self._build_multiproject_dashboard(
            department_users,
            date_start,
            date_end,
            label="department_id",
            label_value=department.id,
            extra={"department_name": department.complete_name or department.name},
        )

    @api.model
    def _check_manager_mode_access(self, manager_user, openable_users):
        """The Manager mode reads the logged hours of a whole team with elevated rights: a user
        opens it for openable_users only. Return whether they do: a user who sees every team, or
        to whom the manager reports, is not refused, but told the manager has no employee, in the
        selected companies when the manager has one in a company the user may select."""
        is_openable = self.env.su or manager_user in openable_users
        if not (is_openable or self._sees_every_team() or manager_user in self._get_users_below()):
            raise AccessError(
                self.env._(
                    "You can open the Team Dashboard by manager only for yourself and the managers who report to you."
                )
            )
        return is_openable

    @api.model
    def _check_department_mode_access(self, department, openable_departments):
        """The Department mode reads the logged hours of a department with elevated rights: a user
        opens it for openable_departments only. Return whether they do: a user who sees every team,
        or whose own department it is, is not refused, but told the department does not exist or is
        outside the companies of the context."""
        is_openable = self.env.su or department in openable_departments
        if not (is_openable or self._sees_every_team() or self._is_own_department(department)):
            raise AccessError(
                self.env._(
                    "You can open the Team Dashboard by department only for your own department or one you manage."
                )
            )
        return is_openable

    @api.model
    def _get_openable_manager_users(self):
        """The managers whose team the user opens, on the screen and on the board: themselves, and
        the users of the public employees of _get_openable_manager_employee_domain."""
        employees = self.env["hr.employee.public"].search(self._get_openable_manager_employee_domain())
        return employees.user_id | self.env["res.users"].browse(self.env.uid)

    @api.model
    def _get_openable_manager_employee_domain(self):
        """The public employees, in a company of the context, of the users whose team the user opens
        besides their own: those below one of their employees, or any for _sees_every_team. Under
        active_test, which the screen and the board pin, these and the user's own employees are the
        active ones.

        Odoo lets an employee manage their own manager, or themselves: the walk visits each
        employee once. Odoo 20 gives public employees no company rule: the company is a condition here.
        """
        domain = Domain("user_id", "!=", False) & Domain("company_id", "in", self.env.companies.ids)
        if self._sees_every_team():
            return domain
        return domain & Domain("user_id", "in", self._get_users_below().ids)

    @api.model
    def _get_users_below(self):
        """The users of the active employees below one of the user's employees, in any company."""
        # archived employees too: the managers below an archived one still report to the user
        own_employees = self._own_employees().with_context(active_test=False)
        return self._get_subordinate_employees(own_employees).filtered("active").user_id

    @api.model
    def _get_openable_departments(self):
        """The departments the user opens, on the screen and on the board."""
        return self.env["hr.department"].with_context(active_test=False).search(self._get_openable_department_domain())

    @api.model
    def _get_openable_department_domain(self):
        """The departments, of a company of the context or of none, the user opens: every one for
        _sees_every_team, else those of their employees and those one of them manages. A database
        may switch the departments' company rule off: the company is a condition here."""
        domain = Domain("company_id", "in", [*self.env.companies.ids, False])
        if self._sees_every_team():
            return domain
        return domain & self._get_own_department_domain()

    @api.model
    def _get_own_department_domain(self):
        """The departments of the user's employees and those one of them manages, in any company."""
        own_employees = self._own_employees()
        return Domain("id", "in", own_employees.department_id.ids) | Domain("manager_id", "in", own_employees.ids)

    @api.model
    def _is_own_department(self, department):
        # sudo: a project user may not read hr.department, nor one of a company outside the context
        return bool(department.sudo().filtered_domain(self._get_own_department_domain()))

    @api.model
    def _sees_every_team(self):
        return self.env.is_superuser() or any(self.env.user.has_group(group) for group in EVERY_TEAM_GROUPS)

    @api.model
    def _own_employees(self):
        # sudo: a project user may not read hr.employee, and these are the user's own employees
        return self.env["hr.employee"].sudo().search([("user_id", "=", self.env.uid)])

    def _get_subordinate_employees(self, manager_employees, max_depth=0):
        """Return the employees below the managers, down to `max_depth` levels (0: all levels).

        Each employee is visited once, and the managers stay out of their own team, where the
        hierarchy loops back to them too.
        """
        subordinates = self.env["hr.employee"].sudo()
        level = self._get_direct_reports(manager_employees) - manager_employees
        depth = 1
        while level:
            subordinates |= level
            if max_depth and depth >= max_depth:
                break
            level = self._get_direct_reports(level) - subordinates - manager_employees
            depth += 1
        return subordinates

    @api.model
    def _get_direct_reports(self, employees):
        # not child_ids: Odoo 20 keeps archived employees out of it, whatever active_test
        return employees.search([("parent_id", "in", employees.ids)])

    def _build_multiproject_dashboard(self, users, date_start, date_end, label, label_value, extra=None):
        """Return the cards of the users with open tasks, across all their projects, under the key
        label holding label_value, with the keys of extra. Their hours are those of their timesheets
        in the companies of the context."""
        # sudo: the manager and department modes read the hours of other users
        Timesheet = self.env["account.analytic.line"].sudo()
        today = fields.Date.context_today(self)
        period_start, period_end = self._get_dashboard_period(date_start, date_end, today)
        # A task without project is a personal to-do, as the ones Odoo gives every new user; the
        # template filters are those of Odoo's own cross-project task lists.
        open_tasks = self.env["project.task"].search(
            [
                ("user_ids", "in", users.ids),
                ("project_id", "!=", False),
                ("has_template_ancestor", "=", False),
                ("has_project_template", "=", False),
                ("is_closed", "=", False),
            ]
        )
        # the lines of invoices and bills are analytic lines too, with a quantity for hours and no project
        timesheet_domain = [("project_id", "!=", False), ("company_id", "in", self.env.companies.ids)]
        period_hours_by_user = self._get_logged_hours_by_user(
            Timesheet,
            timesheet_domain + [("user_id", "in", users.ids), ("date", ">=", period_start), ("date", "<=", period_end)],
        )
        member_today_by_user = self._get_member_today_by_user(users)
        today_hours_by_user = self._get_today_hours_by_user(Timesheet, timesheet_domain, member_today_by_user)

        members_data = []
        for user in users.sorted(key=lambda user: user.name):
            user_tasks = open_tasks.filtered(lambda task, user=user: user in task.user_ids)
            if not user_tasks:
                continue
            member_data = self._get_member_data(
                user,
                user_tasks,
                period_hours_by_user.get(user.id, 0.0),
                today_hours_by_user.get(user.id, 0.0),
                today,
            )
            member_data["project_names"] = sorted(set(user_tasks.project_id.mapped(self._get_project_label)))
            members_data.append(member_data)

        result = {
            label: label_value,
            "period_start": fields.Date.to_string(period_start),
            "period_end": fields.Date.to_string(period_end),
            "members": members_data,
            "project_count": len(open_tasks.project_id),
        }
        if extra:
            result.update(extra)
        return result

    @api.model
    def _get_dashboard_period(self, date_start, date_end, today):
        """Return the dates given, by default the week of today: from the first day of the week of
        the user's language (`res.lang.week_start`, 1 for Monday) to six days later."""
        if date_start:
            period_start = fields.Date.from_string(date_start)
        else:
            week_start_weekday = int(get_lang(self.env).week_start) - 1
            period_start = today - timedelta(days=(today.weekday() - week_start_weekday) % 7)
        period_end = fields.Date.from_string(date_end) if date_end else period_start + timedelta(days=6)
        return period_start, period_end

    @api.model
    def _get_logged_hours_by_user(self, timesheets, domain):
        return {user.id: hours for user, hours in timesheets._read_group(domain, ["user_id"], ["unit_amount:sum"])}

    @api.model
    def _get_member_today_by_user(self, users):
        """Return each user's today on the day core dates their timesheet lines on: in the timezone of
        their preferences, in UTC when they have none, as the web client sends no other."""
        # "UTC", not tz=False: an empty tz falls back to the viewer's
        return {user.id: fields.Date.context_today(self.with_context(tz=user.tz or "UTC")) for user in users}

    @api.model
    def _get_today_hours_by_user(self, timesheets, domain, member_today_by_user):
        hours_by_user = {}
        for day in set(member_today_by_user.values()):
            day_user_ids = [user_id for user_id, member_today in member_today_by_user.items() if member_today == day]
            hours_by_user.update(
                self._get_logged_hours_by_user(
                    timesheets, domain + [("user_id", "in", day_user_ids), ("date", "=", day)]
                )
            )
        return hours_by_user

    @api.model
    def _get_member_planning_day(self, user):
        """Return the day the estimated completion of user is planned from: their today in the
        timezone of their preferences, else in their employee's, else in UTC. Not the UTC day their
        Hours today count on without a timezone: the plan runs in their employee's timezone, where
        that day can be over."""
        # sudo: the viewer may not read employees
        planning_tz = user.tz or user.sudo().employee_id.tz or "UTC"
        return fields.Date.context_today(self.with_context(tz=planning_tz))

    def _get_member_data(self, user, user_tasks, hours_logged_period, hours_logged_today, today):
        """Return the card of user, whose open tasks are user_tasks: the current task is the
        in-progress one written last, the others queue in sequence before the backlog.

        Overdue follows the viewer's today, the ETA the user's own _get_member_planning_day."""
        in_progress_tasks = user_tasks.filtered(lambda task: task.stage_id.is_in_progress)
        backlog_tasks = user_tasks.filtered(lambda task: task.stage_id.is_backlog)
        other_tasks = user_tasks - in_progress_tasks - backlog_tasks
        current_task = in_progress_tasks.sorted(key=lambda task: task.write_date or task.create_date, reverse=True)[:1]
        queue_tasks = (in_progress_tasks - current_task).sorted(key=lambda task: (task.sequence, task.id))
        total_remaining = max(sum(user_tasks.mapped("remaining_hours")), 0)
        return {
            "user_id": user.id,
            "user_name": user.name,
            "user_avatar_url": "/web/image/res.users/%d/avatar_128" % user.id,
            "current_task": self._task_to_dict(current_task, today) if current_task else False,
            "queue": [self._task_to_dict(task, today) for task in queue_tasks],
            "backlog": [
                self._task_to_dict(task, today)
                for task in backlog_tasks.sorted(key=lambda task: (task.sequence, task.id))
            ],
            "other": [self._task_to_dict(task, today) for task in other_tasks.sorted("sequence")],
            "total_open_tasks": len(user_tasks),
            "total_planned_hours": sum(user_tasks.mapped("allocated_hours")),
            "total_remaining_hours": sum(user_tasks.mapped("remaining_hours")),
            "hours_logged_period": hours_logged_period,
            "hours_logged_today": hours_logged_today,
            "estimated_completion": self._compute_user_eta(user, total_remaining, self._get_member_planning_day(user)),
        }

    def _compute_user_eta(self, user, remaining_hours, today):
        """Return the date ("YYYY-MM-DD") the user's remaining hours are worked off from midnight of
        today in their employee's timezone, on the working time and leaves of their employee, or
        False when there is nothing left to work.

        Without an employee or a calendar the user works 5.7 hours a day, every day.
        """
        if remaining_hours <= 0:
            return False

        # sudo: the viewer may not read employees, and the ETA shows nothing of theirs but a date
        employee = user.sudo().employee_id
        calendar = employee.resource_calendar_id or (employee and self.env.company.resource_calendar_id)
        if not calendar:
            work_days = remaining_hours / 5.7
            eta = today + timedelta(days=int(work_days) + (1 if work_days % 1 else 0))
            return fields.Date.to_string(eta)

        start = datetime.combine(today, time.min, tzinfo=ZoneInfo(employee.tz or "UTC"))
        eta_dt = calendar.plan_hours(remaining_hours, start, compute_leaves=True, resource=employee.resource_id)
        return eta_dt and fields.Date.to_string(eta_dt.date())

    @api.model
    def _get_project_label(self, project):
        # sudo: who reads a task reads the name of its project, as core reads a many-to-one
        project = project.sudo()
        return "%s - %s" % (project.key, project.name) if project.key else project.name or ""

    @api.model
    def _get_progress_percentage(self, task):
        """Odoo 20 keeps a task's progress as a ratio; the dashboard and the Gantt chart show a percentage."""
        return round(task.progress * 100, 2)

    def _task_to_dict(self, task, today):
        """Convert a task record to a dictionary for the dashboard."""
        project = task.project_id
        planned_day_start = task._gantt_local_date(task.planned_date_start)
        planned_day_end = task._gantt_local_date(task.planned_date_end)
        return {
            "id": task.id,
            "name": task.name,
            "key": task.key or "",
            "stage_id": task.stage_id.id,
            "stage_name": task.stage_id.name,
            "planned_hours": task.allocated_hours,
            "effective_hours": task.effective_hours,
            "remaining_hours": task.remaining_hours,
            "progress": self._get_progress_percentage(task),
            "planned_date_start": fields.Date.to_string(planned_day_start) or "",
            "planned_date_end": fields.Date.to_string(planned_day_end) or "",
            "is_overdue": bool(planned_day_end) and planned_day_end < today,
            "kanban_state": task.state,
            "priority": task.priority,
            "sequence": task.sequence,
            "is_subtask": bool(task.parent_id),
            # sudo: who reads a sub-task reads the key of its parent, as core reads a many-to-one
            "parent_key": task.parent_id.sudo().key or "",
            "project_id": project.id,
            "project_name": self._get_project_label(project),
        }
