from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError

from .project_pull_request import parse_single_pull_request_link
from .project_task_subtask import SUBTASK_STATES

# The largest estimate in hours of each size. RECOMPUTE_TASK_SIZES of the hooks states the same in SQL.
SIZE_BANDS = ((2, "xs"), (6, "s"), (12, "m"), (32, "l"), (60, "xl"), (float("inf"), "xxl"))


def get_size(hours):
    if not hours or hours <= 0:
        return False
    # The default is the ELSE of the SQL: a NaN is under no bound here and above every one there.
    return next((size for max_hours, size in SIZE_BANDS if hours <= max_hours), SIZE_BANDS[-1][1])


class ProjectTask(models.Model):
    _inherit = "project.task"

    is_ai_driven = fields.Boolean(related="project_id.is_ai_driven")
    questions = fields.Html()
    answers = fields.Html()
    solution = fields.Html()
    priority_level = fields.Selection(
        [
            ("4", "Highest"),
            ("3", "High"),
            ("2", "Medium"),
            ("1", "Low"),
            ("0", "Lowest"),
        ],
        default="1",
    )
    task_type = fields.Selection(
        [
            ("feature", "Feature"),
            ("bug", "Bug"),
            ("improvement", "Improvement"),
            ("investigation", "Investigation"),
            ("milestone", "Milestone"),
            ("support", "Support"),
            ("technical_debt", "Technical Debt"),
        ],
        tracking=True,
        string="AI-driven Task Type",
    )
    tshirt_estimate = fields.Selection(
        [
            ("xs", "XS"),
            ("s", "S"),
            ("m", "M"),
            ("l", "L"),
            ("xl", "XL"),
            ("xxl", "XXL"),
        ],
        string="Size",
        compute="_compute_tshirt_estimate",
        store=True,
        help="Follows the estimate in hours: XS up to 2 hours, S up to 6, M up to 12, L up to 32,"
        " XL up to 60, XXL above. Empty while the task has no estimate.",
    )
    subtask_ids = fields.One2many("project.task.subtask", "task_id", groups="project.group_project_user")
    default_user = fields.Many2one("res.users", compute="_compute_default_user")
    completion = fields.Integer("Completion", compute="_compute_completion")
    completion_xml = fields.Html(compute="_compute_completion_xml")
    kanban_subtasks = fields.Html(compute="_compute_kanban_subtasks")
    testcase_ids = fields.One2many("project.task.testcase", "task_id", groups="project.group_project_user")
    testcase_completion = fields.Integer(compute="_compute_testcase_completion")
    testcase_completion_xml = fields.Html(compute="_compute_testcase_completion")
    kanban_testcases = fields.Html(compute="_compute_kanban_testcases")
    testcase_general_comments = fields.Html()
    pull_request_ids = fields.Many2many(
        "project.pull.request",
        "project_pull_request_task_rel",
        "task_id",
        "pull_request_id",
        string="Pull Requests",
        copy=False,
    )
    pr_uri = fields.Char(
        string="First Pull Request",
        compute="_compute_pr_uri",
        inverse="_inverse_pr_uri",
        store=True,
        readonly=False,
        copy=False,
        tracking=True,
        help="The link of the pull request created first. Writing a link adds that pull request to the task.",
    )
    pr_legacy = fields.Char(string="PR (old value)", readonly=True, copy=False)
    pr_required_states = fields.Many2many(related="project_id.pr_required_states")
    resolution = fields.Selection(
        [
            ("code", "Code"),
            ("data", "Data"),
            ("configuration", "Configuration"),
            ("no_change", "No change"),
        ],
        copy=False,
        tracking=True,
    )

    @api.depends("planned_hours")
    def _compute_tshirt_estimate(self):
        for record in self:
            record.tshirt_estimate = get_size(record.planned_hours)

    @api.depends("user_ids", "create_uid")
    def _compute_default_user(self):
        for record in self:
            if len(record.user_ids) <= 1:
                if self.env.user != record.user_ids and self.env.user != record.create_uid:
                    record.default_user = record.user_ids
                else:
                    if self.env.user != record.user_ids:
                        record.default_user = record.user_ids
                    elif self.env.user != record.create_uid:
                        record.default_user = record.create_uid
                    elif self.env.user == record.create_uid and self.env.user == record.user_ids:
                        record.default_user = self.env.user
            else:
                record.default_user = False

    @api.depends("subtask_ids.state", "subtask_ids.user_id")
    def _compute_completion(self):
        for record in self:
            record.completion = record.task_completion()

    @api.depends("subtask_ids.state", "subtask_ids.user_id")
    def _compute_completion_xml(self):
        for record in self:
            active_subtasks = record.subtask_ids and record.subtask_ids.filtered(
                lambda x: x.user_id.id == record.env.user.id and x.state != "cancelled"
            )
            if not active_subtasks:
                record.completion_xml = """
                    <div class="task_progress">
                    </div>
                    """
                continue

            completion = record.task_completion()
            record.completion_xml = f"""
            <div class="task_progress">
                <div class="progress_info">
                    Your Checklist:
                </div>
                <div> {round(completion, 2)}% Done</div>
            </div>
            """

    @api.depends("subtask_ids.state", "subtask_ids.user_id")
    def _compute_kanban_subtasks(self):
        for record in self:
            result_string_td = ""
            result_string_wt = ""
            if record.subtask_ids:
                task_todo_ids = record.subtask_ids.filtered(
                    lambda x: x.state == "todo" and x.user_id.id == record.env.user.id
                )
                task_waiting_ids = record.subtask_ids.filtered(
                    lambda x: x.state == "waiting" and x.user_id.id == record.env.user.id
                )
                if task_todo_ids:
                    tmp_string_td = escape(f": {len(task_todo_ids)}")
                    result_string_td += f"<li><b>TODO{tmp_string_td}</b></li>"
                if task_waiting_ids:
                    tmp_string_wt = escape(f": {len(task_waiting_ids)}")
                    result_string_wt += f"<li><b>Waiting{tmp_string_wt}</b></li>"
            record.kanban_subtasks = (
                '<div class="kanban_subtasks"><ul>' + result_string_td + result_string_wt + "</ul></div>"
            )

    @api.depends("testcase_ids.state")
    def _compute_testcase_completion(self):
        for record in self:
            user_task_ids = record.testcase_ids.filtered(lambda x: x.state != "cancelled")
            if not user_task_ids:
                completion = 100
                record.testcase_completion_xml = """
                    <div class="task_progress">
                    </div>
                    """
            else:
                user_done_task_ids = user_task_ids.filtered(lambda x: x.state == "done")
                completion = (len(user_done_task_ids) / len(user_task_ids)) * 100
                record.testcase_completion_xml = f"""
                <div class="task_progress">
                    <div class="progress_info">
                        Your Test cases:
                    </div>
                    <div> {round(completion, 2)}% Done</div>
                </div>
                """
            record.testcase_completion = completion

    @api.depends("testcase_ids.state")
    def _compute_kanban_testcases(self):
        for record in self:
            result_string_td = ""
            result_string_wt = ""
            if record.testcase_ids:
                task_todo_ids = record.testcase_ids.filtered(lambda x: x.state == "todo")
                task_waiting_ids = record.testcase_ids.filtered(lambda x: x.state == "waiting")
                if task_todo_ids:
                    tmp_string_td = escape(f": {len(task_todo_ids)}")
                    result_string_td += f"<li><b>TODO{tmp_string_td}</b></li>"
                if task_waiting_ids:
                    tmp_string_wt = escape(f": {len(task_waiting_ids)}")
                    result_string_wt += f"<li><b>Waiting{tmp_string_wt}</b></li>"
            record.kanban_testcases = (
                '<div class="kanban_subtasks"><ul>' + result_string_td + result_string_wt + "</ul></div>"
            )

    @api.depends("pull_request_ids.url")
    def _compute_pr_uri(self):
        for record in self:
            # _origin: in a form the pull requests are new records, whose ids do not sort
            record.pr_uri = record.pull_request_ids._origin.sorted("id")[:1].url

    def _inverse_pr_uri(self):
        for record in self:
            if not record.pr_uri:
                continue
            link = parse_single_pull_request_link(record.pr_uri)
            if not link:
                raise ValidationError(
                    _(
                        '"%s" is not a pull request link. A task closed without a pull request'
                        " takes the Resolution Data, Configuration or No change instead."
                    )
                    % record.pr_uri
                )
            pull_request = self.env["project.pull.request"]._find_or_create(link)
            if pull_request not in record.pull_request_ids:
                record.pull_request_ids = [Command.link(pull_request.id)]
        # The value written names a pull request to add; the field goes on reading the first one.
        with self.env.protecting([self._fields["pr_uri"]], self):
            self._compute_pr_uri()

    @api.model_create_multi
    def create(self, vals_list):
        tasks = super().create(vals_list)
        tasks._apply_closing_rule()
        tasks._apply_estimate_rule()
        return tasks

    def write(self, vals):
        vals = self._without_size(vals)
        former_stages = self._get_former_stages(vals)
        result = super().write(vals)
        moved_tasks = self._filter_moved_from(former_stages)
        moved_tasks._apply_closing_rule()
        moved_tasks._apply_estimate_rule()
        return result

    def copy(self, default=None):
        task = super().copy(self._with_closing_values(default))
        for subtask in self.subtask_ids:
            subtask.copy({"task_id": task.id, "state": subtask.state})
        for testcase in self.testcase_ids:
            testcase.copy({"task_id": task.id, "state": testcase.state})
        return task

    @api.model
    def _create(self, data_list):
        tasks = super()._create(data_list)
        self.env["project.task.stage.log"]._log_created_tasks(tasks)
        return tasks

    def _write(self, vals):
        """Every update of the stage column comes through here, from a write as
        from a recomputation, and no context or sudo goes around it.

        The user logged is the one this update leaves on the task as write_uid:
        the last one who wrote it, when that changes, else the user of the
        environment that flushes, which is all a recomputation has.
        """
        if "stage_id" in vals:
            self.env["project.task.stage.log"]._log_moved_tasks(
                self, vals["stage_id"], vals.get("write_uid") or self.env.uid
            )
        return super()._write(vals)

    def _with_closing_values(self, default):
        """Return default for a copy that keeps the stage of its source, as the
        duplicate of a project makes of each task and subtask: it also takes
        the Resolution and the pull requests that let the source into that
        stage, unless default sets them. Without them the closing rule refuses
        the copy.
        """
        default = dict(default or {})
        if "stage_id" in default and default["stage_id"] == self.stage_id.id:
            default.setdefault("resolution", self.resolution)
            default.setdefault("pull_request_ids", [Command.set(self.pull_request_ids.ids)])
        return default

    @api.model
    def _without_size(self, vals):
        """Return the vals of a write without a size: callers from before the
        size was computed still send one, and a value written to the stored
        field would stand in place of the size of the hours.

        A create needs no such care: it computes a read-only field whatever
        value was sent, or came as a default of the context.
        """
        if "tshirt_estimate" not in vals:
            return vals
        return {field_name: value for field_name, value in vals.items() if field_name != "tshirt_estimate"}

    def _get_former_stages(self, vals):
        """Return {task id: stage} when vals may move the tasks to another stage:
        a project change recomputes the stage.
        """
        if "stage_id" not in vals and "project_id" not in vals:
            return {}
        return {record.id: record.stage_id for record in self}

    def _filter_moved_from(self, former_stages):
        return self.filtered(lambda record: record.id in former_stages and record.stage_id != former_stages[record.id])

    def _apply_closing_rule(self):
        """Run on tasks that just entered a stage. Sudo: a user can write a
        task of a project they cannot read.
        """
        tasks = self.sudo()._filter_closing_rule_tasks()
        tasks._check_resolution_chosen()
        tasks._check_code_has_pull_request()
        tasks._set_resolution_of_pull_request()

    def _filter_closing_rule_tasks(self):
        return self.filtered(
            lambda record: record.project_id.is_ai_driven and record.stage_id in record.project_id.pr_required_states
        )

    def _check_resolution_chosen(self):
        unresolved_tasks = self.filtered(lambda record: not record.resolution and not record.pull_request_ids)
        if unresolved_tasks:
            raise ValidationError(
                _(
                    "Choose a Resolution: a task enters this stage only with one. Code needs a pull"
                    " request on the task; Data, Configuration and No change do not.\nTasks: %s"
                )
                % ", ".join(unresolved_tasks.mapped("display_name"))
            )

    def _check_code_has_pull_request(self):
        code_tasks = self.filtered(lambda record: record.resolution == "code" and not record.pull_request_ids)
        if code_tasks:
            raise ValidationError(
                _(
                    "Add a pull request: a task with the Resolution Code enters this stage only with"
                    " one. A task closed without a pull request takes Data, Configuration or No"
                    " change.\nTasks: %s"
                )
                % ", ".join(code_tasks.mapped("display_name"))
            )

    def _set_resolution_of_pull_request(self):
        self.filtered(lambda record: not record.resolution and record.pull_request_ids).resolution = "code"

    def _apply_estimate_rule(self):
        """Run on tasks that just entered a stage. Sudo: a user can write a
        task of a project they cannot read.
        """
        self.sudo()._filter_estimate_rule_tasks()._check_type_and_estimate()

    def _filter_estimate_rule_tasks(self):
        return self.filtered(
            lambda record: record.project_id.is_ai_driven
            and record.stage_id in record.project_id.estimate_required_states
        )

    def _check_type_and_estimate(self):
        refusals = [
            "%s\n%s" % (requirement, _("Tasks: %s") % ", ".join(tasks.mapped("display_name")))
            for requirement, tasks in self._get_missing_type_and_estimate()
            if tasks
        ]
        if refusals:
            raise ValidationError("\n\n".join(refusals))

    def _get_missing_type_and_estimate(self):
        """Return [(what a task needs to enter the stage, the tasks without it)]."""
        return [
            (
                _("Choose a Task Type: a task enters this stage only with one."),
                self.filtered(lambda record: not record.task_type),
            ),
            (
                _("Enter an Estimate above zero hours: a task enters this stage only with one."),
                self.filtered(lambda record: record.planned_hours <= 0),
            ),
        ]

    def task_completion(self):
        user_task_ids = self.subtask_ids.filtered(lambda x: x.user_id.id == self.env.user.id and x.state != "cancelled")
        if not user_task_ids:
            return 100
        user_done_task_ids = user_task_ids.filtered(lambda x: x.state == "done")
        return (len(user_done_task_ids) / len(user_task_ids)) * 100

    def send_subtask_email(
        self,
        subtask_name,
        subtask_state,
        subtask_reviewer_id,
        subtask_user_id,
        old_name=None,
    ):
        """Notify the subtask reviewer/assignee of a checklist change.

        Honour mail_auto_subscribe_no_notify: the notification is a
        message_post to the reviewer/assignee, which needs a configured
        sender email. An email-less actor (e.g. an RPC service account)
        would otherwise raise "Unable to send message, please configure the
        sender's email address" and block the subtask create/write itself.
        Same guard as _task_message_auto_subscribe_notify.
        """
        if self.env.context.get("mail_auto_subscribe_no_notify"):
            return
        for record in self:
            body = ""
            reviewer = self.env["res.users"].browse(subtask_reviewer_id)
            user = self.env["res.users"].browse(subtask_user_id)
            state = SUBTASK_STATES[subtask_state]
            if subtask_state == "done":
                state = '<span style="color:#080">' + state + "</span>"
            if subtask_state == "todo":
                state = '<span style="color:#A00">' + state + "</span>"
            if subtask_state == "cancelled":
                state = '<span style="color:#777">' + state + "</span>"
            if subtask_state == "waiting":
                state = '<span style="color:#b818ce">' + state + "</span>"
            partner_ids = []
            subtype = "project_ai_driven.subtasks_subtype"
            if user == self.env.user and reviewer == self.env.user:
                body = Markup("<p><strong>" + state + "</strong>: ") + escape(subtask_name)
                subtype = False
            elif self.env.user == reviewer:
                body = (
                    Markup("<p>")
                    + escape(user.name)
                    + Markup(", <br><strong>")
                    + Markup(state)
                    + Markup("</strong>: ")
                    + escape(subtask_name)
                )
                partner_ids = [user.partner_id.id]
            elif self.env.user == user:
                body = (
                    Markup("<p>")
                    + escape(reviewer.name)
                    + ", "
                    + Markup('<em style="color:#999">I updated checklist item assigned to me:</em> <br><strong>')
                    + Markup(state + "</strong>: ")
                    + escape(subtask_name)
                )
                partner_ids = [reviewer.partner_id.id]
            else:
                body = (
                    Markup("<p>")
                    + escape(user.name)
                    + ", "
                    + escape(reviewer.name)
                    + Markup(', <em style="color:#999">I updated checklist item, now its assigned to ')
                    + escape(user.name)
                    + Markup(": </em> <br><strong>")
                    + Markup(state + "</strong>: ")
                    + escape(subtask_name)
                )
                partner_ids = [user.partner_id.id, reviewer.partner_id.id]
            if old_name:
                body = (
                    body
                    + Markup('<br><em style="color:#999">Updated from</em><br><strong>')
                    + Markup(state + "</strong>: ")
                    + escape(old_name)
                    + Markup("</p>")
                )
            else:
                body = body + Markup("</p>")
            record.message_post(
                message_type="comment",
                body=body,
                subtype_xmlid=subtype,
                partner_ids=partner_ids,
            )

    @api.model
    def _task_message_auto_subscribe_notify(self, users_per_task):
        """Honour mail_auto_subscribe_no_notify for assignment notifications.

        Core project calls this directly from create()/write() on assignment,
        bypassing the mail_auto_subscribe_no_notify guard that mail.thread
        applies to its generic path. Without the guard, an email-less user
        (e.g. an RPC service account) can never assign tasks: message_notify
        raises "Unable to send message, please configure the sender's email
        address" before any context flag is consulted.
        """
        if self.env.context.get("mail_auto_subscribe_no_notify"):
            return None
        return super()._task_message_auto_subscribe_notify(users_per_task)
