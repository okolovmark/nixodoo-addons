from collections import defaultdict

import pytz

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.sql import column_exists, create_column

PLANNED_DATE_FIELDS = ("planned_date_start", "planned_date_end")
# The fields of the plan the Deadline follows, and the field each records.
DEADLINE_PLAN_SOURCES = {"date_deadline_planned_end": "planned_date_end", "date_deadline_project_id": "project_id"}
DEADLINE_PLAN_FIELDS = tuple(DEADLINE_PLAN_SOURCES)
# The fields whose write this module acts on, on each task of the recurrence the write reaches too.
WRITE_ACTED_FIELDS = (*PLANNED_DATE_FIELDS, "child_ids", "depend_on_ids", "dependent_ids", "date_deadline")


def local_datetime(utc_datetime, tz_name):
    """Return a naive UTC datetime as a naive one in the time zone tz_name, in UTC when it is empty or unknown."""
    try:
        time_zone = pytz.timezone(tz_name or "UTC")
    except pytz.UnknownTimeZoneError:
        time_zone = pytz.utc
    return pytz.utc.localize(utc_datetime).astimezone(time_zone).replace(tzinfo=None)


def local_date(utc_datetime, tz_name):
    """Return the date of a naive UTC datetime in the time zone tz_name, in UTC when it is empty or unknown."""
    return local_datetime(utc_datetime, tz_name).date()


