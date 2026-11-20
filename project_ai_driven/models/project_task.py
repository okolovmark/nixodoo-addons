import math
from collections import Counter, defaultdict

from markupsafe import escape

from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.misc import clean_context

from .project_pull_request import parse_single_pull_request_link
from .project_task_image_mixin import ImageMover
from .project_task_testcase import TESTCASE_STATES

KANBAN_TESTCASE_STATES = ("not_run", "failed", "waiting")
LINE_FIELDS = ("checklist_item_ids", "testcase_ids", "question_ids")
# A question row was asked and answered for the run before: the next occurrence takes none.
OCCURRENCE_LINE_FIELDS = tuple(field_name for field_name in LINE_FIELDS if field_name != "question_ids")
# The value of the context key under which core creates the next occurrences: an object of
# this module, which no RPC call can send, so no caller switches the stage rules off with it.
NEXT_OCCURRENCE_CREATION = object()
# Marked for an upgrade or a removal, a module is still installed.
INSTALLED_MODULE_STATES = ("installed", "to upgrade", "to remove")

# The largest estimate in hours of each size.
SIZE_BANDS = ((2, "xs"), (6, "s"), (12, "m"), (32, "l"), (60, "xl"), (float("inf"), "xxl"))


def get_size(hours):
    if not hours or hours <= 0:
        return False
    # a NaN, which only SQL can store, is under no bound
    return next((size for max_hours, size in SIZE_BANDS if hours <= max_hours), SIZE_BANDS[-1][1])


def _is_create_command(command):
    # A list from the web client, with a virtual id; a tuple from Command.create.
    return isinstance(command, (list, tuple)) and command[0] == Command.CREATE


def is_next_occurrence_creation(env):
    return env.context.get("project_ai_driven_next_occurrence") is NEXT_OCCURRENCE_CREATION


