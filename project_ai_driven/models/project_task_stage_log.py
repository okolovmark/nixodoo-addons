from odoo import api, fields, models

INSERT_ROWS = """
    INSERT INTO project_task_stage_log
                (task_id, previous_stage_id, stage_id, stage_name, date, user_id, source,
                 create_uid, create_date, write_uid, write_date)
"""
# A task that already has a row was moved while it was being created: that row names both stages.
LOG_CREATED_TASKS = (
    INSERT_ROWS
    + """
         SELECT task.id, NULL, stage.id, stage.name->>'en_US', %(date)s, %(user_id)s, 'live',
                %(user_id)s, %(now)s, %(user_id)s, %(now)s
           FROM project_task task
           JOIN project_task_type stage ON stage.id = task.stage_id
          WHERE task.id IN %(task_ids)s
            AND NOT EXISTS (SELECT 1 FROM project_task_stage_log log WHERE log.task_id = task.id)
       ORDER BY task.id
    """
)
LOG_MOVED_TASKS = (
    INSERT_ROWS
    + """
         SELECT task.id, task.stage_id, stage.id, stage.name->>'en_US', %(date)s, %(user_id)s, 'live',
                %(user_id)s, %(now)s, %(user_id)s, %(now)s
           FROM project_task task
      LEFT JOIN project_task_type stage ON stage.id = %(stage_id)s
          WHERE task.id IN %(task_ids)s
            AND task.stage_id IS DISTINCT FROM %(stage_id)s
       ORDER BY task.id
    """
)


class ProjectTaskStageLog(models.Model):
    """One row each time the stored stage of a task changes, whoever changes it
    and in whatever context: the chatter tracks a stage only for callers that
    leave tracking on.

    The rows are written by SQL where the task rows are, see _create and
    _write of project.task. A user gets no right to create, change or delete
    one.
    """

    _name = "project.task.stage.log"
    _description = "Task Stage Log"
    _order = "date, id"
    # A row is written when the stage of its task is flushed: a search of the log flushes it first,
    # or a reader in the transaction of a change would miss the row while the change is pending.
    _depends = {"project.task": ["stage_id"]}

    task_id = fields.Many2one("project.task", required=True, ondelete="cascade", index=True)
    stage_id = fields.Many2one(
        "project.task.type", help="Empty when the task left its stage, and when the stage was deleted since."
    )
    stage_name = fields.Char(
        help="The name of the stage, which a deleted stage keeps: its source name (en_US) when the row was"
        " written, or, for a row from the chatter, the name the chatter tracked."
        " Empty when the task left its stage."
    )
    previous_stage_id = fields.Many2one(
        "project.task.type",
        help="Empty when there was none, when it is not known, and when that stage was deleted since.",
    )
    date = fields.Datetime(required=True)
    user_id = fields.Many2one("res.users", help="Who made the change. Empty when it is not known.")
    source = fields.Selection(
        [
            ("live", "Recorded"),
            ("tracking", "From the chatter"),
            ("stage_date", "From the last stage update"),
            ("creation", "From the creation date"),
        ],
        required=True,
    )

    @api.model
    def _log_created_tasks(self, tasks):
        """Called once the rows of tasks are inserted: log the stage each one
        was created in.
        """
        self._insert_live_rows(LOG_CREATED_TASKS, tasks, {"user_id": self.env.uid})

    @api.model
    def _log_moved_tasks(self, tasks, stage_id, user_id):
        """Called before the rows of tasks are updated with stage_id: log the
        ones whose column holds another stage, which is their previous one.
        """
        self._insert_live_rows(LOG_MOVED_TASKS, tasks, {"stage_id": stage_id or None, "user_id": user_id})

    @api.model
    def _insert_live_rows(self, query, tasks, params):
        cr = self.env.cr
        params = dict(params, date=fields.Datetime.now(), now=cr.now())
        for task_ids in cr.split_for_in_conditions(tasks.ids):
            cr.execute(query, dict(params, task_ids=task_ids))
