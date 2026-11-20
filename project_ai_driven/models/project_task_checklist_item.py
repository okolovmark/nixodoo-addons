from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context

STATE_COLORS = {"done": "#080", "todo": "#A00", "cancelled": "#777", "waiting": "#b818ce"}
NOTIFIED_FIELDS = ("state", "name", "user_id")
GUARDED_FIELDS = ("state", "name", "user_id")
HIERARCHY_OPERATORS = ("child_of", "parent_of")


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
    hide_button = fields.Boolean(compute="_compute_hide_button")
    can_convert = fields.Boolean(compute="_compute_can_convert")
    recolor = fields.Boolean(compute="_compute_recolor")
    deadline = fields.Datetime()

    @api.depends("reviewer_id", "user_id")
    def _compute_hide_button(self):
        for record in self:
            if self.env.user not in [record.reviewer_id, record.user_id]:
                record.hide_button = True
            else:
                record.hide_button = False

    @api.depends("reviewer_id", "user_id", "task_id.allow_subtasks", "task_id.project_id.privacy_visibility")
    @api.depends_context("uid", "allowed_company_ids")
    def _compute_can_convert(self):
        refused_items = (
            self._filter_items_of_others()
            | self._filter_items_without_subtasks()
            | self._filter_items_not_deletable()
            | self._filter_items_without_task_creation()
        )
        for record in self:
            record.can_convert = record not in refused_items

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

    def _get_copy_values_from_zero(self):
        """The values of a copy of each item that starts it over: TODO without deadline."""
        return [record.copy_data({"state": "todo", "deadline": False})[0] for record in self]

    def _check_change_allowed(self):
        if self._filter_items_of_others():
            raise UserError(
                _("Only the assignee or the reviewer of a checklist item can change its state, name or assignee.")
            )

    def _check_conversion_allowed(self):
        """Refuse what can_convert hides, before anything is created."""
        if self._filter_items_of_others():
            raise UserError(_("Only the assignee or the reviewer of a checklist item can convert it into a sub-task."))
        self._check_subtasks_allowed()
        self.check_access_rights("unlink")
        self.check_access_rule("unlink")
        projects = self._filter_items_without_task_creation().task_id.project_id.sudo()
        if len(projects) == 1:
            raise UserError(
                _(
                    "You may not create tasks in the project %s, so you cannot convert its checklist items"
                    " into sub-tasks.",
                    projects.display_name,
                )
            )
        if projects:
            raise UserError(
                _(
                    "You may not create tasks in the projects %s, so you cannot convert their checklist items"
                    " into sub-tasks.",
                    ", ".join(projects.mapped("display_name")),
                )
            )

    def _filter_items_of_others(self):
        """The items the user is neither the assignee nor the reviewer of."""
        return self.filtered(lambda record: self.env.user not in record.user_id | record.reviewer_id)

    def _filter_items_without_subtasks(self):
        return self.filtered(lambda record: not record.task_id.allow_subtasks)

    def _filter_items_not_deletable(self):
        if not self.check_access_rights("unlink", raise_exception=False):
            return self
        return self - self._filter_access_rules_python("unlink")

    def _filter_items_without_task_creation(self):
        """The items whose sub-task the user may not create: the user's read
        and create rules of project.task, any module's included, are evaluated
        on the sub-task as core holds it when it reads the new row. Fields
        core computes only after that read are computed here: a rule on one of
        them can still refuse the conversion after the click. The create rules
        see no assignee either, which core writes before it checks them: a
        create rule on the assignees refuses here what core allows.
        """
        task_model = self.env["project.task"]
        if not task_model.check_access_rights("create", raise_exception=False):
            return self
        rule_domains = [
            self._resolve_hierarchy_leaves(self.env["ir.rule"]._compute_domain(task_model._name, mode) or [])
            for mode in ("read", "create")
        ]
        refused_tasks = task_model.browse()
        for task, record in {record.task_id: record for record in self}.items():
            subtask = record._new_subtask_as_read_on_creation()
            if not all(subtask.filtered_domain(domain) for domain in rule_domains):
                refused_tasks |= task
        return self.filtered(lambda record: record.task_id in refused_tasks)

    @api.model
    def _resolve_hierarchy_leaves(self, task_domain):
        """task_domain with each child_of and parent_of leaf turned into the
        records it matches: filtered_domain searches those leaves by id, which
        a sub-task not yet created has none of. A leaf on the task's own id is
        taken as matching no new task, where core may match one by its parent.
        """
        resolved_domain = []
        for leaf in task_domain:
            if isinstance(leaf, (list, tuple)) and leaf[1] in HIERARCHY_OPERATORS:
                path, operator, value = leaf
                target_model = self.env["project.task"].sudo()
                for field_name in path.split("."):
                    if field_name != "id":
                        target_model = target_model[field_name]
                leaf = (path, "in", target_model.search([("id", operator, value)]).ids)
            resolved_domain.append(leaf)
        return resolved_domain

    def _check_subtasks_allowed(self):
        tasks = self._filter_items_without_subtasks().task_id
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
        The sub-task takes no default of the calling view, which is about the
        item or its task. Return the sub-tasks in the order of the items.
        """
        self._check_conversion_allowed()
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
        """No stage: the cleaned context holds no default_stage_id, so the task
        takes the first unfolded stage of its project (core's stage_find with
        fold False).
        """
        self.ensure_one()
        return {
            "name": self.name,
            "parent_id": self.task_id.id,
            "project_id": self.task_id.project_id.id,
            "user_ids": [Command.set(self.user_id.ids)],
            "date_deadline": self.deadline and fields.Datetime.context_timestamp(self, self.deadline).date(),
        }

    def _new_subtask_as_read_on_creation(self):
        """The sub-task of the item, not saved, as core holds it when it reads
        the new row: its parent, project, company and creator written, its
        assignees and followers not yet,
        the company set as project.task's create sets it, no default of the
        calling view, as _convert_to_subtasks. As superuser, so that only the
        rule domains evaluated on it judge the user.
        """
        self.ensure_one()
        subtask_values = self._get_subtask_values()
        # the saved parent: the new task of an onchange would list the sub-task among its children
        parent = self.env["project.task"].browse([subtask_values["parent_id"]])._origin
        project = self.env["project.project"].sudo().browse([subtask_values["project_id"]])._origin
        return (
            self.env["project.task"]
            .with_context(clean_context(self.env.context))
            .sudo()
            .new(
                {
                    "parent_id": parent.id,
                    "project_id": project.id,
                    "company_id": project.company_id.id or self.env.company.id,
                    "create_uid": self.env.uid,
                    # left out, a new record would take default_get's assignee, the current user
                    "user_ids": [],
                }
            )
        )

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
