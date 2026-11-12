from markupsafe import Markup, escape

from odoo import api, fields, models

from .project_task_subtask import SUBTASK_STATES

TSHIRT_HOURS = {
    "xs": 1,
    "s": 4,
    "m": 8,
    "l": 24,
    "xl": 40,
    "xxl": 80,
}


class ProjectTask(models.Model):
    _inherit = "project.task"

    is_ai_driven = fields.Boolean(related="project_id.is_ai_driven")
    questions = fields.Html()
    answers = fields.Html()
    solution = fields.Html()
    difficulty = fields.Selection(
        [("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard")],
        default="easy",
    )
    knowledge_area_level = fields.Selection(
        [
            ("basic", "Basic"),
            ("intermediate", "Intermediate"),
            ("advanced", "Advanced"),
        ],
        default="basic",
    )
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
        default="improvement",
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
        default="m",
        help="T-shirt size estimate: XS = 1h or less, S = 4h, M = 1d, L = 3d, XL = 1w, XXL = 2w or more",
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
    tester_id = fields.Many2one("res.users", string="Tester")
    testcase_general_comments = fields.Html()

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

    @api.onchange("tshirt_estimate")
    def _onchange_tshirt_estimate(self):
        for record in self:
            if record.tshirt_estimate and not record.planned_hours:
                record.planned_hours = TSHIRT_HOURS.get(record.tshirt_estimate, 0)

    def copy(self, default=None):
        task = super().copy(default)
        for subtask in self.subtask_ids:
            subtask.copy({"task_id": task.id, "state": subtask.state})
        for testcase in self.testcase_ids:
            testcase.copy({"task_id": task.id, "state": testcase.state})
        return task

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