class ProjectTask(models.Model):
    _name = "project.task"
    # the mixin comes below core: the description history builds its revision from the moved text
    _inherit = ["project.task", "project.task.image.mixin"]

    is_ai_driven = fields.Boolean(related="project_id.is_ai_driven")
    develops_this_odoo = fields.Boolean(related="project_id.develops_this_odoo")
    uat_user_ids = fields.Many2many(
        "res.users",
        relation="project_task__uat_user_rel",
        column1="task_id",
        column2="user_id",
        string="UAT users",
        # as the Assignees: an archived user stays shown on the tasks they were set on
        context={"active_test": False},
        copy=False,
        tracking=True,
        help="The users who run the acceptance tests of the task.",
    )
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
    completion = fields.Integer("Completion", compute="_compute_completion", groups="project.group_project_user")
    completion_xml = fields.Html(compute="_compute_completion_xml", groups="project.group_project_user")
    kanban_checklist = fields.Html(compute="_compute_kanban_checklist", groups="project.group_project_user")
    testcase_ids = fields.One2many("project.task.testcase", "task_id", groups="project.group_project_user")
    question_ids = fields.One2many(
        "project.task.question", "task_id", string="Questions", groups="project.group_project_user"
    )
    session_ids = fields.One2many(
        "project.task.session", "task_id", string="Sessions", groups="project.group_project_user"
    )
    testcase_completion = fields.Integer(compute="_compute_testcase_completion", groups="project.group_project_user")
    testcase_completion_xml = fields.Html(compute="_compute_testcase_completion", groups="project.group_project_user")
    kanban_testcases = fields.Html(compute="_compute_kanban_testcases", groups="project.group_project_user")
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
    # No field domain: it would filter the reads too, and a module uninstalled later stays on the task.
    # Not tracked: Odoo 20 writes tracked values into the message body, which ignores the field's groups.
    module_ids = fields.Many2many(
        "ir.module.module",
        "project_task_ir_module_module_rel",
        "task_id",
        "module_id",
        string="Modules",
        copy=False,
        groups="project.group_project_user",
        help="The installed modules the task touches. Only an installed module can be added.",
    )
    module_names = fields.Char(
        compute="_compute_module_names",
        compute_sudo=True,
        groups="project.group_project_user",
        help="The technical names of the modules, sorted, separated by commas.",
    )

    @api.depends("allocated_hours")
    def _compute_tshirt_estimate(self):
        for record in self:
            record.tshirt_estimate = get_size(record.allocated_hours)

    @api.depends("checklist_item_ids.state", "checklist_item_ids.user_id")
    @api.depends_context("uid")
    def _compute_completion(self):
        for record in self:
            record.completion = record.task_completion()

    @api.depends("checklist_item_ids.state", "checklist_item_ids.user_id")
    @api.depends_context("uid")
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
    @api.depends_context("uid")
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
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_note_count(self):
        # res_id, not task_id: the access check of mail.message reads the task ids from it
        note_domain = [*self.env["mail.message"]._get_task_note_domain(with_templates=True), ("res_id", "in", self.ids)]
        note_counts = dict(self.env["mail.message"]._read_group(note_domain, ["res_id"], ["__count"]))
        for record in self:
            record.note_count = note_counts.get(record.id, 0)

    @api.depends("module_ids.name")
    def _compute_module_names(self):
        for record in self:
            record.module_names = ", ".join(sorted(record.module_ids.mapped("name"))) or False

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
        vals_list, new_items_per_task = self._split_checklist_item_creations(vals_list)
        tasks = super().create(vals_list)
        # Sudo: a default of the context may set modules too, and a read checks the field's groups.
        tasks._check_modules_installed(tasks.sudo().module_ids)
        if not is_next_occurrence_creation(self.env):
            tasks._apply_closing_rule()
            tasks._apply_estimate_rule()
        # The caller's env: an override may return the tasks in sudo, past the item's access rights.
        tasks.with_env(self.env)._create_checklist_items(new_items_per_task)
        return tasks

    def write(self, vals):
        vals = self._without_size(vals)
        former_stages = self._get_former_stages(vals)
        former_modules = self._get_former_modules(vals)
        result = super().write(vals)
        self._check_modules_installed(self._get_added_modules(former_modules))
        moved_tasks = self._filter_moved_from(former_stages)
        moved_tasks._apply_closing_rule()
        moved_tasks._apply_estimate_rule()
        return result

    @api.ondelete(at_uninstall=False)
    def _unlink_image_rows(self):
        """Delete the rows of the tasks through the ORM, which deletes the attachments of their images: the
        database cascade would leave them. It runs on the tasks core deletes, sub-tasks included, after its
        access check.
        """
        for rows in self._get_image_rows():
            rows.unlink()

    def copy_data(self, default=None):
        """A task created from a template, or in a project created from one, is
        a new task to the closing rule: it takes no Resolution and no pull
        request of its source.

        Every copy takes the lines of its source here, as core builds the
        sub-tasks of a copy from their copy_data alone: from zero on a copy
        from or into a template, else as they are. The next occurrence of a
        recurring task takes its lines from the recurrence instead.
        """
        vals_list = super().copy_data(default=default)
        keep_progress = not self._is_template_copy()
        for task, vals in zip(self, vals_list):
            if not self.env.context.get("copy_from_template"):
                task._add_closing_values(vals)
            if not self.env.context.get("copy_without_lines"):
                task._add_line_values(vals, keep_progress)
        return vals_list

    def copy(self, default=None):
        copied_tasks = super().copy(default=default)
        self._write_case_verifications_on_copies(copied_tasks, default or {})
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
        action["domain"] = [
            *self.env["mail.message"]._get_task_note_domain(with_templates=True),
            ("res_id", "=", self.id),
        ]
        return action

    @api.model
    def _split_checklist_item_creations(self, vals_list):
        """Return vals_list without the checklist items it creates, and per task
        the vals of those items, for _create_checklist_items. An item posts on
        its task: inside the task's create that post comes before the task
        posts its creation and subscribes its followers, so the project's
        followers of all checklist updates miss it.

        The items are created after the field's other commands: a create
        followed by a Command.set or Command.clear in the same list survives.
        Items a default_checklist_item_ids of the context gives are not split
        and are created inside the task's create.
        """
        task_vals_list = []
        new_items_per_task = []
        for vals in vals_list:
            commands = vals.get("checklist_item_ids") or []
            new_items_vals = [command[2] for command in commands if _is_create_command(command)]
            if new_items_vals:
                vals = dict(vals)
                other_commands = [command for command in commands if not _is_create_command(command)]
                if other_commands:
                    vals["checklist_item_ids"] = other_commands
                else:
                    del vals["checklist_item_ids"]
            task_vals_list.append(vals)
            new_items_per_task.append(new_items_vals)
        return task_vals_list, new_items_per_task

    def _create_checklist_items(self, new_items_per_task):
        """Create the items of each task of self, in order, as a task saved first
        and given its items right after gets them.
        """
        item_vals_list = [
            {**item_vals, "task_id": task.id}
            for task, new_items_vals in zip(self, new_items_per_task)
            for item_vals in new_items_vals
        ]
        if item_vals_list:
            # Without the default_* keys of the context: they are the task's, as for any inline line.
            self.env["project.task.checklist.item"].with_context(clean_context(self.env.context)).create(item_vals_list)

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

    @api.model
    def _remove_closing_values(self, vals):
        """Drop the Resolution and the pull requests from the values of a task
        that is new work, as the next occurrence of a recurring task is, in
        whatever stage it starts and whatever put them there.
        """
        vals.pop("resolution", None)
        vals.pop("pull_request_ids", None)

    def _clear_closing_values(self):
        self.write({"resolution": False, "pull_request_ids": [Command.clear()]})

    def _is_stage_kept_in(self, vals):
        return bool(self.stage_id) and vals.get("stage_id") == self.stage_id.id

    @api.model
    def _is_template_copy(self):
        """Whether the copy under way creates a task from a template, the tasks
        of a project from a project template, or those of a project template
        from a project: the context core gives each.
        """
        return bool(self.env.context.get("copy_from_template") or self.env.context.get("convert_to_template"))

    def _add_line_values(self, vals, keep_progress, field_names=LINE_FIELDS):
        """Complete the values of a copy with the lines of this task, unless the
        values set them.
        """
        self.ensure_one()
        for field_name, lines_values in self._get_line_copy_values(keep_progress, field_names).items():
            vals.setdefault(field_name, [Command.create(line_values) for line_values in lines_values])

    def _get_line_copy_values(self, keep_progress, field_names=LINE_FIELDS):
        """Return {line field: [values of the copy of each line]} for the line
        fields of field_names the user may read, as core copies no other field
        of a task. keep_progress keeps the state, the result or the answer of
        each line, and the deadline of an item or the comments of a case.
        """
        return {
            field_name: self[field_name]._get_task_copy_values(keep_progress)
            for field_name in field_names
            if self.has_field_access(self._fields[field_name], "read")
        }

    def _write_case_verifications_on_copies(self, copied_tasks, default):
        """Give each Not run case of copied_tasks, sub-tasks included, the
        verification of its source case, which its create took back. A copy
        from or into a template takes its Verification alone, see
        _write_verification_on_copy.
        """
        if not self.has_field_access(self._fields["testcase_ids"], "read"):
            return
        keep_progress = not self._is_template_copy()
        for case, case_copy in self._get_copied_case_pairs(copied_tasks, default, copied_tasks):
            case._write_verification_on_copy(case_copy, keep_progress)

    def _get_copied_case_pairs(self, copied_tasks, default, new_tasks):
        """Yield each case of self and of their sub-tasks with its copy, as
        copy_data creates them: the cases of a task in order, then its active
        sub-tasks in order, none of either that default gives. new_tasks are
        left out of a source's sub-tasks: a sub-task copied along with its
        parent keeps that parent.

        Not core's _create_task_mapping: it pairs archived sub-tasks and
        new_tasks too, and its dict drops a sub-task copied with its parent.
        """
        for task, copied_task in zip(self, copied_tasks):
            if "testcase_ids" not in default:
                yield from zip(task.testcase_ids, copied_task.testcase_ids.sorted("id"))
            if not default.get("child_ids"):
                source_subtasks = task.child_ids.filtered("active") - new_tasks
                yield from source_subtasks._get_copied_case_pairs(copied_task.child_ids.sorted("id"), {}, new_tasks)

    @api.model
    def _without_size(self, vals):
        """Return the vals of a write without a size: a value written to the
        stored field would stand in place of the size of the hours. A user who
        may not write the field, a portal user, keeps it in the vals, for core
        to refuse as any field outside their reach.

        A create needs no such care: it computes a read-only field whatever
        value was sent, or came as a default of the context.
        """
        if "tshirt_estimate" not in vals or not self.has_field_access(self._fields["tshirt_estimate"], "write"):
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

    def _get_former_modules(self, vals):
        if "module_ids" not in vals:
            return {}
        return {record.id: record.module_ids for record in self.sudo()}

    def _get_added_modules(self, former_modules):
        added_modules = self.env["ir.module.module"]
        for record in self.sudo().filtered(lambda record: record.id in former_modules):
            added_modules |= record.module_ids - former_modules[record.id]
        return added_modules

    def _check_modules_installed(self, modules):
        """Callers pass only the modules they add: one already on a task stays
        when it is uninstalled. Runs after the write, as a constraint does, so
        the refusal rolls the write back.
        """
        not_installed_modules = modules.sudo().filtered(lambda module: module.state not in INSTALLED_MODULE_STATES)
        if not_installed_modules:
            raise ValidationError(
                self.env._(
                    "Only an installed module can be added to a task. Not installed: %s",
                    ", ".join(sorted(not_installed_modules.mapped("name"))),
                )
            )

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

    @api.model
    def _get_task_fname(self):
        return None

    def _get_container_changing_records(self, vals, container_fnames):
        """A sub-task follows the project of its parent, at any depth: a write of either may move them all."""
        if not {"project_id", "parent_id"} & vals.keys():
            return super()._get_container_changing_records(vals, container_fnames)
        return self.sudo().with_context(active_test=False).search([("id", "child_of", self.ids)])

    def _get_image_rows(self):
        """The question rows and the test cases of these tasks, in sudo: the rows whose texts hold images."""
        tasks = self.sudo()
        return [tasks.question_ids, tasks.testcase_ids]

    def _move_inline_images_to_attachments(self, dry_run=False, store=True):
        """Move the inline images of these tasks and of their lines to
        attachments of the records holding them, as a save does: for texts
        saved before.

        Returns {"records": how many tasks and lines change, "images": how
        many attachments are created, "bytes": their size before core resizes
        them, "texts": {record: {field name: new text}}}, the new value of the
        task properties a {property name: value} dict. An image core cannot read
        stays inline and counts nowhere. dry_run changes nothing and counts as
        a run would. store=False creates the attachments and leaves the texts
        to the caller, to store without changing write_date and write_uid.
        Every new text stays in memory until the return: call it in batches.
        """
        mover = ImageMover(self.env, dry_run=dry_run)
        new_texts = {}
        for records in (self, self.question_ids, self.testcase_ids):
            new_texts.update(records._get_moved_image_texts(mover, records._get_image_fields()))
        if store and not dry_run:
            self._store_texts(new_texts)
        return {
            "records": len(new_texts),
            "images": mover.image_count,
            "bytes": mover.byte_count,
            "texts": {} if dry_run else new_texts,
        }

    def task_completion(self):
        user_task_ids = self.checklist_item_ids.filtered(
            lambda x: x.user_id.id == self.env.user.id and x.state != "cancelled"
        )
        if not user_task_ids:
            return 100
        user_done_task_ids = user_task_ids.filtered(lambda x: x.state == "done")
        return (len(user_done_task_ids) / len(user_task_ids)) * 100
