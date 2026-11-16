from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

CHECKLIST_ITEM_STATES = {
    "done": "Done",
    "todo": "TODO",
    "waiting": "Waiting",
    "cancelled": "Cancelled",
}
STATE_COLORS = {"done": "#080", "todo": "#A00", "cancelled": "#777", "waiting": "#b818ce"}
NOTIFIED_FIELDS = ("state", "name", "user_id")
GUARDED_FIELDS = ("state", "name")


class ProjectTaskChecklistItem(models.Model):
    _name = "project.task.checklist.item"
    _description = "Checklist Item"
    _inherit = ["mail.activity.mixin"]

    state = fields.Selection(
        selection=list(CHECKLIST_ITEM_STATES.items()),
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

    def _compute_reviewer_id(self):
        for record in self:
            record.reviewer_id = record.create_uid

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        items._notify_checklist_item_change()
        return items

    def write(self, vals):
        if any(field_name in vals for field_name in GUARDED_FIELDS):
            self._check_change_allowed()
        former_values = {record.id: (record.name, record.state) for record in self} if "name" in vals else {}
        result = super().write(vals)
        if any(field_name in vals for field_name in NOTIFIED_FIELDS):
            self._notify_checklist_item_change(former_values)
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

    def _check_change_allowed(self):
        if self.filtered(lambda record: self.env.user not in record.user_id | record.reviewer_id):
            raise UserError(_("Only the assignee or the reviewer of a checklist item can change its state or name."))

    def _notify_checklist_item_change(self, former_values=None):
        """Post each item on its task to its assignee and its reviewer, but not
        to the user making the change: a change of one's own item is a note to
        no one. Followers of the task who opted into the checklist subtype get
        the posts of others. former_values, {item id: (name, state)}, adds the
        name an item had before, under the state it had then.

        Honours mail_auto_subscribe_no_notify, as _task_message_auto_subscribe_notify
        of the task does: the post needs the author's email, and an author
        without one, such as a service account over RPC, would be refused the
        change itself.
        """
        if self.env.context.get("mail_auto_subscribe_no_notify"):
            return
        for record in self:
            recipients = (record.user_id | record.reviewer_id) - self.env.user
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
        body += _get_state_label(self.state) + escape(self.name)
        if former_values:
            former_name, former_state = former_values
            body += (
                Markup('<br><em style="color:#999">Updated from</em><br>')
                + _get_state_label(former_state)
                + escape(former_name)
            )
        return body + Markup("</p>")

    def _get_change_intro(self):
        if self.env.user == self.reviewer_id:
            return Markup()
        if self.env.user == self.user_id:
            return Markup('<em style="color:#999">I updated checklist item assigned to me:</em> ')
        return (
            Markup('<em style="color:#999">I updated checklist item, now its assigned to %s: </em> ')
            % self.user_id.name
        )


def _get_state_label(state):
    return Markup('<strong><span style="color:%s">%s</span></strong>: ') % (
        STATE_COLORS[state],
        CHECKLIST_ITEM_STATES[state],
    )