class Task(models.Model):
    _inherit = "project.task"

    date_deadline = fields.Date(compute="_compute_date_deadline", store=True, readonly=False)
    date_deadline_planned_end = fields.Datetime(
        string="Planned End of the Deadline",
        compute="_compute_date_deadline",
        store=True,
        help="The planned end the Deadline last followed, or was set against.",
    )
    date_deadline_project_id = fields.Many2one(
        "project.project",
        string="Project of the Deadline",
        compute="_compute_date_deadline",
        store=True,
        help="The project the Deadline last followed the planned end in, or was set in.",
    )
    planned_date_start = fields.Datetime(
        compute="_compute_planned_date_start",
        store=True,
        readonly=False,
        recursive=True,
    )
    planned_date_end = fields.Datetime(
        compute="_compute_planned_date_end",
        store=True,
        readonly=False,
        recursive=True,
    )
    is_planned_from_subtasks = fields.Boolean(
        compute="_compute_is_planned_from_subtasks",
        compute_sudo=True,
        help="The task has active sub-tasks: its planned dates span theirs, and stay empty while none is dated.",
    )

    # Not on project_id.is_ai_driven: flagging a project leaves the Deadlines of its tasks alone.
    @api.depends(*DEADLINE_PLAN_SOURCES.values())
    def _compute_date_deadline(self):
        """In an AI-driven project the Deadline becomes the date of the planned end for the user who
        saves, a rolled-up one included, when the planned end or the project differs from the plan
        the Deadline last followed. Odoo also runs this when a dependency of the planned end changes
        and leaves it as it was (a stage change under project_timeline): the Deadline stays then.
        Every saved task records its plan, so that ticking AI-driven Project aligns nothing; a task
        without a planned end keeps its Deadline.

        The record a form edits records only a plan its Deadline follows, so that a project changed
        and changed back on the form aligns nothing; the form carries that plan from one onchange to
        the next."""
        for task in self:
            plan = task._get_deadline_plan()
            followed_plan = task._get_followed_deadline_plan()
            should_align = bool(task.planned_date_end) and task.project_id.is_ai_driven and plan != followed_plan
            if should_align:
                task.date_deadline = local_date(task.planned_date_end, self.env.user.tz)
            # the followed plan is in cache now: assigning it unchanged writes nothing
            task.update(dict(zip(DEADLINE_PLAN_FIELDS, plan if should_align or task.id else followed_plan)))

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
                raise ValidationError(_("The end date must be after the start date."))

    def _auto_init(self):
        self._init_planned_date_columns()
        self._init_deadline_plan_columns()
        return super()._auto_init()

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        # picked before the re-queue, which queues the plan of the parents too
        tasks_given_a_deadline = tasks._without_deadline_plan()
        tasks._recompute_planned_dates_from_subtasks()
        tasks_given_a_deadline._record_deadline_plan()
        return tasks

    def write(self, vals):
        """Apply this module's rules (planned dates from sub-tasks, dependency lags, the plan the
        Deadline follows) to every task the write reaches: with recurrence_update, the occurrences too."""
        # before super().write, which may drop recurrence_update
        written_tasks = self._get_tasks_written_by(vals)
        written_tasks._check_subtask_driven_dates(vals)
        tasks_given_a_deadline = written_tasks if "date_deadline" in vals else self.browse()
        with self.env.protecting(self._get_given_deadline_fields(vals), tasks_given_a_deadline):
            result = super().write(vals)
        if "depend_on_ids" in vals or "dependent_ids" in vals:
            written_tasks._prune_dependency_lags()
        if "child_ids" in vals:
            written_tasks._recompute_planned_dates_from_subtasks()
        tasks_given_a_deadline._record_deadline_plan()
        return result

    @api.returns("self", lambda value: value.id)
    def copy(self, default=None):
        if self.allow_task_dependencies and "task_mapping" not in self.env.context:
            self = self.with_context(task_mapping={})
        task_copy = super().copy(default)
        if self.allow_task_dependencies:
            self._copy_dependency_lags(task_copy, self.env.context["task_mapping"])
        return task_copy

    @api.model
    def action_open_backlog(self):
        """Open the backlog on the user's default project when they find it in the companies of the context."""
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "project_team_dashboard.action_project_task_backlog",
        )
        project = self.env["project.project"].search(
            [("id", "=", self.env.user.dashboard_default_project_id.id)], limit=1
        )
        action["context"] = {
            "search_default_project_id": project.id,
            "search_default_unassigned": True,
        }
        return action

    def gantt_add_dependency(self, predecessor_id):
        self.ensure_one()
        predecessor = self.browse(predecessor_id).exists()
        if not predecessor:
            raise UserError(_("The selected predecessor task does not exist."))
        predecessor.check_access_rule("read")
        if not self.project_id or not predecessor.project_id:
            raise UserError(_("Only tasks that belong to a project can depend on each other."))
        if not (self.project_id.allow_task_dependencies and predecessor.project_id.allow_task_dependencies):
            raise UserError(_("Task dependencies are disabled on the project of one of these tasks."))
        self.write({"depend_on_ids": [Command.link(predecessor.id)]})
        return True

    def gantt_remove_dependency(self, predecessor_id):
        self.ensure_one()
        self.write({"depend_on_ids": [Command.unlink(predecessor_id)]})
        return True

    def gantt_set_dependency_lag(self, predecessor_id, lag_days):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        if predecessor_id not in self.depend_on_ids.ids:
            raise UserError(_("Task '%(task)s' does not depend on the selected task.", task=self.display_name))
        self.env["project.task"].browse(predecessor_id).check_access_rule("read")
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
        """Re-queue the roll-up that a create/write writing planned dates and sub-tasks together
        protected, and the Deadline: computed meanwhile, it followed the planned end given."""
        parents = self.filtered("child_ids")
        for field_name in (*PLANNED_DATE_FIELDS, "date_deadline", *DEADLINE_PLAN_FIELDS):
            self.env.add_to_compute(self._fields[field_name], parents)

    @api.model
    def _init_planned_date_columns(self):
        """Create a missing planned date column in SQL, empty: added by the ORM, it would be
        computed task by task to change nothing but the parents, which the post-init hook rolls up."""
        for field_name in PLANNED_DATE_FIELDS:
            if not column_exists(self.env.cr, self._table, field_name):
                create_column(self.env.cr, self._table, field_name, "timestamp")

    @api.model
    def _init_deadline_plan_columns(self):
        """Create a missing column of the plan the Deadline follows in SQL, filled with the current
        plan of every task: added by the ORM, it would be computed on every task and align the
        Deadlines of closed ones. The install hook and the update migration record the plan again
        after the roll-up, then align the open tasks."""
        for field_name, source_name in DEADLINE_PLAN_SOURCES.items():
            if not column_exists(self.env.cr, self._table, field_name):
                create_column(self.env.cr, self._table, field_name, self._fields[field_name].column_type[1])
                self.env.cr.execute('UPDATE "%s" SET "%s" = "%s"' % (self._table, field_name, source_name))

    def _get_deadline_plan(self):
        """Return the plan the Deadline follows: the current values of its sources."""
        return tuple(self[source_name] for source_name in DEADLINE_PLAN_SOURCES.values())

    def _get_followed_deadline_plan(self):
        """Return the plan the Deadline last followed. The record a form edits holds it in cache when
        the form carries it; otherwise it is the saved task's, since the record reads it empty while
        it is being computed."""
        plan_fields = [self._fields[field_name] for field_name in DEADLINE_PLAN_FIELDS]
        is_in_cache = all(self.env.cache.contains(self, field) for field in plan_fields)
        task = self if is_in_cache else self._origin
        return tuple(task[field.name] for field in plan_fields)

    def _without_deadline_plan(self):
        """Return the tasks whose plan is neither recorded nor to compute: those given a Deadline, by
        their values or a default of any kind. A task whose plan Odoo computed already is left out:
        its Deadline follows the roll-up that the create re-queues."""
        plan_field = self._fields["date_deadline_planned_end"]
        return self.filtered(
            lambda task: not self.env.is_to_compute(plan_field, task)
            and not any(task[field_name] for field_name in DEADLINE_PLAN_FIELDS)
        )

    def _get_tasks_written_by(self, vals):
        """Return the tasks this module acts on in a write of vals: self, and when vals holds a field
        of WRITE_ACTED_FIELDS the occurrences of their recurrences the write reaches too."""
        if vals.keys().isdisjoint(WRITE_ACTED_FIELDS):
            return self
        return self._get_tasks_written_with(vals)

    def _get_given_deadline_fields(self, vals):
        """Return the Deadline and the fields of its plan when vals gives the Deadline, else none.
        The write protects them through its whole chain, which Odoo does in its own write alone: a
        nested write that reads the Deadline (the new key of a moved task) would otherwise align it
        against the plan recorded before."""
        if "date_deadline" not in vals:
            return []
        return [self._fields[field_name] for field_name in ("date_deadline", *DEADLINE_PLAN_FIELDS)]

    def _record_deadline_plan(self):
        """Record the current plan of the tasks as the one their Deadline follows. A Deadline set by
        hand needs it: Odoo computes none of the fields that share a compute with a field it is given."""
        with self.env.protecting([self._fields[field_name] for field_name in DEADLINE_PLAN_FIELDS], self):
            for task in self:
                task.update(dict(zip(DEADLINE_PLAN_FIELDS, task._get_deadline_plan())))

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
                    _(
                        "The planned dates of task '%(task)s' come from its sub-tasks: "
                        "change the dates of the sub-tasks instead.",
                        task=task.display_name,
                    )
                )

    def _gantt_task_attributes(self, writable_ids, lags):
        self.ensure_one()
        # sudo: who reads a sub-task reads the label of its parent, as core reads a many-to-one
        parent = self.parent_id.sudo()
        return {
            "parent_label": parent.key or parent.name or "",
            "project_id": self.project_id.id,
            "date_deadline": fields.Date.to_string(self.date_deadline),
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
            history_by_task[task.id] = [
                {
                    "stage_id": visit["stage"].id,
                    "stage_name": visit["stage"].name or visit["stage_name"] or "",
                    "stage_sequence": visit["stage"].sequence,
                    "is_closed": visit["stage"].fold,
                    "start": fields.Datetime.to_string(visit["start"]),
                    "end": fields.Datetime.to_string(visit["end"]),
                }
                for visit in self._gantt_stage_visits(logs_by_task[task.id], now)
            ]
        return history_by_task

    @api.model
    def _gantt_stage_visits(self, stage_logs, now):
        """Return the visits described by stage_logs, which must be the log
        rows of one task in the order of the log. A row that names a stage
        opens a visit, and any row closes the visit before it. A row with no
        stage and no name is the task leaving its stage; one with a name only
        is a stage deleted since.
        """
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
            last_visit["end"] = last_visit["start"] if last_visit["stage"].fold else max(now, last_visit["start"])
        return visits

    def _copy_dependency_lags(self, task_copy, task_mapping):
        Lag = self.env["project.task.dependency.lag"].sudo().with_context(active_test=False)
        Task = self.env["project.task"].sudo().with_context(active_test=False)
        candidate_pairs = {}
        for lag in Lag.search([("task_id", "=", self.id)]):
            candidate_pairs[(task_copy.id, task_mapping.get(lag.depends_on_id.id, lag.depends_on_id.id))] = lag.lag_days
        for lag in Lag.search([("depends_on_id", "=", self.id)]):
            candidate_pairs[(task_mapping.get(lag.task_id.id, lag.task_id.id), task_copy.id)] = lag.lag_days
        successors = Task.browse([successor_id for successor_id, _predecessor_id in candidate_pairs])
        successors.invalidate_recordset(["depend_on_ids"])
        linked = {successor.id: set(successor.depend_on_ids.ids) for successor in successors}
        known_pairs = {
            (lag.task_id.id, lag.depends_on_id.id) for lag in Lag.search([("task_id", "in", successors.ids)])
        }
        vals_list = []
        for (successor_id, predecessor_id), lag_days in candidate_pairs.items():
            if predecessor_id in linked[successor_id] and (successor_id, predecessor_id) not in known_pairs:
                vals_list.append({"task_id": successor_id, "depends_on_id": predecessor_id, "lag_days": lag_days})
        Lag.create(vals_list)

    def _prune_dependency_lags(self):
        lags = (
            self.env["project.task.dependency.lag"]
            .sudo()
            .with_context(active_test=False)
            .search(["|", ("task_id", "in", self.ids), ("depends_on_id", "in", self.ids)])
        )
        lags.task_id.invalidate_recordset(["depend_on_ids"])
        lags.filtered(lambda lag: lag.depends_on_id not in lag.task_id.depend_on_ids).unlink()
