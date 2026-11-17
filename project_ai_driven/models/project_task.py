import math
from collections import Counter, defaultdict

from markupsafe import escape

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError

from .project_pull_request import parse_single_pull_request_link
from .project_task_testcase import TESTCASE_STATES

KANBAN_TESTCASE_STATES = ("not_run", "failed", "waiting")

# The largest estimate in hours of each size.
SIZE_BANDS = ((2, "xs"), (6, "s"), (12, "m"), (32, "l"), (60, "xl"), (float("inf"), "xxl"))


def get_size(hours):
    if not hours or hours <= 0:
        return False
    # a NaN, which only SQL can store, is under no bound
    return next((size for max_hours, size in SIZE_BANDS if hours <= max_hours), SIZE_BANDS[-1][1])


class ProjectTask(models.Model):
    _inherit = "project.task"

    is_ai_driven = fields.Boolean(related="project_id.is_ai_driven")
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
    checklist_item_ids = fields.One2many(
        "project.task.checklist.item", "task_id", string="Checklist", groups="project.group_project_user"
    )
    completion = fields.Integer("Completion", compute="_compute_completion")
    completion_xml = fields.Html(compute="_compute_completion_xml")
    kanban_checklist = fields.Html(compute="_compute_kanban_checklist")
    testcase_ids = fields.One2many("project.task.testcase", "task_id", groups="project.group_project_user")
    question_ids = fields.One2many(
        "project.task.question", "task_id", string="Questions", groups="project.group_project_user"
    )
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
        tracking=True,
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
    # Nothing writes it on this branch: it holds what a database upgraded from 16.0 kept there.
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
    note_count = fields.Integer("Notes", compute="_compute_note_count")

    @api.depends("allocated_hours")
    def _compute_tshirt_estimate(self):
        for record in self:
            record.tshirt_estimate = get_size(record.allocated_hours)

    @api.depends("checklist_item_ids.state", "checklist_item_ids.user_id")
    def _compute_completion(self):
        for record in self:
            record.completion = record.task_completion()

    @api.depends("checklist_item_ids.state", "checklist_item_ids.user_id")
    def _compute_completion_xml(self):
        for record in self:
            active_items = record.checklist_item_ids.filtered(
                lambda x: x.user_id.id == record.env.user.id and x.state != "cancelled"
            )
            if not active_items:
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

    @api.depends("checklist_item_ids.state", "checklist_item_ids.user_id")
    def _compute_kanban_checklist(self):
        for record in self:
            result_string_td = ""
            result_string_wt = ""
            if record.checklist_item_ids:
                task_todo_ids = record.checklist_item_ids.filtered(
                    lambda x: x.state == "todo" and x.user_id.id == record.env.user.id
                )
                task_waiting_ids = record.checklist_item_ids.filtered(
                    lambda x: x.state == "waiting" and x.user_id.id == record.env.user.id
                )
                if task_todo_ids:
                    tmp_string_td = escape(f": {len(task_todo_ids)}")
                    result_string_td += f"<li><b>TODO{tmp_string_td}</b></li>"
                if task_waiting_ids:
                    tmp_string_wt = escape(f": {len(task_waiting_ids)}")
                    result_string_wt += f"<li><b>Waiting{tmp_string_wt}</b></li>"
            record.kanban_checklist = (
                '<div class="kanban_subtasks"><ul>' + result_string_td + result_string_wt + "</ul></div>"
            )

    @api.depends("testcase_ids.state")
    def _compute_testcase_completion(self):
        for record in self:
            applicable_cases = record.testcase_ids.filtered(lambda case: case.state != "not_applicable")
            if not applicable_cases:
                completion = 100
                record.testcase_completion_xml = """
                    <div class="task_progress">
                    </div>
                    """
            else:
                passed_cases = applicable_cases.filtered(lambda case: case.state == "passed")
                completion = (len(passed_cases) / len(applicable_cases)) * 100
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
            state_counts = Counter(record.testcase_ids.mapped("state"))
            state_lines = "".join(
                "<li><b>%s</b></li>" % escape("%s: %d" % (TESTCASE_STATES[state], state_counts[state]))
                for state in KANBAN_TESTCASE_STATES
                if state_counts[state]
            )
            record.kanban_testcases = '<div class="kanban_subtasks"><ul>' + state_lines + "</ul></div>"

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
                    self.env._(
                        '"%s" is not a pull request link. A task closed without a pull request'
                        " takes the Resolution Data, Configuration or No change instead.",
                        record.pr_uri,
                    )
                )
            pull_request = self.env["project.pull.request"]._find_or_create(link)
            if pull_request not in record.pull_request_ids:
                record.pull_request_ids = [Command.link(pull_request.id)]
        # The value written names a pull request to add; the field goes on reading the first one.
        with self.env.protecting([self._fields["pr_uri"]], self):
            self._compute_pr_uri()

    # message_ids clears only the count cached within a transaction: an open form shows a new note on reload
    @api.depends("message_ids")
    def _compute_note_count(self):
        # res_id, not task_id: the access check of mail.message reads the task ids from it
        note_domain = [*self.env["mail.message"]._get_task_note_domain(), ("res_id", "in", self.ids)]
        note_counts = dict(self.env["mail.message"]._read_group(note_domain, ["res_id"], ["__count"]))
        for record in self:
            record.note_count = note_counts.get(record.id, 0)

    @api.constrains("allocated_hours")
    def _check_estimate_is_a_number(self):
        """A NaN passes no comparison of the rules, the JSON the web client
        receives would carry a NaN or an infinity bare, which is not JSON, and
        the tracking of the Allocated Time cannot format either.
        """
        tasks = self.filtered(lambda record: not math.isfinite(record.allocated_hours))
        if tasks:
            raise ValidationError(
                self.env._(
                    "The Estimate is a finite number of hours: NaN and infinity are refused.\nTasks: %s",
                    ", ".join(tasks.mapped("display_name")),
                )
            )

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

    def copy_data(self, default=None):
        """A task created from a template, or in a project created from one, is
        a new task to the closing rule: it takes no Resolution and no pull
        request of its source.
        """
        vals_list = super().copy_data(default=default)
        if not self.env.context.get("copy_from_template"):
            for task, vals in zip(self, vals_list):
                task._add_closing_values(vals)
        return vals_list

    def copy(self, default=None):
        copied_tasks = super().copy(default)
        for task, copied_task in zip(self, copied_tasks):
            for item in task.checklist_item_ids:
                item.copy({"task_id": copied_task.id, "state": item.state})
            for testcase in task.testcase_ids:
                testcase.copy({"task_id": copied_task.id, **testcase._get_result_values()})
            for question in task.question_ids:
                question.copy({"task_id": copied_task.id, "state": question.state})
        return copied_tasks

    @api.model
    def _create(self, data_list):
        tasks = super()._create(data_list)
        self.env["project.task.stage.log"]._log_created_tasks(tasks)
        return tasks

    def _write_multi(self, vals_list):
        """Every update of the stage column comes through here, with the values
        of each task: from a write as from a recomputation, and no context or
        sudo goes around it.
        """
        self._log_stage_moves(vals_list)
        return super()._write_multi(vals_list)

    def action_open_notes(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("project_ai_driven.mail_message_action_task_note")
        action["domain"] = [*self.env["mail.message"]._get_task_note_domain(), ("res_id", "=", self.id)]
        return action

    def _log_stage_moves(self, vals_list):
        """Log the tasks whose values carry a stage, before their rows are
        updated, in one statement for those that go to the same stage with the
        same user.

        The user logged is the one this update leaves on the task as write_uid:
        the last one who wrote it, when that changes, else the user of the
        environment that flushes, which is all a recomputation has.
        """
        task_ids_by_move = defaultdict(list)
        for task, vals in zip(self, vals_list):
            if "stage_id" in vals:
                move = (vals["stage_id"] or None, vals.get("write_uid") or self.env.uid)
                task_ids_by_move[move].append(task.id)
        for (stage_id, user_id), task_ids in task_ids_by_move.items():
            self.env["project.task.stage.log"]._log_moved_tasks(self.browse(task_ids), stage_id, user_id)

    def _add_closing_values(self, vals):
        """Complete the values of a copy that keeps the stage of its source, as
        a duplicate does unless told another stage: it also takes the Resolution
        and the pull requests that let the source into that stage, unless the
        values set them. Without them the closing rule refuses the copy.

        A source without a stage hands nothing over: the values of its copy
        hold an empty stage too, which is no stage kept.
        """
        self.ensure_one()
        if self._is_stage_kept_in(vals):
            vals.setdefault("resolution", self.resolution)
            vals.setdefault("pull_request_ids", [Command.set(self.pull_request_ids.ids)])

    def _remove_closing_values(self, vals):
        """Take back what _add_closing_values added to the values of a copy
        that was given another stage since, as the next occurrence of a
        recurring task is.
        """
        self.ensure_one()
        if not self._is_stage_kept_in(vals):
            vals.pop("resolution", None)
            vals.pop("pull_request_ids", None)

    def _clear_closing_values(self):
        self.write({"resolution": False, "pull_request_ids": [Command.clear()]})

    def _is_stage_kept_in(self, vals):
        return bool(self.stage_id) and vals.get("stage_id") == self.stage_id.id

    @api.model
    def _without_size(self, vals):
        """Return the vals of a write without a size: a value written to the
        stored field would stand in place of the size of the hours.

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

    def _filter_rule_tasks(self):
        """Return the tasks of flagged projects, without the templates: core
        keeps a task under a task template or in a project template out of its
        task lists and reports.
        """
        return self.filtered(
            lambda record: record.project_id.is_ai_driven
            and not record.has_template_ancestor
            and not record.has_project_template
        )

    def _filter_closing_rule_tasks(self):
        return self._filter_rule_tasks().filtered(
            lambda record: record.stage_id in record.project_id.pr_required_states
        )

    def _check_resolution_chosen(self):
        unresolved_tasks = self.filtered(lambda record: not record.resolution and not record.pull_request_ids)
        if unresolved_tasks:
            raise ValidationError(
                self.env._(
                    "Choose a Resolution: a task enters this stage only with one. Code needs a pull"
                    " request on the task; Data, Configuration and No change do not.\nTasks: %s",
                    ", ".join(unresolved_tasks.mapped("display_name")),
                )
            )

    def _check_code_has_pull_request(self):
        code_tasks = self.filtered(lambda record: record.resolution == "code" and not record.pull_request_ids)
        if code_tasks:
            raise ValidationError(
                self.env._(
                    "Add a pull request: a task with the Resolution Code enters this stage only with"
                    " one. A task closed without a pull request takes Data, Configuration or No"
                    " change.\nTasks: %s",
                    ", ".join(code_tasks.mapped("display_name")),
                )
            )

    def _set_resolution_of_pull_request(self):
        self.filtered(lambda record: not record.resolution and record.pull_request_ids).resolution = "code"

    def _apply_estimate_rule(self):
        """Run on tasks that just entered a stage. Sudo: a user can write a
        task of a project they cannot read.
        """
        self.sudo()._filter_estimate_rule_tasks()._check_type_and_estimate()

    def _filter_estimate_rule_tasks(self):
        return self._filter_rule_tasks().filtered(
            lambda record: record.stage_id in record.project_id.estimate_required_states
        )

    def _check_type_and_estimate(self):
        refusals = [
            "%s\n%s" % (requirement, self.env._("Tasks: %s", ", ".join(tasks.mapped("display_name"))))
            for requirement, tasks in self._get_missing_type_and_estimate()
            if tasks
        ]
        if refusals:
            raise ValidationError("\n\n".join(refusals))

    def _get_missing_type_and_estimate(self):
        """Return [(what a task needs to enter the stage, the tasks without it)]."""
        return [
            (
                self.env._("Choose a Task Type: a task enters this stage only with one."),
                self.filtered(lambda record: not record.task_type),
            ),
            (
                self.env._("Enter an Estimate above zero hours: a task enters this stage only with one."),
                # not above zero: a NaN is neither above nor below it
                self.filtered(lambda record: not record.allocated_hours > 0),
            ),
        ]

    def task_completion(self):
        user_task_ids = self.checklist_item_ids.filtered(
            lambda x: x.user_id.id == self.env.user.id and x.state != "cancelled"
        )
        if not user_task_ids:
            return 100
        user_done_task_ids = user_task_ids.filtered(lambda x: x.state == "done")
        return (len(user_done_task_ids) / len(user_task_ids)) * 100
