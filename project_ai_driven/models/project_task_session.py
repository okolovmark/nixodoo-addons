from odoo import api, fields, models

from .project_task_question import DISPLAY_NAME_WIDTH, _shorten_text
from .task_line import check_task_line_move

SESSION_KEY_FIELDS = ("task_id", "session_id")


class ProjectTaskSession(models.Model):
    _name = "project.task.session"
    _description = "Task Session"
    _order = "date desc, id desc"

    task_id = fields.Many2one("project.task", "Task", ondelete="cascade", required=True)
    project_id = fields.Many2one("project.project", related="task_id.project_id", store=True)
    session_id = fields.Char(
        "Session",
        required=True,
        index=True,
        help="The id of the agent session that worked on the task."
        " Where the Entire CLI records sessions, entire session info <id> opens it.",
    )
    date = fields.Datetime(required=True, default=fields.Datetime.now, help="When the session last wrote its row.")
    user_id = fields.Many2one(
        "res.users", "Worked by", default=lambda self: self.env.user, domain=[("share", "=", False)]
    )
    outcome = fields.Char(help="What the session did, in one line.")

    _task_session_unique = models.Constraint(
        "UNIQUE(task_id, session_id)",
        "A task lists a session once: update the row of that session instead.",
    )

    @api.depends("outcome", "session_id")
    def _compute_display_name(self):
        for record in self:
            record.display_name = _shorten_text(record.outcome or record.session_id or "", DISPLAY_NAME_WIDTH)

    @api.model_create_multi
    def create(self, vals_list):
        """A task and session that already have a row update that row, which is
        returned at that position; a pair given twice keeps the later values:
        see ADR 0010.
        """
        defaults = self._get_session_key_defaults(vals_list)
        self._check_tasks_readable(self._get_named_task_ids(vals_list, defaults))
        keys = self._get_session_keys(vals_list, defaults)
        rows_by_key = self._find_rows_by_key(keys)
        update_vals_by_row, create_vals_by_key = self._group_session_values(keys, vals_list, rows_by_key)
        for row, update_vals in update_vals_by_row.items():
            row.write(update_vals)
        created_rows = super().create(list(create_vals_by_key.values()))
        rows_by_key.update(zip(create_vals_by_key, created_rows))
        return self.browse([rows_by_key[key].id for key in keys])

    def write(self, vals):
        """A row moved to another task takes read, then write access to that task; a move onto a task and
        session that have a row fails on the unique constraint. A write leaves the date alone unless it names it.
        """
        if vals.get("task_id"):
            self._check_tasks_readable([vals["task_id"]])
        check_task_line_move(self, vals)
        return super().write(vals)

    @api.model
    def _check_tasks_readable(self, task_ids):
        """Odoo checks a create after its insert and a write against the task a
        row leaves: the unique constraint would tell the caller which sessions
        a task they cannot read lists, so such a task is refused first.
        """
        self.env["project.task"].browse(task_ids).check_access("read")

    @api.model
    def _get_session_key_defaults(self, vals_list):
        """The defaults of the task and the session, for the vals that do not name them."""
        missing_fields = [name for name in SESSION_KEY_FIELDS if any(name not in vals for vals in vals_list)]
        return self.default_get(missing_fields) if missing_fields else {}

    @api.model
    def _get_named_task_ids(self, vals_list, defaults):
        """The tasks vals_list names, the default task included, whether or not a session is named too."""
        return {vals.get("task_id", defaults.get("task_id", False)) for vals in vals_list} - {False}

    @api.model
    def _get_session_keys(self, vals_list, defaults):
        """The (task, session) pair each vals names, defaults included; a vals
        that does not name both gets a key of its own and is created on its own.
        """
        keys = []
        for vals in vals_list:
            pair = tuple(vals.get(name, defaults.get(name)) for name in SESSION_KEY_FIELDS)
            keys.append(pair if all(pair) else object())
        return keys

    @api.model
    def _find_rows_by_key(self, keys):
        pairs = {key for key in keys if isinstance(key, tuple)}
        if not pairs:
            return {}
        rows = self.search(
            [
                ("task_id", "in", [task_id for task_id, _session_id in pairs]),
                ("session_id", "in", [session_id for _task_id, session_id in pairs]),
            ]
        )
        return {(row.task_id.id, row.session_id): row for row in rows if (row.task_id.id, row.session_id) in pairs}

    @api.model
    def _group_session_values(self, keys, vals_list, rows_by_key):
        """The values to write on each row found, without the task and session
        it already has and stamped now unless they give a date, and the values
        to create each new pair with.
        """
        update_vals_by_row = {}
        create_vals_by_key = {}
        for key, vals in zip(keys, vals_list):
            row = rows_by_key.get(key)
            if row:
                update_vals = {name: value for name, value in vals.items() if name not in SESSION_KEY_FIELDS}
                update_vals.setdefault("date", fields.Datetime.now())
                update_vals_by_row[row] = self._merge_session_values(update_vals_by_row.get(row, {}), update_vals)
            else:
                create_vals_by_key[key] = self._merge_session_values(create_vals_by_key.get(key, {}), vals)
        return update_vals_by_row, create_vals_by_key

    @api.model
    def _merge_session_values(self, earlier_vals, later_vals):
        """later_vals over earlier_vals as a second create would apply them: the
        earlier date goes, so a later vals without one is stamped now.
        """
        return {**{name: value for name, value in earlier_vals.items() if name != "date"}, **later_vals}
