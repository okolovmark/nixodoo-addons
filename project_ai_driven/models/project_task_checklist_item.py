from markupsafe import Markup, escape

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools.misc import clean_context

from .task_line import check_task_line_move

STATE_COLORS = {"done": "#080", "todo": "#A00", "cancelled": "#777", "waiting": "#b818ce"}
NOTIFIED_FIELDS = ("state", "name", "user_id")
GUARDED_FIELDS = ("state", "name", "user_id")


class ProjectTaskChecklistItem(models.Model):
    _name = "project.task.checklist.item"
    _description = "Checklist Item"

    state = fields.Selection(
        selection=[("done", "Done"), ("todo", "TODO"), ("waiting", "Waiting"), ("cancelled", "Cancelled")],
        string="Status",
        required=True,
        copy=False,
        default="todo",
    )
    # copy=True: a copied task carries its items under their own name, not "name (copy)"
    name = fields.Char(required=True, string="Description", copy=True)
    reviewer_id = fields.Many2one("res.users", "Reviewer", readonly=True, default=lambda self: self.env.user)
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    user_id = fields.Many2one("res.users", "Assigned to", required=True, default=lambda self: self.env.user)
    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True, index=True)
    task_state = fields.Char(string="Task state", related="task_id.stage_id.name", readonly=True)
    hide_button = fields.Boolean(compute="_compute_hide_button")
    can_convert = fields.Boolean(compute="_compute_can_convert")
    recolor = fields.Boolean(compute="_compute_recolor")
    deadline = fields.Datetime()

    @api.depends("reviewer_id", "user_id")
    @api.depends_context("uid")
    def _compute_hide_button(self):
        for record in self:
            if self.env.user not in [record.reviewer_id, record.user_id]:
                record.hide_button = True
            else:
                record.hide_button = False

    @api.depends(
        "reviewer_id", "user_id", "task_id.project_id", "task_id.project_id.privacy_visibility", "task_id.is_template"
    )
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_can_convert(self):
        refused_items = (
            self._filter_items_of_others()
            | self._filter_items_of_private_tasks()
            | self._filter_items_of_read_only_tasks()
            | self._filter_items_without_project_access()
        )
        for record in self:
            record.can_convert = record not in refused_items

    @api.depends("user_id", "state")
    @api.depends_context("uid")
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
        check_task_line_move(self, vals)
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
        # XML-RPC cannot marshal None: a tool would get a fault after the conversion was saved
        return True

    def action_convert_to_subtask_open(self):
        """Convert from the form of the item, which cannot reload a deleted
        record: open the form of the sub-task in place of the breadcrumbs,
        which would lead back to the item.
        """
        self.ensure_one()
        subtask = self._convert_to_subtasks()
        return {
            "type": "ir.actions.act_window",
            "res_model": "project.task",
            "res_id": subtask.id,
            "views": [(False, "form")],
            "target": "main",
        }

    def _convert_to_subtasks(self):
        """Replace each item with a sub-task of its task and a note on the task
        linking it. As the user: a refused step raises before the items go.
        The sub-task takes no default of the calling view, which is about the
        item or its task. Return the sub-tasks in the order of the items.
        """
        self._check_conversion_allowed()
        self.check_access("unlink")
        subtasks = (
            self.env["project.task"]
            .with_context(clean_context(self.env.context))
            .create([record._get_subtask_values() for record in self])
        )
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
        """No stage: in the cleaned context core gives the sub-task the first
        unfolded stage of its project by sequence (the first folded one when all
        are folded), and none to a template without a project.
        A sub-task of a template is a template, as core's Sub-tasks tab makes it.
        """
        self.ensure_one()
        return {
            "name": self.name,
            "parent_id": self.task_id.id,
            "project_id": self.task_id.project_id.id,
            "is_template": self.task_id.is_template,
            "user_ids": [Command.set(self.user_id.ids)],
            "date_deadline": self.deadline,
        }

    def _get_conversion_body(self, subtask):
        # opens with plain text: a note opening with a category in bold is filed under it
        return Markup("<p>%s</p>") % self.env._(
            "Checklist item %(item)s converted to the sub-task %(subtask)s.",
            item=self.name,
            subtask=subtask._get_html_link(),
        )

    def _get_task_copy_values(self, keep_progress):
        """The values of a copy of each item for a copy of its task: with
        keep_progress its state and deadline, else TODO without deadline.
        """
        from_zero = {} if keep_progress else {"state": "todo", "deadline": False}
        return [{**values, "state": item.state, **from_zero} for item, values in zip(self, self.copy_data())]

    def _check_change_allowed(self):
        if self._filter_items_of_others():
            raise UserError(
                self.env._(
                    "Only the assignee or the reviewer of a checklist item can change its state, name or assignee."
                )
            )

    def _check_conversion_allowed(self):
        """Refuse what can_convert hides, before anything is created."""
        if self._filter_items_of_others():
            raise UserError(
                self.env._("Only the assignee or the reviewer of a checklist item can convert it into a sub-task.")
            )
        if self._filter_items_of_private_tasks():
            raise UserError(
                self.env._(
                    "The checklist items of a private task cannot be converted into sub-tasks:"
                    " set a project on the task first."
                )
            )
        if self._filter_items_of_read_only_tasks():
            # an AccessError, as the deletion of the item would raise
            raise AccessError(
                self.env._("Only a user who may change the task can convert its checklist items into sub-tasks.")
            )
        items_without_project_access = self._filter_items_without_project_access()
        if items_without_project_access.filtered(lambda record: not record.task_id.project_id):
            raise UserError(
                self.env._(
                    "On a task without a project only the assignee of a checklist item or a project manager"
                    " can convert it into a sub-task."
                )
            )
        if items_without_project_access:
            raise UserError(
                self.env._(
                    "On a task of a project you have no access to, only the assignee of a checklist item"
                    " can convert it into a sub-task."
                )
            )

    def _filter_items_of_others(self):
        """The items the user is neither the assignee nor the reviewer of."""
        return self.filtered(lambda record: self.env.user not in record.user_id | record.reviewer_id)

    def _filter_items_of_private_tasks(self):
        # core refuses a sub-task under a private task (_private_task_has_no_parent)
        return self.filtered(lambda record: not record.task_id.project_id and not record.task_id.is_template)

    def _filter_items_of_read_only_tasks(self):
        """The items of the tasks the user may not change: deleting an item
        takes write access to its task.
        """
        writable_tasks = self.task_id._filtered_access("write")
        return self.filtered(lambda record: record.task_id not in writable_tasks)

    def _filter_items_without_project_access(self):
        """The items whose sub-task core's create rule refuses the user: a
        project user creates a task in a project they may read or as one of its
        assignees, the sub-task's being the item's; following the task does not
        count, as the creator follows it only once created. A project manager
        creates a task in any project and any task template.
        """
        if self.env.user.has_group("project.group_project_manager"):
            return self.browse()
        readable_projects = self.task_id.project_id._filtered_access("read")
        return self.filtered(
            lambda record: record.user_id != self.env.user and record.task_id.project_id not in readable_projects
        )

    def _notify_checklist_item_change(self, former_values=None, former_assignees=None):
        """Post each item on its task to its assignee and its reviewer, never to
        the user making the change: a change of one's own item is a note to no
        one. former_values, {item id: (name, state)}, adds the name the item had
        before under the state it had then; former_assignees, {item id: res.users},
        adds the former assignee to the recipients.

        Honours mail_auto_subscribe_no_notify as the task's
        _task_message_auto_subscribe_notify does: an integration writing items
        asks for a silent change with it.
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
                Markup('<br><em style="color:#999">%s</em><br>') % self.env._("Updated from")
                + self._get_state_label(former_state)
                + escape(former_name)
            )
        return body + Markup("</p>")

    def _get_change_intro(self):
        if self.env.user == self.reviewer_id:
            return Markup()
        if self.env.user == self.user_id:
            intro = self.env._("I updated checklist item assigned to me:")
        else:
            intro = self.env._("I updated checklist item, now it is assigned to %s:", self.user_id.name)
        return Markup('<em style="color:#999">%s</em> ') % intro

    def _get_state_label(self, state):
        state_labels = dict(self._fields["state"]._description_selection(self.env))
        return Markup('<strong><span style="color:%s">%s</span></strong>: ') % (
            STATE_COLORS[state],
            state_labels[state],
        )
