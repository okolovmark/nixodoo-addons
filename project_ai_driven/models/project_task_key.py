from odoo import api, fields, models

TASK_URL = "/odoo/action-project.action_view_task/%s"


class ProjectTask(models.Model):
    _inherit = "project.task"
    _rec_names_search = ["key", "name"]

    key = fields.Char(size=20, index=True, copy=False)
    url = fields.Char(string="URL", compute="_compute_url")

    _task_key_unique = models.Constraint("UNIQUE(key)", "Task key must be unique!")

    @property
    def TASK_PORTAL_READABLE_FIELDS(self):
        return super().TASK_PORTAL_READABLE_FIELDS | {"key"}

    @api.depends("key")
    def _compute_display_name(self):
        super()._compute_display_name()
        for task in self:
            if task.key:
                task.display_name = "%s - %s" % (task.key, task.display_name)

    # Made of the id alone, which no write changes.
    @api.depends()
    def _compute_url(self):
        for task in self:
            task.url = TASK_URL % task.id if task.id else False

    @api.model_create_multi
    def create(self, vals_list):
        """Give each task with a project the next key of that project."""
        self._add_next_keys(vals_list)
        tasks = super().create(vals_list)
        tasks.filtered(lambda record: record.project_id and not record.key)._set_next_key()
        return tasks

    def write(self, vals):
        """A task that changes project gets the next key of its new project, or loses its key
        outside a project, and so does each sub-task that moves with it or with its new parent; a
        task written with its own project keeps its key, or gets the next key of that project when
        it has none.
        """
        if "project_id" not in vals and "parent_id" not in vals:
            return super().write(vals)
        # Core moves archived sub-tasks with their parent too, and those the user cannot read.
        moving_tasks = (self | self.with_context(active_test=False)._get_all_subtasks()).sudo()
        former_projects = {task: task.project_id for task in moving_tasks}
        result = super().write(vals)
        rekeyed_tasks = moving_tasks.filtered(lambda record: record.project_id != former_projects[record])
        if "project_id" in vals:
            rekeyed_tasks |= self.filtered(lambda record: not record.key)
        rekeyed_tasks.sorted("id")._set_next_key()
        return result

    @api.model
    def _add_next_keys(self, vals_list):
        """Give the values of each task with a project the next key of that project: the
        notifications sent from the create already name the task with it. Not for a portal user,
        whose values core refuses with a key: their tasks get it once created.
        """
        if self.env.user._is_portal() and not self.env.su:
            return
        for vals in vals_list:
            project_id = vals["project_id"] if "project_id" in vals else self.env.context.get("default_project_id")
            if project_id:
                vals["key"] = self.env["project.project"].browse(project_id)._get_next_task_key()

    def _set_next_key(self):
        """Give each task the next key of its project, in the order of self; a task whose project
        gives none, or that has no project, loses its key: a later key of the project would rename it
        into a collision, and a reused key of its former project would draw it again. Sudo: the key
        is the project's to give, and a portal user may not write it.
        """
        for task in self.sudo():
            key = task.project_id._get_next_task_key()
            if key or task.key:
                task.key = key
