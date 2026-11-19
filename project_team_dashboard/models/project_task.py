from collections import defaultdict
from datetime import UTC

from odoo import Command, api, fields, models
from odoo.exceptions import UserError, ValidationError

PLANNED_DATE_FIELDS = ("planned_date_start", "planned_date_end")


class ProjectTask(models.Model):
    _inherit = "project.task"

    planned_date_start = fields.Datetime(
        compute="_compute_planned_date_start",
        store=True,
        readonly=False,
        recursive=True,
        init_storage="_init_planned_date_column",
    )
    planned_date_end = fields.Datetime(
        compute="_compute_planned_date_end",
        store=True,
        readonly=False,
        recursive=True,
        init_storage="_init_planned_date_column",
    )
    is_planned_from_subtasks = fields.Boolean(
        compute="_compute_is_planned_from_subtasks",
        compute_sudo=True,
        help="The task has active sub-tasks: its planned dates span theirs, and stay empty while none is dated.",
    )

    @api.depends("child_ids.planned_date_start", "child_ids.planned_date_end", "child_ids.active")
    def _compute_planned_date_start(self):
        self._roll_up_planned_dates()

    @api.depends("child_ids.planned_date_start", "child_ids.planned_date_end", "child_ids.active")
    def _compute_planned_date_end(self):
        self._roll_up_planned_dates()

    @api.depends("child_ids.planned_date_start", "child_ids.planned_date_end", "child_ids.active")
    def _compute_is_planned_from_subtasks(self):
        spans = self._subtask_date_spans()
        for task in self:
            task.is_planned_from_subtasks = task in spans

    @api.constrains("planned_date_start", "planned_date_end")
    def _check_planned_dates(self):
        for task in self:
            if task.planned_date_start and task.planned_date_end and task.planned_date_end < task.planned_date_start:
                raise ValidationError(self.env._("The end date must be after the start date."))

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks._recompute_planned_dates_from_subtasks()
        return tasks

    def write(self, vals):
        self._check_subtask_driven_dates(vals)
        result = super().write(vals)
        if "depend_on_ids" in vals or "dependent_ids" in vals:
            self._prune_dependency_lags()
        if "child_ids" in vals:
            self._recompute_planned_dates_from_subtasks()
        return result

    @api.model
    def action_open_backlog(self):
        """Open backlog with the user's default project pre-selected."""
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "project_team_dashboard.action_project_task_backlog",
        )
        action["context"] = {
            "search_default_project_id": self.env.user.dashboard_default_project_id.id,
            "search_default_unassigned": True,
        }
        return action

    def gantt_add_dependency(self, predecessor_id):
        """Make this task depend on the task predecessor_id, which the user must be able to read; both
        tasks need a project that allows task dependencies."""
        self.ensure_one()
        predecessor = self.browse(predecessor_id).exists()
        if not predecessor:
            raise UserError(self.env._("The selected predecessor task does not exist."))
        predecessor.check_access("read")
        if not self.project_id or not predecessor.project_id:
            raise UserError(self.env._("Only tasks that belong to a project can depend on each other."))
        if not (self.project_id.allow_task_dependencies and predecessor.project_id.allow_task_dependencies):
            raise UserError(self.env._("Task dependencies are disabled on the project of one of these tasks."))
        self.write({"depend_on_ids": [Command.link(predecessor.id)]})
        return True

    def gantt_remove_dependency(self, predecessor_id):
        """Remove the dependency of this task on the task predecessor_id; its lag goes with it."""
        self.ensure_one()
        self.write({"depend_on_ids": [Command.unlink(predecessor_id)]})
        return True

    def gantt_set_dependency_lag(self, predecessor_id, lag_days):
        """Set the lag, in calendar days, of this task's dependency on the task predecessor_id; 0 removes
        it. The lag is written as superuser once the user may write this task and read the predecessor."""
        self.ensure_one()
        self.check_access("write")
        # Before the dependency check: a user's read of depend_on_ids leaves out what they cannot read.
        self.env["project.task"].browse(predecessor_id).check_access("read")
        if predecessor_id not in self.depend_on_ids.ids:
            raise UserError(self.env._("Task '%(task)s' does not depend on the selected task.", task=self.display_name))
        lag = (
            self.env["project.task.dependency.lag"]
            .sudo()
            .search([("task_id", "=", self.id), ("depends_on_id", "=", predecessor_id)])
        )
        if lag and not lag_days:
            lag.unlink()
        elif lag:
            lag.lag_days = lag_days
        elif lag_days:
            lag.create(
                {"task_id": self.id, "depends_on_id": predecessor_id, "lag_days": lag_days},
            )
        return True

    def _init_planned_date_column(self):
        """Compute nothing when the columns are added: they are empty on every task, so no parent
        has a date to roll up, and the ORM would compute a recursive field task by task."""

    def _subtask_date_spans(self):
        spans = {}
        for task in self:
            active_subtasks = task.child_ids.filtered("active")
            subtask_dates = [
                date for field_name in PLANNED_DATE_FIELDS for date in active_subtasks.mapped(field_name) if date
            ]
            if subtask_dates:
                spans[task] = (min(subtask_dates), max(subtask_dates))
            elif active_subtasks:
                spans[task] = (False, False)
        return spans

    def _roll_up_planned_dates(self):
        """Set both planned dates of the tasks with active sub-tasks; the other tasks keep theirs.

        Both planned date computes call it, and set both sides of a parent at once: the start/end
        constraint is checked after each compute, and one rolled-up side against the other side's
        stale value could invert the window.
        """
        spans = self._subtask_date_spans()
        parents = self.filtered(lambda task: task in spans)
        date_fields = [self._fields[field_name] for field_name in PLANNED_DATE_FIELDS]
        with self.env.protecting(date_fields, parents):
            for task, (date_start, date_end) in spans.items():
                task.planned_date_start = date_start
                task.planned_date_end = date_end
        for date_field in date_fields:
            self.env.remove_to_compute(date_field, parents)

    def _recompute_planned_dates_from_subtasks(self):
        """Re-queue the roll-up that a create/write writing planned dates and sub-tasks together protected."""
        parents = self.filtered("child_ids")
        for field_name in PLANNED_DATE_FIELDS:
            self.env.add_to_compute(self._fields[field_name], parents)

    def _check_subtask_driven_dates(self, vals):
        """Refuse a write that changes the planned dates of a task planned from its sub-tasks.

        A write that also changes the sub-tasks is left to the roll-up re-queued after it.
        """
        if "child_ids" in vals:
            return
        written_fields = [field_name for field_name in PLANNED_DATE_FIELDS if field_name in vals]
        if not written_fields:
            return
        for task in self.filtered("is_planned_from_subtasks"):
            if any(
                (task[field_name] or None) != task._fields[field_name].convert_to_cache(vals[field_name], task)
                for field_name in written_fields
            ):
                raise UserError(
                    self.env._(
                        "The planned dates of task '%(task)s' come from its sub-tasks: "
                        "change the dates of the sub-tasks instead.",
                        task=task.display_name,
                    )
                )

    @api.model
    def _gantt_local_date(self, value):
        """Return the date of the UTC datetime value in the user's timezone, the one of the Gantt payload."""
        return value and value.replace(tzinfo=UTC).astimezone(self.env.tz).date()

    def _gantt_task_attributes(self, writable_ids, lags, readable_parent_ids):
        self.ensure_one()
        parent = self.parent_id
        return {
            "parent_label": (parent.key or parent.name) if parent.id in readable_parent_ids else "",
            "project_id": self.project_id.id,
            "date_deadline": fields.Date.to_string(self._gantt_local_date(self.date_deadline)),
            "effective_hours": self.effective_hours,
            "can_write": self.id in writable_ids,
            "depend_on_lags": lags,
            "is_planned_from_subtasks": self.is_planned_from_subtasks,
        }

    def _gantt_stage_history(self):
        if not self:
            return {}
        # sudo: only administrators read the log, and the caller searched these tasks as the user
        stage_logs = self.env["project.task.stage.log"].sudo().search([("task_id", "in", self.ids)])
        logs_by_task = defaultdict(list)
        for stage_log in stage_logs:
            logs_by_task[stage_log.task_id.id].append(stage_log)
        now = fields.Datetime.now()
        history_by_task = {}
        for task in self:
            visits = task._gantt_stage_visits(logs_by_task[task.id], now)
            history_by_task[task.id] = [
                {
                    "stage_id": visit["stage"].id,
                    "stage_name": visit["stage"].name or visit["stage_name"] or "",
                    "stage_sequence": visit["stage"].sequence,
                    "is_closed": task.is_closed and visit_index == len(visits) - 1,
                    "start": fields.Datetime.to_string(visit["start"]),
                    "end": fields.Datetime.to_string(visit["end"]),
                }
                for visit_index, visit in enumerate(visits)
            ]
        return history_by_task

    def _gantt_stage_visits(self, stage_logs, now):
        """Return the visits described by stage_logs, which must be the log rows of this task in
        the order of the log. A row that names a stage opens a visit, and any row closes the visit
        before it. A row with no stage and no name is the task leaving its stage; one with a name
        only is a stage deleted since.

        The visit the task is still in lasts until now, or, for a closed task, until its state or
        stage last changed, which closing it did; never before it starts.
        """
        self.ensure_one()
        visits = []
        is_in_stage = False
        for stage_log in stage_logs:
            if is_in_stage:
                visits[-1]["end"] = stage_log.date
            is_in_stage = bool(stage_log.stage_id or stage_log.stage_name)
            if is_in_stage:
                visits.append(
                    {"stage": stage_log.stage_id, "stage_name": stage_log.stage_name, "start": stage_log.date}
                )
        if is_in_stage:
            last_visit = visits[-1]
            visit_end = (self.date_last_stage_update if self.is_closed else now) or last_visit["start"]
            last_visit["end"] = max(visit_end, last_visit["start"])
        return visits

    @api.model
    def _get_template_field_blacklist(self):
        # a task made from a template is planned anew, as Odoo leaves its deadline empty
        return super()._get_template_field_blacklist() + list(PLANNED_DATE_FIELDS)

    def _resolve_copied_dependencies(self, copied_tasks):
        super()._resolve_copied_dependencies(copied_tasks)
        task_mapping, _task_dependencies = self._create_task_mapping(copied_tasks)
        self._copy_dependency_lags({original_id: task_copy.id for original_id, task_copy in task_mapping.items()})

    def _copy_dependency_lags(self, copy_ids_by_original_id):
        """Give the copies the lags of their originals, on the dependencies the copies took over."""
        Lag = self.env["project.task.dependency.lag"].sudo().with_context(active_test=False)
        original_ids = list(copy_ids_by_original_id)
        lag_days_by_pair = {}
        for lag in Lag.search(["|", ("task_id", "in", original_ids), ("depends_on_id", "in", original_ids)]):
            successor_id = copy_ids_by_original_id.get(lag.task_id.id, lag.task_id.id)
            predecessor_id = copy_ids_by_original_id.get(lag.depends_on_id.id, lag.depends_on_id.id)
            lag_days_by_pair[successor_id, predecessor_id] = lag.lag_days
        successors = (
            self.env["project.task"]
            .sudo()
            .with_context(active_test=False)
            .browse({successor_id for successor_id, _predecessor_id in lag_days_by_pair})
        )
        successors.invalidate_recordset(["depend_on_ids"])
        linked_ids = {successor.id: set(successor.depend_on_ids.ids) for successor in successors}
        known_pairs = {
            (lag.task_id.id, lag.depends_on_id.id) for lag in Lag.search([("task_id", "in", successors.ids)])
        }
        Lag.create(
            [
                {"task_id": successor_id, "depends_on_id": predecessor_id, "lag_days": lag_days}
                for (successor_id, predecessor_id), lag_days in lag_days_by_pair.items()
                if predecessor_id in linked_ids[successor_id] and (successor_id, predecessor_id) not in known_pairs
            ]
        )

    def _prune_dependency_lags(self):
        lags = (
            self.env["project.task.dependency.lag"]
            .sudo()
            .with_context(active_test=False)
            .search(["|", ("task_id", "in", self.ids), ("depends_on_id", "in", self.ids)])
        )
        lags.task_id.invalidate_recordset(["depend_on_ids"])
        lags.filtered(lambda lag: lag.depends_on_id not in lag.task_id.depend_on_ids).unlink()
