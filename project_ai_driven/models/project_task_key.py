from odoo import api, fields, models

TASK_URL = "/web#id=%s&view_type=form&model=project.task&action=%s"


class ProjectTask(models.Model):
    _inherit = "project.task"
    _rec_names_search = ["key", "name"]

    key = fields.Char(size=20, index=True)
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
        if not vals.get("project_id") or not self.env["project.project"]._gives_task_keys():
            return super().write(vals)
        rekeyed_tasks = self.filtered(lambda record: not record.key or record.project_id.id != vals["project_id"])
        # Core moves archived sub-tasks with their parent too.
        subtasks = rekeyed_tasks.with_context(active_test=False)._get_all_subtasks()
        former_projects = {subtask: subtask.project_id for subtask in subtasks}
        result = super().write(vals)
        moved_subtasks = subtasks.filtered(lambda record: record.project_id != former_projects[record])
        (rekeyed_tasks | moved_subtasks).sorted("id")._set_next_key()
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
            project_id = vals.get("project_id") or self.env.context.get("default_project_id")
            if project_id:
                vals["key"] = self.env["project.project"].browse(project_id)._get_next_task_key()

    def _set_next_key(self):
        """Give each task the next key of its project, in the order of self; a task whose project
        gives none loses its key, which a later key of the project would rename into a collision.
        Sudo: the key is the project's to give, and a portal user may not write it.
        """
        for task in self.sudo():
            key = task.project_id._get_next_task_key()
            if key or task.key:
                task.key = key
