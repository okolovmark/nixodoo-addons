from datetime import datetime, timedelta

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

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
        self.check_access_rights("read")
        project = self.env["project.project"].search([("id", "=", project_id)], limit=1)
        if not project:
            return {"error": "Project not found"}

        Task = self.env["project.task"]
        Timesheet = self.env["account.analytic.line"]

        # Determine date range for utilization
        today = fields.Date.today()
        if date_start:
            period_start = fields.Date.from_string(date_start)
        else:
            # Default: current week (Monday to Sunday)
            period_start = today - timedelta(days=today.weekday())
        if date_end:
            period_end = fields.Date.from_string(date_end)
        else:
            period_end = period_start + timedelta(days=6)

        # Get all open tasks for this project (not in folded/closed stages)
        open_tasks = Task.search(
            [
                ("project_id", "=", project_id),
                ("stage_id.fold", "=", False),
            ],
        )

        # Collect all assigned users
        user_ids_set = set(open_tasks.mapped("user_ids").ids)

        # Pre-fetch all timesheets for the period using read_group
        period_ts_data = Timesheet.read_group(
            [
                ("project_id", "=", project_id),
                ("date", ">=", period_start),
                ("date", "<=", period_end),
            ],
            ["unit_amount"],
            ["user_id"],
        )
        period_hours_by_user = {r["user_id"][0]: r["unit_amount"] for r in period_ts_data if r["user_id"]}

        today_ts_data = Timesheet.read_group(
            [
                ("project_id", "=", project_id),
                ("date", "=", today),
            ],
            ["unit_amount"],
            ["user_id"],
        )
        today_hours_by_user = {r["user_id"][0]: r["unit_amount"] for r in today_ts_data if r["user_id"]}

        members_data = []

        # Build per-user data
        users = self.env["res.users"].browse(list(user_ids_set))
        for user in users.sorted(key=lambda u: u.name):
            user_tasks = open_tasks.filtered(lambda t, u=user: u in t.user_ids)

            # Separate in-progress vs backlog tasks
            in_progress_tasks = user_tasks.filtered(
                lambda t: t.stage_id.is_in_progress,
            )
            backlog_tasks = user_tasks.filtered(lambda t: t.stage_id.is_backlog)
            other_tasks = user_tasks - in_progress_tasks - backlog_tasks

            # Determine current task: prefer in-progress, then most recently written
            current_task = False
            if in_progress_tasks:
                current_task = in_progress_tasks.sorted(
                    key=lambda t: t.write_date or t.create_date,
                    reverse=True,
                )[0]

            # Build task queue: in-progress (excl current) first, then backlog, by sequence
            queue_tasks = (in_progress_tasks - current_task) if current_task else in_progress_tasks
            queue_tasks = queue_tasks.sorted(key=lambda t: (t.sequence, t.id))
            backlog_sorted = backlog_tasks.sorted(key=lambda t: (t.sequence, t.id))

            # Utilization: timesheets from pre-fetched read_group
            hours_logged = period_hours_by_user.get(user.id, 0.0)
            hours_today = today_hours_by_user.get(user.id, 0.0)

            # Compute ETA: when will all remaining work be done?
            total_remaining = max(sum(user_tasks.mapped("remaining_hours")), 0)
            estimated_completion = self._compute_user_eta(
                user,
                total_remaining,
                today,
            )

            members_data.append(
                {
                    "user_id": user.id,
                    "user_name": user.name,
                    "user_avatar_url": "/web/image/res.users/%d/avatar_128" % user.id,
                    "current_task": (self._task_to_dict(current_task, today) if current_task else False),
                    "queue": [self._task_to_dict(t, today) for t in queue_tasks],
                    "backlog": [self._task_to_dict(t, today) for t in backlog_sorted],
                    "other": [self._task_to_dict(t, today) for t in other_tasks.sorted("sequence")],
                    "total_open_tasks": len(user_tasks),
                    "total_planned_hours": sum(user_tasks.mapped("planned_hours")),
                    "total_remaining_hours": sum(user_tasks.mapped("remaining_hours")),
                    "hours_logged_period": hours_logged,
                    "hours_logged_today": hours_today,
                    "estimated_completion": estimated_completion,
                },
            )

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
                    "user_name": "Unassigned",
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
                - today: today's date string (YYYY-MM-DD)
                - tasks_without_dates: int count of the unscheduled tasks
                - user_tz: user's timezone string
        """
        self.check_access_rights("read")
        result = {
            "tasks": [],
            "today": fields.Date.to_string(fields.Date.today()),
            "tasks_without_dates": 0,
            "user_tz": self.env.user.tz or "UTC",
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
        project_name_map = {p.id: ((p.key + " - " + p.name) if p.key else p.name) for p in projects}

        Task = self.env["project.task"]
        today = fields.Date.today()

        domain = [
            ("project_id", "in", pids),
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
            readable_parents = queue.parent_id._filter_access_rules("read")
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

        user_tz = pytz.timezone(result["user_tz"])

        def _local_date(value):
            return value and pytz.utc.localize(value).astimezone(user_tz).date()

        unscheduled_domain = [
            ("project_id", "in", pids),
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
        readable_parent_ids = set((all_tasks | unscheduled_tasks).parent_id._filter_access_rules("read").ids)

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

            # Determine stage flags
            stage = task.stage_id
            if not date_end:
                is_overdue = False
            elif hasattr(date_end, "date"):
                is_overdue = date_end.date() < today
            else:
                is_overdue = date_end < datetime.combine(today, datetime.min.time())

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
                "is_overdue": is_overdue,
                "user_names": user_names,
                "user_list": [{"id": u.id, "name": u.name} for u in task.user_ids],
                "progress": task.progress,
                "planned_hours": task.planned_hours,
                "remaining_hours": task.remaining_hours,
                "depend_on_ids": [d.id for d in task.depend_on_ids if d.id in task_ids_set],
                "task_type": task.task_type or "",
            }
            task_dict.update(
                task._gantt_task_attributes(writable_ids, lags_by_task.get(task.id, {}), readable_parent_ids)
            )
            baseline_line = baseline_by_task.get(task.id, self.env["project.baseline.line"])
            baseline_end = _local_date(baseline_line.planned_date_end)
            plan_start = has_planned_dates and _local_date(task.planned_date_start)
            plan_end = has_planned_dates and _local_date(task.planned_date_end)
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
            task_dict.update(task._gantt_task_attributes(writable_ids, {}, readable_parent_ids))
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
            return {"error": "No projects selected"}

        gantt_data = self.get_gantt_chart_data(project_ids=project_ids, show_completed=False)
        tasks = gantt_data.get("tasks", [])

        # Filter tasks with dates, sort by start date
        tasks_with_dates = [t for t in tasks if t.get("planned_date_start") and t.get("planned_date_end")]
        tasks_with_dates.sort(key=lambda t: t["planned_date_start"])

        if not tasks_with_dates:
            return {"error": "No tasks with planned dates"}

        # Find overall date range
        all_starts = [t["planned_date_start"][:10] for t in tasks_with_dates]
        all_ends = [t["planned_date_end"][:10] for t in tasks_with_dates]
        min_date = datetime.strptime(min(all_starts), "%Y-%m-%d").date()
        max_date = datetime.strptime(max(all_ends), "%Y-%m-%d").date()
        # Pad by a few days
        min_date = min_date - timedelta(days=1)
        max_date = max_date + timedelta(days=1)

        try:
            import xlsxwriter
        except ImportError:
            return {"error": "xlsxwriter not available"}

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
        fixed_cols = ["Key", "Task", "Type", "Assignee", "Start", "End", "Progress", "Stage"]
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

        today = fields.Date.today()
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
            start_str = task["planned_date_start"][:10]
            end_str = task["planned_date_end"][:10]
            start_dt = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_str, "%Y-%m-%d").date()

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

        projects = self.env["project.project"].browse(project_ids)
        project_name = projects[0].name if projects else "Gantt"
        filename = "Gantt_%s.xlsx" % project_name.replace(" ", "_")[:30]

        return {"data": data, "filename": filename}

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

        Raises AccessError unless _check_manager_mode_access lets the user see that manager's team.
        """
        self.check_access_rights("read")

        if not user_id:
            user_id = self.env.uid
        manager_user = self.env["res.users"].browse(user_id)
        # Use sudo() for HR reads: project users may lack hr.group_hr_user
        Employee = self.env["hr.employee"].sudo()
        manager_employees = Employee.search([("user_id", "=", user_id)])
        self._check_manager_mode_access(manager_user)
        if not manager_user.exists():
            return {"error": "User not found"}
        if not manager_employees:
            return {
                "error": "No employee record found for this user",
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

        Raises AccessError unless _check_department_mode_access lets the user see that department.
        """
        self.check_access_rights("read")

        # Use sudo() for HR reads: project users may lack hr.group_hr_user
        Department = self.env["hr.department"].sudo()
        department = Department.browse(department_id).exists()
        self._check_department_mode_access(department)
        if not department:
            return {"error": "Department not found"}

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
        opens it for the managers of _get_openable_manager_users only."""
        if not self.env.su and manager_user not in self._get_openable_manager_users():
            raise AccessError(
                _("You can open the Team Dashboard by manager only for yourself and the managers who report to you.")
            )

    @api.model
    def _check_department_mode_access(self, department):
        """The Department mode reads the logged hours of a department with elevated rights: a user
        opens it for the departments of _get_openable_departments only."""
        if not self.env.su and department not in self._get_openable_departments():
            raise AccessError(
                _("You can open the Team Dashboard by department only for your own department or one you manage.")
            )

    @api.model
    def _get_openable_manager_users(self):
        """The managers whose team the user opens, on the screen and on the board: themselves, and
        the users with an employee below one of theirs, or any user for _sees_every_team, when the
        user finds one of their active employees (the employees' company rules).

        Odoo lets an employee manage their own manager, or themselves: the walk visits each
        employee once.
        """
        employee_domain = [("user_id", "!=", False)]
        if not self._sees_every_team():
            # archived employees too: the managers below an archived one still report to the user
            own_employees = self._own_employees().with_context(active_test=False)
            users_below = self._get_subordinate_employees(own_employees).filtered("active").user_id
            employee_domain = [("user_id", "in", users_below.ids)]
        return self.env.user | self.env["hr.employee"].search(employee_domain).sudo().user_id

    @api.model
    def _get_openable_departments(self):
        """The departments the user opens, on the screen and on the board, among those they find
        (the departments' company rules): every one for _sees_every_team, else those of their
        employees and those one of them manages."""
        Department = self.env["hr.department"].with_context(active_test=False)
        if self._sees_every_team():
            return Department.search([])
        own_employees = self._own_employees()
        return Department.search(
            ["|", ("id", "in", own_employees.department_id.ids), ("manager_id", "in", own_employees.ids)]
        )

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
        """Build dashboard data for a set of users across all their projects.

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
        Task = self.env["project.task"]
        # Use sudo() for timesheets: manager/dept view needs cross-user reads
        Timesheet = self.env["account.analytic.line"].sudo()
        today = fields.Date.today()

        if date_start:
            period_start = fields.Date.from_string(date_start)
        else:
            period_start = today - timedelta(days=today.weekday())
        if date_end:
            period_end = fields.Date.from_string(date_end)
        else:
            period_end = period_start + timedelta(days=6)

        user_ids_list = users.ids

        # Get all open tasks for these users across all projects
        open_tasks = Task.search(
            [
                ("user_ids", "in", user_ids_list),
                ("stage_id.fold", "=", False),
            ]
        )

        # Pre-fetch timesheets for all users in period (across all projects)
        period_ts_data = Timesheet.read_group(
            [
                ("user_id", "in", user_ids_list),
                ("date", ">=", period_start),
                ("date", "<=", period_end),
            ],
            ["unit_amount"],
            ["user_id"],
        )
        period_hours_by_user = {r["user_id"][0]: r["unit_amount"] for r in period_ts_data if r["user_id"]}

        today_ts_data = Timesheet.read_group(
            [
                ("user_id", "in", user_ids_list),
                ("date", "=", today),
            ],
            ["unit_amount"],
            ["user_id"],
        )
        today_hours_by_user = {r["user_id"][0]: r["unit_amount"] for r in today_ts_data if r["user_id"]}

        # Collect unique projects for the result
        project_ids_set = set(open_tasks.mapped("project_id").ids)

        members_data = []
        for user in users.sorted(key=lambda u: u.name):
            user_tasks = open_tasks.filtered(lambda t, u=user: u in t.user_ids)
            if not user_tasks:
                continue

            in_progress_tasks = user_tasks.filtered(
                lambda t: t.stage_id.is_in_progress,
            )
            backlog_tasks = user_tasks.filtered(lambda t: t.stage_id.is_backlog)
            other_tasks = user_tasks - in_progress_tasks - backlog_tasks

            current_task = False
            if in_progress_tasks:
                current_task = in_progress_tasks.sorted(
                    key=lambda t: t.write_date or t.create_date,
                    reverse=True,
                )[0]

            queue_tasks = (in_progress_tasks - current_task) if current_task else in_progress_tasks
            queue_tasks = queue_tasks.sorted(key=lambda t: (t.sequence, t.id))
            backlog_sorted = backlog_tasks.sorted(key=lambda t: (t.sequence, t.id))

            hours_logged = period_hours_by_user.get(user.id, 0.0)
            hours_today = today_hours_by_user.get(user.id, 0.0)

            total_remaining = max(sum(user_tasks.mapped("remaining_hours")), 0)
            estimated_completion = self._compute_user_eta(user, total_remaining, today)

            # Collect project names for this user's tasks
            user_project_names = sorted(
                set(user_tasks.mapped("project_id").mapped(lambda p: (p.key + " - " + p.name) if p.key else p.name))
            )

            members_data.append(
                {
                    "user_id": user.id,
                    "user_name": user.name,
                    "user_avatar_url": "/web/image/res.users/%d/avatar_128" % user.id,
                    "current_task": (self._task_to_dict(current_task, today) if current_task else False),
                    "queue": [self._task_to_dict(t, today) for t in queue_tasks],
                    "backlog": [self._task_to_dict(t, today) for t in backlog_sorted],
                    "other": [self._task_to_dict(t, today) for t in other_tasks.sorted("sequence")],
                    "total_open_tasks": len(user_tasks),
                    "total_planned_hours": sum(user_tasks.mapped("planned_hours")),
                    "total_remaining_hours": sum(user_tasks.mapped("remaining_hours")),
                    "hours_logged_period": hours_logged,
                    "hours_logged_today": hours_today,
                    "estimated_completion": estimated_completion,
                    "project_names": user_project_names,
                }
            )

        result = {
            label: label_value,
            "period_start": fields.Date.to_string(period_start),
            "period_end": fields.Date.to_string(period_end),
            "members": members_data,
            "project_count": len(project_ids_set),
        }
        if extra:
            result.update(extra)
        return result

    def _task_to_dict(self, task, today):
        """Convert a task record to a dictionary for the dashboard."""
        parent_key = (task.parent_id.key or "") if task.parent_id else ""
        project = task.project_id
        project_label = (project.key + " - " + project.name) if project.key else project.name
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
            "planned_date_start": (str(task.planned_date_start.date()) if task.planned_date_start else ""),
            "planned_date_end": str(task.planned_date_end.date()) if task.planned_date_end else "",
            "is_overdue": bool(task.planned_date_end.date() < today) if task.planned_date_end else False,
            "kanban_state": task.kanban_state,
            "priority": task.priority,
            "sequence": task.sequence,
            "is_subtask": bool(task.parent_id),
            "parent_key": parent_key,
            "project_id": project.id,
            "project_name": project_label,
        }
