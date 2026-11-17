from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

STATE_COLORS = {"done": "#080", "todo": "#A00", "cancelled": "#777", "waiting": "#b818ce"}
NOTIFIED_FIELDS = ("state", "name", "user_id")
GUARDED_FIELDS = ("state", "name", "user_id")


class ProjectTaskChecklistItem(models.Model):
    _name = "project.task.checklist.item"
    _description = "Checklist Item"
    _inherit = ["project.task.access.mixin"]

    state = fields.Selection(
        selection=[("done", "Done"), ("todo", "TODO"), ("waiting", "Waiting"), ("cancelled", "Cancelled")],
        string="Status",
        required=True,
        copy=False,
        default="todo",
    )
    name = fields.Char(required=True, string="Description")
    reviewer_id = fields.Many2one("res.users", "Reviewer", readonly=True, default=lambda self: self.env.user)
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    user_id = fields.Many2one("res.users", "Assigned to", required=True, default=lambda self: self.env.user)
    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True, index=True)
    task_state = fields.Char(string="Task state", related="task_id.stage_id.name", readonly=True)
    allow_subtasks = fields.Boolean(related="task_id.allow_subtasks")
    hide_button = fields.Boolean(compute="_compute_hide_button")
    recolor = fields.Boolean(compute="_compute_recolor")
    deadline = fields.Datetime()

    @api.depends("reviewer_id", "user_id")
    def _compute_hide_button(self):
        for record in self:
            if self.env.user not in [record.reviewer_id, record.user_id]:
                record.hide_button = True
            else:
                record.hide_button = False

    @api.depends("user_id", "state")
    def _compute_recolor(self):
        for record in self:
            if self.env.user == record.user_id and record.state == "todo":
                record.recolor = True
            else:
                record.recolor = False

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        items._notify_checklist_item_change()
        return items

    def write(self, vals):
        if any(field_name in vals for field_name in GUARDED_FIELDS):
            self._check_change_allowed()
        former_values = {record.id: (record.name, record.state) for record in self} if "name" in vals else {}
        former_assignees = {record.id: record.user_id for record in self} if "user_id" in vals else {}
        result = super().write(vals)
        if any(field_name in vals for field_name in NOTIFIED_FIELDS):
            self._notify_checklist_item_change(former_values, former_assignees)
        return result

    def change_state_done(self):
        for record in self:
            record.state = "done"

    def change_state_todo(self):
        for record in self:
            record.state = "todo"

    def change_state_cancelled(self):
        for record in self:
            record.state = "cancelled"

    def change_state_waiting(self):
        for record in self:
            record.state = "waiting"

    def action_convert_to_subtask(self):
        self._convert_to_subtasks()

    def action_convert_to_subtask_open(self):
        """Convert from the form of the item, which cannot reload a deleted
        record: open the form of the sub-task instead.
        """
        self.ensure_one()
        subtask = self._convert_to_subtasks()
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": subtask.id,
            "views": [(False, "form")],
        }

    def _check_change_allowed(self):
        if self.filtered(lambda record: self.env.user not in record.user_id | record.reviewer_id):
            raise UserError(
                _("Only the assignee or the reviewer of a checklist item can change its state, name or assignee.")
            )

    def _check_subtasks_allowed(self):
        tasks = self.task_id.filtered(lambda task: not task.allow_subtasks)
        if tasks:
            raise UserError(
                _(
                    "A checklist item becomes a sub-task only in a project with Sub-tasks turned on.\nTasks: %s",
                    ", ".join(tasks.mapped("display_name")),
                )
            )

    def _convert_to_subtasks(self):
        """Replace each item with a sub-task of its task and a note on the task
        linking it. As the user: a refused step raises before the items go.
        Return the sub-tasks in the order of the items.
        """
        self._check_change_allowed()
        self._check_subtasks_allowed()
        self.check_access_rights("unlink")
        self.check_access_rule("unlink")
        subtasks = self.env["project.task"].create([record._get_subtask_values() for record in self])
        for record, subtask in zip(self, subtasks):
            # the assignee follows through user_ids
            subtask.message_subscribe(partner_ids=(record.reviewer_id - record.user_id).partner_id.ids)
            record.task_id.message_post(
                body=record._get_conversion_body(subtask),
                message_type="comment",
                subtype_xmlid="mail.mt_note",
            )
        self.unlink()
        return subtasks

    def _get_subtask_values(self):
        """No stage: the task takes the default one of its project, its first."""
        self.ensure_one()
        return {
            "name": self.name,
            "parent_id": self.task_id.id,
            "project_id": self.task_id.project_id.id,
            "user_ids": [Command.set(self.user_id.ids)],
            "date_deadline": self.deadline and fields.Datetime.context_timestamp(self, self.deadline).date(),
        }

    def _get_conversion_body(self, subtask):
        # opens with plain text: a note opening with a category in bold is filed under it
        return Markup("<p>%s</p>") % (
            escape(_("Checklist item %(item)s converted to the sub-task %(subtask)s."))
            % {"item": self.name, "subtask": subtask._get_html_link()}
        )

    def _notify_checklist_item_change(self, former_values=None, former_assignees=None):
        """Post each item on its task to its assignee and its reviewer, but not
        to the user making the change: a change of one's own item is a note to
        no one. Followers of the task who opted into the checklist subtype get
        the posts of others. former_values, {item id: (name, state)}, adds the
        name an item had before, under the state it had then;
        former_assignees, {item id: res.users}, adds the assignee it had before
        to its recipients.

        Honours mail_auto_subscribe_no_notify, as _task_message_auto_subscribe_notify
        of the task does: the post needs the author's email, and an author
        without one, such as a service account over RPC, would be refused the
        change itself.
        """
        if self.env.context.get("mail_auto_subscribe_no_notify"):
            return
        for record in self:
            former_assignee = (former_assignees or {}).get(record.id, self.env["res.users"])
            recipients = (record.user_id | record.reviewer_id | former_assignee) - self.env.user
            record.task_id.message_post(
                message_type="comment",
                body=record._get_change_body(recipients, (former_values or {}).get(record.id)),
                subtype_xmlid="project_ai_driven.checklist_subtype" if recipients else "mail.mt_note",
                partner_ids=recipients.partner_id.ids,
            )

    def _get_change_body(self, recipients, former_values):
        body = Markup("<p>")
        if recipients:
            body += (
                Markup(", ").join(recipients.mapped("name")) + Markup(", ") + self._get_change_intro() + Markup("<br>")
            )
        body += self._get_state_label(self.state) + escape(self.name)
        if former_values:
            former_name, former_state = former_values
            body += (
                Markup('<br><em style="color:#999">%s</em><br>') % _("Updated from")
                + self._get_state_label(former_state)
                + escape(former_name)
            )
        return body + Markup("</p>")

    def _get_change_intro(self):
        if self.env.user == self.reviewer_id:
            return Markup()
        if self.env.user == self.user_id:
            intro = _("I updated checklist item assigned to me:")
        else:
            intro = _("I updated checklist item, now it is assigned to %s:", self.user_id.name)
        return Markup('<em style="color:#999">%s</em> ') % intro

    def _get_state_label(self, state):
        state_labels = dict(self._fields["state"]._description_selection(self.env))
        return Markup('<strong><span style="color:%s">%s</span></strong>: ') % (
            STATE_COLORS[state],
            state_labels[state],
        )
