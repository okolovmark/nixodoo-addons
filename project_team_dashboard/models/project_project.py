from datetime import datetime, timedelta

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .project_task import local_date, local_datetime

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
                    _("The active baseline of project '%s' belongs to another project.") % record.name
                )

    def save_gantt_baselines(self):
        if not self.env.user.has_group("project.group_project_manager"):
            raise AccessError(_("Only Project Managers can save a baseline."))
        projects = self.browse(list(dict.fromkeys(self.ids))).exists()
        if not projects:
            return True
        projects.check_access_rights("write")
        projects.check_access_rule("write")
        projects = projects.sudo()
        self.env.cr.execute("SELECT id FROM project_project WHERE id IN %s FOR UPDATE", [tuple(projects.ids)])
        Baseline = projects.env["project.baseline"]
        open_tasks = projects.env["project.task"].search(
            [
                ("project_id", "in", projects.ids),
                ("stage_id.fold", "=", False),
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
        for project, new_baseline in zip(projects, new_baselines):
            project.baseline_id = new_baseline
        Baseline.search([("project_id", "in", projects.ids), ("id", "not in", new_baselines.ids)]).unlink()
        return True

    @api.model
    def get_team_dashboard_data(self, project_id, date_start=None, date_end=None):
        """Return structured data for the team workload dashboard.

        Args:
            project_id: ID of the project to get dashboard data for.
            date_start: Optional start date string (YYYY-MM-DD) for utilization period.
            date_end: Optional end date string (YYYY-MM-DD) for utilization period.

        Returns:
            dict with project info and list of team member data dicts.
        """
        # archived projects, tasks, users and employees stay out whatever the caller sends
        return self.with_context(active_test=True)._get_team_dashboard_data(project_id, date_start, date_end)

    @api.model
    def _get_team_dashboard_data(self, project_id, date_start, date_end):
        self.check_access_rights("read")
        project = self.env["project.project"].search([("id", "=", project_id)], limit=1)
        if not project:
            return {"error": _("Project not found")}

        Timesheet = self.env["account.analytic.line"]
        today = fields.Date.context_today(self)
        period_start, period_end = self._get_dashboard_period(date_start, date_end, today)
        open_tasks = self.env["project.task"].search([("project_id", "=", project_id), ("stage_id.fold", "=", False)])
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
                member_today_by_user[user.id],
            )
            for user in open_tasks.user_ids.sorted(key=lambda user: user.name)
        ]

        # Unassigned tasks
        unassigned_tasks = open_tasks.filtered(lambda t: not t.user_ids)
        if unassigned_tasks:
            unassigned_in_progress = unassigned_tasks.filtered(
                lambda t: t.stage_id.is_in_progress,
            )
            unassigned_backlog = unassigned_tasks.filtered(
                lambda t: t.stage_id.is_backlog,
            )
            unassigned_other = unassigned_tasks - unassigned_in_progress - unassigned_backlog

            members_data.append(
                {
                    "user_id": False,
                    "user_name": _("Unassigned"),
                    "user_avatar_url": False,
                    "current_task": False,
                    "queue": [self._task_to_dict(t, today) for t in unassigned_in_progress.sorted("sequence")],
                    "backlog": [self._task_to_dict(t, today) for t in unassigned_backlog.sorted("sequence")],
                    "other": [self._task_to_dict(t, today) for t in unassigned_other.sorted("sequence")],
                    "total_open_tasks": len(unassigned_tasks),
                    "total_planned_hours": sum(unassigned_tasks.mapped("planned_hours")),
                    "total_remaining_hours": sum(
                        unassigned_tasks.mapped("remaining_hours"),
                    ),
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

    def _compute_user_eta(self, user, remaining_hours, today):
        """Compute estimated completion date for a user's remaining work.

        Uses the employee's resource calendar and leaves to project when
        all remaining hours of work will be completed.

        Returns:
            str: Date string (YYYY-MM-DD) or False if cannot compute.
        """
        if remaining_hours <= 0:
            return False

        # sudo: the reader may not read employees, and the ETA shows nothing of theirs but a date
        employee = user.sudo().employee_id
        if not employee:
            # No employee record: fall back to simple 40h/week estimate
            work_days = remaining_hours / 5.7
            eta = today + timedelta(days=int(work_days) + (1 if work_days % 1 else 0))
            return fields.Date.to_string(eta)

        calendar = employee.resource_calendar_id
        if not calendar:
            calendar = self.env.company.resource_calendar_id
        if not calendar:
            work_days = remaining_hours / 5.7
            eta = today + timedelta(days=int(work_days) + (1 if work_days % 1 else 0))
            return fields.Date.to_string(eta)

        # Use plan_hours with compute_leaves=True to account for holidays/leaves
        tz = pytz.timezone(employee.tz or "UTC")
        now_dt = datetime.combine(today, datetime.min.time())
        now_dt = tz.localize(now_dt)

        eta_dt = calendar.plan_hours(
            remaining_hours,
            now_dt,
            compute_leaves=True,
            resource=employee.resource_id,
        )
        if eta_dt:
            return fields.Date.to_string(eta_dt.date())
        return False

    @api.model
    def get_gantt_chart_data(
        self, project_id=None, show_completed=False, project_ids=None, include_stage_history=False
    ):
        """Return Gantt chart data for the given project(s).

        Supports both single project_id (backward compat) and multi project_ids.

        Args:
            project_id: ID of a single project (legacy, backward compat).
            show_completed: If True, include tasks in folded (closed) stages.
            project_ids: List of project IDs for multi-project gantt.
            include_stage_history: If True, fill each task's stage_history with its stage visits.

        Returns:
            dict with keys:
                - tasks: list of task dicts
                - today: the reader's today (YYYY-MM-DD)
                - tasks_without_dates: int count of the unscheduled tasks
                - user_tz: the reader's time zone, which the days of the tasks are in
                - columns: the attribute columns the user picked for the chart
                - is_project_manager: whether the user may save baselines
                - unscheduled: list of task dicts of the tasks without any date
        """
        self.check_access_rights("read")
        today = fields.Date.context_today(self)
        result = {
            "tasks": [],
            "today": fields.Date.to_string(today),
            "tasks_without_dates": 0,
            "user_tz": self._get_reader_tz() or "UTC",
            "columns": self.env.user._get_gantt_columns(),
            "is_project_manager": self.env.user.has_group("project.group_project_manager"),
            "unscheduled": [],
        }

        # Normalize to a list of project IDs
        if project_ids:
            pids = project_ids
        elif project_id:
            pids = [project_id]
        else:
            return result

        projects = self.env["project.project"].search([("id", "in", pids)])
        if not projects:
            return result

        is_multi = len(pids) > 1
        project_name_map = {project.id: self._get_project_label(project) for project in projects}

        Task = self.env["project.task"]

        # not the requested ids: a follower of a task reads it in a project they cannot read
        domain = [
            ("project_id", "in", projects.ids),
            "|",
            "|",
            ("planned_date_start", "!=", False),
            ("planned_date_end", "!=", False),
            ("date_deadline", "!=", False),
        ]
        if not show_completed:
            domain.insert(1, ("stage_id.fold", "=", False))

        all_tasks = Task.search(domain)

        task_ids_set = set(all_tasks.ids)

        # Recursively walk up the parent chain to include all ancestors
        # whose children are in the set (even if the ancestor has no dates).
        ancestors_needed = self.env["project.task"]
        visited = set(task_ids_set)
        queue = all_tasks
        while queue:
            next_queue = self.env["project.task"]
            readable_parents = self._get_gantt_readable_parents(queue)
            for task in queue:
                parent = task.parent_id
                if parent and parent.id not in visited and parent in readable_parents:
                    if not parent.stage_id.fold or show_completed:
                        visited.add(parent.id)
                        ancestors_needed |= parent
                        next_queue |= parent
            queue = next_queue

        all_tasks |= ancestors_needed
        task_ids_set.update(ancestors_needed.ids)

        children_map = {}  # task_id -> [child_ids in set]
        for task in all_tasks:
            children_map[task.id] = [c.id for c in task.child_ids if c.id in task_ids_set]

        unscheduled_domain = [
            ("project_id", "in", projects.ids),
            ("id", "not in", list(task_ids_set)),
            ("planned_date_start", "=", False),
            ("planned_date_end", "=", False),
            ("date_deadline", "=", False),
        ]
        if not show_completed:
            unscheduled_domain.append(("stage_id.fold", "=", False))
        unscheduled_tasks = Task.search(unscheduled_domain)

        writable_ids = set()
        if Task.check_access_rights("write", raise_exception=False):
            writable_ids = set((all_tasks | unscheduled_tasks)._filter_access_rules("write").ids)

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
            # Determine dates
            date_start = task.planned_date_start
            date_end = task.planned_date_end
            has_planned_dates = bool(date_start and date_end)

            child_ids_in_set = children_map[task.id]

            # If only one date, create a 1-day range
            if date_start and not date_end:
                date_end = date_start + timedelta(days=1)
            elif date_end and not date_start:
                date_start = date_end - timedelta(days=1)

            is_deadline_only = not date_start and not date_end and bool(task.date_deadline)
            if is_deadline_only:
                date_start = date_end = datetime.combine(task.date_deadline, datetime.min.time())

            if not date_start and not date_end:
                # Skip tasks with no dates at all (shouldn't happen often)
                continue
            # a deadline-only bar sits on the Deadline's own day
            end_day = date_end.date() if is_deadline_only else self._get_reader_day(date_end)

            stage = task.stage_id

            # User names
            user_names = ", ".join(task.user_ids.mapped("name")) if task.user_ids else ""

            parent_id = task.parent_id.id if task.parent_id else False
            # Only keep parent_id if parent is in our dataset
            if parent_id and parent_id not in task_ids_set:
                parent_id = False

            task_dict = {
                "id": task.id,
                "name": task.name,
                "key": task.key or "",
                "parent_id": parent_id,
                "child_ids": child_ids_in_set,
                "planned_date_start": str(date_start),
                "planned_date_end": str(date_end),
                # the bar pads a missing date: these say which dates are the task's own
                "has_planned_date_start": bool(task.planned_date_start),
                "has_planned_date_end": bool(task.planned_date_end),
                "stage_id": stage.id,
                "stage_name": stage.name,
                "stage_sequence": stage.sequence,
                "is_in_progress": stage.is_in_progress,
                "is_backlog": stage.is_backlog,
                "is_closed": stage.fold,
                "is_overdue": end_day < today,
                "user_names": user_names,
                "user_list": [{"id": u.id, "name": u.name} for u in task.user_ids],
                "progress": task.progress,
                "planned_hours": task.planned_hours,
                "remaining_hours": task.remaining_hours,
                "depend_on_ids": [d.id for d in task.depend_on_ids if d.id in task_ids_set],
                "task_type": task.task_type or "",
            }
            task_dict.update(task._gantt_task_attributes(writable_ids, lags_by_task.get(task.id, {})))
            baseline_line = baseline_by_task.get(task.id, self.env["project.baseline.line"])
            baseline_end = self._get_reader_day(baseline_line.planned_date_end)
            plan_start = has_planned_dates and self._get_reader_day(task.planned_date_start)
            plan_end = has_planned_dates and self._get_reader_day(task.planned_date_end)
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
            # Include project_name for multi-project mode
            if is_multi:
                task_dict["project_name"] = project_name_map.get(task.project_id.id, "")
            tasks_data.append(task_dict)

        unscheduled_data = []
        for task in unscheduled_tasks:
            task_dict = {
                "id": task.id,
                "name": task.name,
                "key": task.key or "",
                "stage_name": task.stage_id.name or "",
                "is_closed": task.stage_id.fold,
                "user_names": ", ".join(task.user_ids.mapped("name")),
                "planned_hours": task.planned_hours,
                "task_type": task.task_type or "",
            }
            task_dict.update(task._gantt_task_attributes(writable_ids, {}))
            if is_multi:
                task_dict["project_name"] = project_name_map.get(task.project_id.id, "")
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
        import base64
        import io

        self.check_access_rights("read")
        if not project_ids:
            return {"error": _("No projects selected")}

        gantt_data = self.get_gantt_chart_data(project_ids=project_ids, show_completed=False)
        tasks = gantt_data.get("tasks", [])

        tasks_with_dates = [t for t in tasks if t.get("planned_date_start") and t.get("planned_date_end")]
        if not tasks_with_dates:
            return {"error": _("No tasks with planned dates")}
        bar_ends_by_task_id = {task["id"]: self._get_gantt_bar_ends(task) for task in tasks_with_dates}
        tasks_with_dates.sort(key=lambda task: bar_ends_by_task_id[task["id"]])
        bar_days_by_task_id = {
            task_id: (bar_start.date(), bar_end.date()) for task_id, (bar_start, bar_end) in bar_ends_by_task_id.items()
        }

        min_date = min(start_day for start_day, _end_day in bar_days_by_task_id.values()) - timedelta(days=1)
        max_date = max(end_day for _start_day, end_day in bar_days_by_task_id.values()) + timedelta(days=1)

        try:
            import xlsxwriter
        except ImportError:
            return {"error": _("xlsxwriter not available")}

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        ws = wb.add_worksheet("Gantt Chart")

        # Formats
        header_fmt = wb.add_format(
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
        task_fmt = wb.add_format(
            {
                "border": 1,
                "text_wrap": True,
                "valign": "vcenter",
                "font_size": 9,
            }
        )
        date_fmt = wb.add_format(
            {
                "border": 1,
                "valign": "vcenter",
                "font_size": 9,
                "num_format": "dd mmm",
            }
        )
        pct_fmt = wb.add_format(
            {
                "border": 1,
                "valign": "vcenter",
                "font_size": 9,
                "num_format": "0%",
                "align": "center",
            }
        )
        # Gantt bar colors
        bar_in_progress = wb.add_format({"bg_color": "#2ecb71", "border": 1})
        bar_done = wb.add_format({"bg_color": "#27ae60", "border": 1})
        bar_backlog = wb.add_format({"bg_color": "#5dade2", "border": 1})
        bar_overdue = wb.add_format({"bg_color": "#ec7063", "border": 1})
        bar_other = wb.add_format({"bg_color": "#aab7b8", "border": 1})
        bar_milestone = wb.add_format(
            {
                "bg_color": "#8e44ad",
                "font_color": "#FFFFFF",
                "border": 1,
                "bold": True,
                "align": "center",
                "font_size": 9,
            }
        )
        empty_fmt = wb.add_format({"border": 0})
        date_header_fmt = wb.add_format(
            {
                "bold": True,
                "bg_color": "#F0F0F0",
                "border": 1,
                "font_size": 8,
                "align": "center",
                "valign": "vcenter",
                "rotation": 90,
            }
        )
        today_header_fmt = wb.add_format(
            {
                "bold": True,
                "bg_color": "#FDEBD0",
                "border": 1,
                "font_size": 8,
                "align": "center",
                "valign": "vcenter",
                "rotation": 90,
            }
        )

        # Fixed columns
        fixed_cols = [_("Key"), _("Task"), _("Type"), _("Assignee"), _("Start"), _("End"), _("Progress"), _("Stage")]
        FIXED = len(fixed_cols)

        # Set column widths
        ws.set_column(0, 0, 12)  # Key
        ws.set_column(1, 1, 35)  # Task name
        ws.set_column(2, 2, 14)  # Type
        ws.set_column(3, 3, 18)  # Assignee
        ws.set_column(4, 4, 11)  # Start
        ws.set_column(5, 5, 11)  # End
        ws.set_column(6, 6, 9)  # Progress
        ws.set_column(7, 7, 14)  # Stage

        # Date columns -- one per day
        total_days = (max_date - min_date).days + 1
        for d in range(total_days):
            ws.set_column(FIXED + d, FIXED + d, 3)

        # Row 0: Header
        ws.set_row(0, 60)
        for i, h in enumerate(fixed_cols):
            ws.write(0, i, h, header_fmt)

        today = fields.Date.to_date(gantt_data["today"])
        for d in range(total_days):
            day = min_date + timedelta(days=d)
            fmt = today_header_fmt if day == today else date_header_fmt
            ws.write(0, FIXED + d, day.strftime("%d %b"), fmt)

        # Data rows
        for row_idx, task in enumerate(tasks_with_dates, start=1):
            ws.set_row(row_idx, 22)
            key = task.get("key", "")
            name = task.get("name", "")
            assignee = task.get("user_names", "")
            start_dt, end_dt = bar_days_by_task_id[task["id"]]

            # Progress
            progress = task.get("progress", 0) or 0
            planned_h = task.get("planned_hours", 0)
            remaining_h = task.get("remaining_hours", 0)
            if not progress and planned_h:
                progress = ((planned_h - remaining_h) / planned_h) * 100
            progress = max(0, min(100, progress))

            stage = task.get("stage_name", "")
            task_type = (task.get("task_type", "") or "").replace("_", " ").title()
            is_milestone = task_type.lower() == "milestone" or task.get("is_deadline_only")

            ws.write(row_idx, 0, key, task_fmt)
            ws.write(row_idx, 1, name, task_fmt)
            ws.write(row_idx, 2, task_type, task_fmt)
            ws.write(row_idx, 3, assignee, task_fmt)
            ws.write_datetime(row_idx, 4, datetime.combine(start_dt, datetime.min.time()), date_fmt)
            ws.write_datetime(row_idx, 5, datetime.combine(end_dt, datetime.min.time()), date_fmt)
            ws.write(row_idx, 6, progress / 100.0, pct_fmt)
            ws.write(row_idx, 7, stage, task_fmt)

            # Gantt bar cells -- pick color based on task state
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

            for d in range(total_days):
                day = min_date + timedelta(days=d)
                col = FIXED + d
                if is_milestone:
                    # Milestones are a single point: only mark the start date
                    if day == start_dt:
                        ws.write(row_idx, col, "\u25c6", bar_fmt)
                    else:
                        ws.write(row_idx, col, "", empty_fmt)
                elif start_dt <= day <= end_dt:
                    ws.write(row_idx, col, "", bar_fmt)
                else:
                    ws.write(row_idx, col, "", empty_fmt)

        # Freeze panes: freeze fixed columns + header row
        ws.freeze_panes(1, FIXED)

        wb.close()
        output.seek(0)
        data = base64.b64encode(output.read()).decode("utf-8")

        Project = self.env["project.project"]
        first_readable_project = (Project.browse(project_ids) & Project.search([("id", "in", project_ids)]))[:1]
        project_name = first_readable_project.name or "Gantt"
        filename = "Gantt_%s.xlsx" % project_name.replace(" ", "_")[:30]

        return {"data": data, "filename": filename}

    @api.model
    def _get_gantt_bar_ends(self, task_data):
        """Return the start and end, naive in the reader's timezone, of the bar of task_data, a task
        of the Gantt payload: the moments the chart draws it from and to."""
        # the payload holds str() of the datetimes, which carries microseconds when the value has them
        bar_ends = [
            fields.Datetime.to_datetime(task_data[key][:19]) for key in ("planned_date_start", "planned_date_end")
        ]
        if task_data["is_deadline_only"]:
            # the payload puts a deadline-only bar at midnight of its Deadline already
            return tuple(bar_ends)
        return tuple(local_datetime(bar_end, self._get_reader_tz()) for bar_end in bar_ends)

    @api.model
    def get_team_dashboard_by_manager(self, user_id, date_start=None, date_end=None, max_depth=0):
        """Return dashboard data for all subordinates of a given user.

        Uses hr.employee parent/child hierarchy to find subordinates.
        Shows member cards for each subordinate across ALL projects where
        they have open tasks.

        Args:
            user_id: ID of the manager user (defaults to current user).
            date_start: Optional start date string (YYYY-MM-DD).
            date_end: Optional end date string (YYYY-MM-DD).
            max_depth: Max depth of subordinates. 0 = all levels.

        Returns:
            dict with manager info and list of team member data dicts.

        Opens the managers of _get_openable_manager_users; another raises AccessError, except for a
        user who sees every team, who gets the error dict of a user not found or of a manager
        without an employee in the companies of the context instead.
        Archived employees and tasks stay out whatever the caller's active_test.
        """
        return self.with_context(active_test=True)._get_team_dashboard_by_manager(
            user_id, date_start, date_end, max_depth
        )

    @api.model
    def _get_team_dashboard_by_manager(self, user_id, date_start, date_end, max_depth):
        self.check_access_rights("read")

        if not user_id:
            user_id = self.env.uid
        manager_user = self.env["res.users"].browse(user_id)
        # Use sudo() for HR reads: project users may lack hr.group_hr_user
        Employee = self.env["hr.employee"].sudo()
        manager_employees = Employee.search([("user_id", "=", user_id)])
        is_openable = self._check_manager_mode_access(manager_user)
        if not manager_user.exists():
            return {"error": _("User not found")}
        if not manager_employees:
            return {
                "error": _("No employee record found for this user"),
                "manager_name": manager_user.name,
            }
        if not is_openable:
            return {
                # a manager without an employee in a company the user may select is answered as if they had none
                "error": _("No employee record found for this user in the selected companies")
                if manager_employees.company_id & self.env.user.company_ids
                else _("No employee record found for this user"),
                "manager_name": manager_user.name,
            }

        subordinate_emps = self._get_subordinate_employees(manager_employees, max_depth)

        # Get user IDs from subordinate employees
        subordinate_users = subordinate_emps.mapped("user_id").filtered(lambda u: u.active)
        if not subordinate_users:
            return {
                "manager_id": user_id,
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
            label_value=user_id,
            extra={"manager_name": manager_user.name, "max_depth": max_depth},
        )

    @api.model
    def get_team_dashboard_by_department(self, department_id, date_start=None, date_end=None):
        """Return dashboard data for all users in a department.

        Shows member cards for each department member across ALL projects
        where they have open tasks.

        Args:
            department_id: ID of the hr.department.
            date_start: Optional start date string (YYYY-MM-DD).
            date_end: Optional end date string (YYYY-MM-DD).

        Returns:
            dict with department info and list of team member data dicts.

        Opens the departments of _get_openable_departments; another raises AccessError, except for
        a user who sees every team and for the user's own department, which get the error dict of a
        department not found or not in the selected companies instead.
        Archived employees and tasks stay out whatever the caller's active_test.
        """
        return self.with_context(active_test=True)._get_team_dashboard_by_department(
            department_id, date_start, date_end
        )

    @api.model
    def _get_team_dashboard_by_department(self, department_id, date_start, date_end):
        self.check_access_rights("read")

        # Use sudo() for HR reads: project users may lack hr.group_hr_user
        Department = self.env["hr.department"].sudo()
        department = Department.browse(department_id).exists()
        is_openable = self._check_department_mode_access(department)
        if not department:
            return {"error": _("Department not found")}
        if not is_openable:
            # a department of a company the user cannot select is answered as if it did not exist
            if department.company_id and department.company_id not in self.env.user.company_ids:
                return {"error": _("Department not found")}
            return {"error": _("Department not in the selected companies")}

        # Get all employees in this department
        employees = (
            self.env["hr.employee"]
            .sudo()
            .search(
                [
                    ("department_id", "=", department_id),
                ]
            )
        )
        dept_users = employees.mapped("user_id").filtered(lambda u: u.active)
        if not dept_users:
            return {
                "department_id": department_id,
                "department_name": department.complete_name or department.name,
                "period_start": date_start or "",
                "period_end": date_end or "",
                "members": [],
            }

        return self._build_multiproject_dashboard(
            dept_users,
            date_start,
            date_end,
            label="department_id",
            label_value=department_id,
            extra={
                "department_name": department.complete_name or department.name,
            },
        )

    @api.model
    def _check_manager_mode_access(self, manager_user):
        """The Manager mode reads the logged hours of a whole team with elevated rights: a user
        opens it for the managers of _get_openable_manager_users only. Return whether they do: a
        user who sees every team is not refused, but told the manager has no employee, in the
        selected companies when the manager has one in a company the user may select."""
        is_openable = self.env.su or manager_user in self._get_openable_manager_users()
        if not is_openable and not self._sees_every_team():
            raise AccessError(
                _("You can open the Team Dashboard by manager only for yourself and the managers who report to you.")
            )
        return is_openable

    @api.model
    def _check_department_mode_access(self, department):
        """The Department mode reads the logged hours of a department with elevated rights: a user
        opens it for the departments of _get_openable_departments only. Return whether they do: a
        user who sees every team, or whose own department it is, is not refused, but told the
        department does not exist or is outside the companies of the context."""
        is_openable = self.env.su or department in self._get_openable_departments()
        is_answered = (
            is_openable
            or self._sees_every_team()
            or bool(department.filtered_domain(self._get_own_department_domain()))
        )
        if not is_answered:
            raise AccessError(
                _("You can open the Team Dashboard by department only for your own department or one you manage.")
            )
        return is_openable

    @api.model
    def _get_openable_manager_users(self):
        """The managers whose team the user opens, on the screen and on the board: themselves, and
        the users with an active employee in a company of the context below one of theirs, or any
        such user for _sees_every_team, whatever the caller's active_test. A database may switch
        the employees' company rules off: the company is a condition here.

        Odoo lets an employee manage their own manager, or themselves: the walk visits each
        employee once.
        """
        employee_domain = [("user_id", "!=", False), ("company_id", "in", self.env.companies.ids)]
        if not self._sees_every_team():
            # archived employees too: the managers below an archived one still report to the user
            own_employees = self._own_employees().with_context(active_test=False)
            users_below = self._get_subordinate_employees(own_employees).filtered("active").user_id
            employee_domain.append(("user_id", "in", users_below.ids))
        manager_users = self.env["hr.employee"].with_context(active_test=True).search(employee_domain).sudo().user_id
        return self.env.user | manager_users

    @api.model
    def _get_openable_departments(self):
        """The departments, of a company of the context or of none, the user opens, on the screen
        and on the board: every one for _sees_every_team, else those of their active employees and
        those one of them manages. A database may switch the departments' company rule off: the
        company is a condition here."""
        domain = [("company_id", "in", [*self.env.companies.ids, False])]
        if not self._sees_every_team():
            domain += self._get_own_department_domain()
        return self.env["hr.department"].with_context(active_test=False).search(domain)

    @api.model
    def _get_own_department_domain(self):
        """The departments of the user's active employees and those one of them manages, in any company."""
        own_employees = self._own_employees()
        return ["|", ("id", "in", own_employees.department_id.ids), ("manager_id", "in", own_employees.ids)]

    @api.model
    def _sees_every_team(self):
        return self.env.is_superuser() or any(self.env.user.has_group(group) for group in EVERY_TEAM_GROUPS)

    @api.model
    def _own_employees(self):
        """The user's active employees, whatever the caller's active_test: an archived one opens no team."""
        # sudo: a project user may not read hr.employee, and these are the user's own employees
        return self.env["hr.employee"].sudo().with_context(active_test=True).search([("user_id", "=", self.env.uid)])

    def _get_subordinate_employees(self, manager_employees, max_depth=0):
        """Return the employees below the managers, down to `max_depth` levels (0: all levels).

        Each employee is visited once, and the managers stay out of their own team, where the
        hierarchy loops back to them too.
        """
        subordinates = self.env["hr.employee"].sudo()
        level = manager_employees.child_ids - manager_employees
        depth = 1
        while level:
            subordinates |= level
            if max_depth and depth >= max_depth:
                break
            level = level.child_ids - subordinates - manager_employees
            depth += 1
        return subordinates

    def _build_multiproject_dashboard(self, users, date_start, date_end, label, label_value, extra=None):
        """Build dashboard data for a set of users across all their projects. Their hours are those
        of their timesheets in the companies of the context.

        Args:
            users: res.users recordset.
            date_start: Start date string or None.
            date_end: End date string or None.
            label: Key name for the result dict (e.g. 'manager_id').
            label_value: Value for the label key.
            extra: Additional dict keys to merge into result.

        Returns:
            dict with members list and metadata.
        """
        # Use sudo() for timesheets: manager/dept view needs cross-user reads
        Timesheet = self.env["account.analytic.line"].sudo()
        today = fields.Date.context_today(self)
        period_start, period_end = self._get_dashboard_period(date_start, date_end, today)
        open_tasks = self.env["project.task"].search([("user_ids", "in", users.ids), ("stage_id.fold", "=", False)])
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
                member_today_by_user[user.id],
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
        """Return the dates given, by default the week of today from Monday to Sunday, as the
        screen's _getDates and the board count it."""
        period_start = fields.Date.from_string(date_start) if date_start else today - timedelta(days=today.weekday())
        period_end = fields.Date.from_string(date_end) if date_end else period_start + timedelta(days=6)
        return period_start, period_end

    @api.model
    def _get_logged_hours_by_user(self, timesheets, domain):
        groups = timesheets.read_group(domain, ["unit_amount"], ["user_id"])
        return {group["user_id"][0]: group["unit_amount"] for group in groups if group["user_id"]}

    @api.model
    def _get_member_today_by_user(self, users):
        """Return each user's today, the day core dates their timesheet lines on: in the timezone of
        their preferences, in UTC, the server's, when they have none."""
        # "UTC", not tz=False: an empty tz falls back to the reader's
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

    def _get_member_data(self, user, user_tasks, hours_logged_period, hours_logged_today, today, member_today):
        """Return the card of user, whose open tasks are user_tasks: the current task is the
        in-progress one written last, the others queue in sequence before the backlog.

        Overdue follows the reader's today, the ETA the user's own member_today."""
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
            "total_planned_hours": sum(user_tasks.mapped("planned_hours")),
            "total_remaining_hours": sum(user_tasks.mapped("remaining_hours")),
            "hours_logged_period": hours_logged_period,
            "hours_logged_today": hours_logged_today,
            "estimated_completion": self._compute_user_eta(user, total_remaining, member_today),
        }

    @api.model
    def _get_reader_tz(self):
        """The reader's time zone, the one context_today counts their today in."""
        return self.env.context.get("tz") or self.env.user.tz

    @api.model
    def _get_reader_day(self, utc_datetime):
        return utc_datetime and local_date(utc_datetime, self._get_reader_tz())

    @api.model
    def _get_gantt_readable_parents(self, tasks):
        """The parents of tasks the Gantt chart shows: those the reader reads, without a project or
        in one they read in the companies of the context, archived or not. A follower reads a task in
        a project they cannot read, which the chart leaves out as it leaves out that project's other
        tasks."""
        parents = tasks.parent_id._filter_access_rules("read")
        readable_projects = (
            self.env["project.project"].with_context(active_test=False).search([("id", "in", parents.project_id.ids)])
        )
        return parents.filtered(lambda parent: not parent.project_id or parent.project_id in readable_projects)

    @api.model
    def _get_project_label(self, project):
        # sudo: who reads a task reads the name of its project, as core reads a many-to-one
        project = project.sudo()
        return "%s - %s" % (project.key, project.name) if project.key else project.name

    def _task_to_dict(self, task, today):
        """Convert a task record to a dictionary for the dashboard, its days the reader's."""
        project = task.project_id
        planned_day_start = self._get_reader_day(task.planned_date_start)
        planned_day_end = self._get_reader_day(task.planned_date_end)
        return {
            "id": task.id,
            "name": task.name,
            "key": task.key or "",
            "stage_id": task.stage_id.id,
            "stage_name": task.stage_id.name,
            "planned_hours": task.planned_hours,
            "effective_hours": task.effective_hours,
            "remaining_hours": task.remaining_hours,
            "progress": task.progress,
            "planned_date_start": fields.Date.to_string(planned_day_start) or "",
            "planned_date_end": fields.Date.to_string(planned_day_end) or "",
            "is_overdue": bool(planned_day_end) and planned_day_end < today,
            "kanban_state": task.kanban_state,
            "priority": task.priority,
            "sequence": task.sequence,
            "is_subtask": bool(task.parent_id),
            # sudo: who reads a sub-task reads the key of its parent, as core reads a many-to-one
            "parent_key": task.parent_id.sudo().key or "",
            "project_id": project.id,
            "project_name": self._get_project_label(project),
        }
