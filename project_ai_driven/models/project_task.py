import math
from collections import Counter

from markupsafe import escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools.misc import clean_context

from .project_pull_request import parse_single_pull_request_link
from .project_task_image_mixin import ImageMover
from .project_task_testcase import TESTCASE_STATES

KANBAN_TESTCASE_STATES = ("not_run", "failed", "waiting")
# Marked for an upgrade or a removal, a module is still installed.
INSTALLED_MODULE_STATES = ("installed", "to upgrade", "to remove")

# The largest estimate in hours of each size. RECOMPUTE_TASK_SIZES of the hooks states the same in SQL.
SIZE_BANDS = ((2, "xs"), (6, "s"), (12, "m"), (32, "l"), (60, "xl"), (float("inf"), "xxl"))


def get_size(hours):
    if not hours or hours <= 0:
        return False
    # The default is the ELSE of the SQL: a NaN is under no bound here and above every one there.
    return next((size for max_hours, size in SIZE_BANDS if hours <= max_hours), SIZE_BANDS[-1][1])


def _is_create_command(command):
    # A list from the web client, with a virtual id; a tuple from Command.create.
    return isinstance(command, (list, tuple)) and command[0] == Command.CREATE


class ProjectTask(models.Model):
    _name = "project.task"
    # the mixin comes below core: its write sees every task core writes, those of a recurrence too
    _inherit = ["project.task", "project.task.image.mixin"]

    is_ai_driven = fields.Boolean(related="project_id.is_ai_driven")
    develops_this_odoo = fields.Boolean(related="project_id.develops_this_odoo")
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
    session_ids = fields.One2many(
        "project.task.session", "task_id", string="Sessions", groups="project.group_project_user"
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
    note_count = fields.Integer("Notes", compute="_compute_note_count")
    # No field domain: it would filter the reads too, and a module uninstalled later stays on the task.
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

    @api.depends("planned_hours")
    def _compute_tshirt_estimate(self):
        for record in self:
            record.tshirt_estimate = get_size(record.planned_hours)

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

    # message_ids clears only the count cached within a transaction: an open form shows a new note on reload
    @api.depends("message_ids")
    def _compute_note_count(self):
        note_domain = expression.AND([self.env["mail.message"]._get_task_note_domain(), [("task_id", "in", self.ids)]])
        # sudo: core groups messages for administrators alone, and these are notes of the tasks read
        note_groups = self.env["mail.message"].sudo()._read_group(note_domain, ["res_id"], ["res_id"])
        note_counts = {group["res_id"]: group["res_id_count"] for group in note_groups}
        for record in self:
            record.note_count = note_counts.get(record.id, 0)

    @api.depends("module_ids.name")
    def _compute_module_names(self):
        for record in self:
            record.module_names = ", ".join(sorted(record.module_ids.mapped("name"))) or False

    @api.constrains("planned_hours")
    def _check_estimate_is_a_number(self):
        """A NaN passes no comparison of the rules, and the JSON the web client
        receives would carry a NaN or an infinity bare, which is not JSON.
        """
        tasks = self.filtered(lambda record: not math.isfinite(record.planned_hours))
        if tasks:
            raise ValidationError(
                _("The Estimate is a finite number of hours: NaN and infinity are refused.\nTasks: %s")
                % ", ".join(tasks.mapped("display_name"))
            )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list, new_items_per_task = self._split_checklist_item_creations(vals_list)
        tasks = super().create(vals_list)
        # Sudo: an empty module_ids in vals leaves nothing cached, and a read checks the field's groups.
        tasks._check_modules_installed(tasks.sudo().module_ids)
        tasks._apply_closing_rule()
        tasks._apply_estimate_rule()
        # The caller's env: core returns a portal user's tasks in sudo, past the item's access rights.
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

    def copy(self, default=None):
        task = super().copy(self._with_closing_values(default))
        for item in self.checklist_item_ids:
            item.copy({"task_id": task.id, "state": item.state})
        for testcase in self.testcase_ids:
            testcase.copy({"task_id": task.id, **testcase._get_result_values()})
        for question in self.question_ids:
            question.copy({"task_id": task.id, "state": question.state})
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

    def action_open_notes(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("project_ai_driven.mail_message_action_task_note")
        action["domain"] = expression.AND(
            [self.env["mail.message"]._get_task_note_domain(), [("task_id", "=", self.id)]]
        )
        return action

    @api.model
    def _split_checklist_item_creations(self, vals_list):
        """Return vals_list without the checklist items it creates, and per task
        the vals of those items, for _create_checklist_items. An item posts on
        its task: inside the task's create that post comes before the task's
        followers are subscribed, so they miss it, and subscribing the creator
        after it breaks the unique constraint of the followers.
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

    def _get_former_modules(self, vals):
        if "module_ids" not in vals:
            return {}
        return {record.id: record.module_ids for record in self.sudo()}

    def _get_added_modules(self, former_modules):
        added_modules = self.env["ir.module.module"]
        for record in self.filtered(lambda record: record.id in former_modules):
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
                _("Only an installed module can be added to a task. Not installed: %s")
                % ", ".join(sorted(not_installed_modules.mapped("name")))
            )

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
                # not above zero: a NaN is neither above nor below it
                self.filtered(lambda record: not record.planned_hours > 0),
            ),
        ]

    def _get_image_task(self):
        return self

    @api.model
    def _get_image_task_fname(self):
        return None

    def _move_inline_images_to_attachments(self, dry_run=False, store=True):
        """Move the inline images of these tasks and of their lines to
        attachments of the tasks, as a save does: for texts saved before.

        Returns {"records": the tasks and lines changed, "images": the
        attachments created, "bytes": their size before core resizes them,
        "texts": {record: {field name: new text}}}. An image core cannot read
        stays inline and counts nowhere. dry_run changes nothing and counts as
        a run would. store=False creates the attachments and leaves the texts
        to the caller, to store without changing write_date and write_uid.
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
