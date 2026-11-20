import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

TASK_URL = "/web#id=%s&view_type=form&model=project.task&action=%s"
# A sub-task's project is computed from its parent and its displayed project.
PROJECT_MOVING_FIELDS = ("project_id", "parent_id", "display_project_id")
# Only a key ending in a hyphen and a number can collide: a sequence or a rename gave it. A key
# typed by hand stays, as the rename of a project key leaves it.
STALE_KEY_TASKS = """
    SELECT task.id, task.key
      FROM project_task task
 LEFT JOIN project_project project ON project.id = task.project_id
     WHERE task.key ~ '-[0-9]+$'
       AND (project.key IS NULL OR left(task.key, length(project.key) + 1) <> project.key || '-')
       AND (%(task_ids)s::int[] IS NULL OR task.id = ANY(%(task_ids)s::int[]))
  ORDER BY task.id
"""
CLEAR_TASK_KEYS = "UPDATE project_task SET key = NULL WHERE id = ANY(%s)"
RENEW_TASK_KEYS = """
    UPDATE project_task task
       SET key = renewed_task.key
      FROM unnest(%s::int[], %s::varchar[]) AS renewed_task(id, key)
     WHERE renewed_task.id = task.id
"""


class ProjectTask(models.Model):
    _inherit = "project.task"
    _rec_names_search = ["key", "name"]

    key = fields.Char(size=20, index=True, copy=False)
    url = fields.Char(string="URL", compute="_compute_url")

    _sql_constraints = [("task_key_unique", "UNIQUE(key)", "Task key must be unique!")]

    # Made of the id alone, which no write changes.
    @api.depends()
    def _compute_url(self):
        action_id = self.env.ref("project.action_view_task").id
        for task in self:
            task.url = TASK_URL % (task.id, action_id)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env["project.project"]._gives_task_keys():
            return super().create(vals_list)
        self._add_next_keys(vals_list)
        tasks = super().create(vals_list)
        tasks.filtered(lambda record: record.project_id and not record.key)._set_next_key()
        return tasks

    def write(self, vals):
        """A task that changes project gets the next key of its new project, or loses its key
        outside a project, and so does each sub-task that moves with it, with its new parent or
        with its own displayed project, and each other task of its recurrence that the write
        reaches (_get_tasks_written_with); a task written with its own project keeps its key, or
        gets one if it has none.
        """
        if vals.keys().isdisjoint(PROJECT_MOVING_FIELDS) or not self.env["project.project"]._gives_task_keys():
            return super().write(vals)
        # Sudo: core moves sub-tasks the writing user may not read.
        written_tasks = self._get_tasks_written_with(vals).sudo()
        keyless_tasks = written_tasks.filtered(lambda record: not record.key) if "project_id" in vals else self.browse()
        # Core moves archived sub-tasks with their parent too; its search for them reads active_test.
        moving_tasks = written_tasks | written_tasks.with_context(active_test=False)._get_all_subtasks()
        former_projects = {task: task.project_id for task in moving_tasks}
        result = super().write(vals)
        moved_tasks = moving_tasks.filtered(lambda record: record.project_id != former_projects[record])
        (moved_tasks | keyless_tasks).sorted("id")._set_next_key()
        return result

    def name_get(self):
        if not self.env["project.project"]._gives_task_keys():
            return super().name_get()
        names = dict(super().name_get())
        return [(task.id, "%s - %s" % (task.key, names[task.id]) if task.key else names[task.id]) for task in self]

    @api.model
    def _add_next_keys(self, vals_list):
        """Give the values of each task with a project the next key of that project: the
        notifications sent from the create already name the task with it. Not for a portal user,
        whose values core refuses with a key: their tasks get it once created.
        """
        if self.env.user.has_group("base.group_portal") and not self.env.su:
            return
        for vals in vals_list:
            project_id = vals["project_id"] if "project_id" in vals else self.env.context.get("default_project_id")
            if project_id:
                vals["key"] = self.env["project.project"].browse(project_id)._get_next_task_key()

    def _set_next_key(self):
        """Give each task the next key of its project, in the order of self; a task whose project
        gives none, or that has no project, loses its key: a later key of the project would rename
        it into a collision, and a reused key of its former project would draw it again. Sudo: the
        key is the project's to give, and a portal user may not write it.
        """
        for task in self.sudo():
            key = task.project_id._get_next_task_key()
            if key or task.key:
                task.key = key

    @api.model
    def _renew_stale_keys(self, tasks=None):
        """Of the keys ending in a hyphen and a number, clear each one of a task without a project,
        and give each task whose key does not start with the key of its project and a hyphen the
        next key of its project, or none when it gives none, in the order the tasks were created;
        log each old and new key, and return the number of keys cleared and of keys renewed. tasks
        limits the run to those tasks, None runs it over all. SQL writes the keys, so the write
        dates stay.

        Nothing changes while keys are off: every project would give none, and the tasks would lose
        keys their projects give.
        """
        if self.env["project.project"]._is_task_key_off():
            return 0, 0
        self.env["project.project"].flush_model(["key"])
        self.flush_model(["key", "project_id"])
        self.env.cr.execute(STALE_KEY_TASKS, {"task_ids": None if tasks is None else tasks.ids})
        old_keys = dict(self.env.cr.fetchall())
        stale_tasks = self.browse(list(old_keys))
        new_keys = [task.project_id._get_next_task_key() or None for task in stale_tasks]
        renewed_keys = {task_id: key for task_id, key in zip(stale_tasks.ids, new_keys) if key}
        # UNIQUE(key) is checked row by row: a new key may be the old key of a row updated after it.
        self.env.cr.execute(CLEAR_TASK_KEYS, (stale_tasks.ids,))
        self.env.cr.execute(RENEW_TASK_KEYS, (list(renewed_keys), list(renewed_keys.values())))
        self.invalidate_model(["key", "display_name"])
        for task_id, new_key in zip(stale_tasks.ids, new_keys):
            _logger.info("Stale key of task %s: %s -> %s", task_id, old_keys[task_id], new_key or "none")
        cleared_count = len(new_keys) - len(renewed_keys)
        _logger.info("Stale task keys: %d cleared, %d renewed", cleared_count, len(renewed_keys))
        return cleared_count, len(renewed_keys)
