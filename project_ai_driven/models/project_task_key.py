from odoo import api, fields, models

TASK_URL = "/odoo/action-project.action_view_task/%s"


class ProjectTask(models.Model):
    _inherit = "project.task"
    _rec_names_search = ["key", "name"]

    key = fields.Char(size=20, index=True)
    url = fields.Char(string="URL", compute="_compute_url")

    _task_key_unique = models.Constraint("UNIQUE(key)", "Task key must be unique!")

    @api.depends("key")
    def _compute_display_name(self):
        super()._compute_display_name()
        # Sudo: a portal user, who may not read the key, sees it in the name as everyone does.
        for task in self:
            key = task.sudo().key
            if key:
                task.display_name = "%s - %s" % (key, task.display_name)

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
        tasks.sudo().filtered(lambda record: record.project_id and not record.key)._set_next_key()
        return tasks

    def write(self, vals):
        """A task moved to another project gets its next key, and so does each sub-task that moves
        with it; a task written with its own project keeps its key.
        """
        if not vals.get("project_id"):
            return super().write(vals)
        rekeyed_tasks = self.filtered(
            lambda record: not record.sudo().key or record.project_id.id != vals["project_id"]
        )
        # Core moves archived sub-tasks with their parent too.
        subtasks = rekeyed_tasks.with_context(active_test=False)._get_all_subtasks()
        former_projects = {subtask: subtask.project_id for subtask in subtasks}
        result = super().write(vals)
        moved_subtasks = subtasks.filtered(lambda record: record.project_id != former_projects[record])
        (rekeyed_tasks | moved_subtasks).sorted("id")._set_next_key()
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
            project_id = vals.get("project_id") or self.env.context.get("default_project_id")
            if project_id:
                vals["key"] = self.env["project.project"].browse(project_id)._get_next_task_key()

    def _set_next_key(self):
        """Give each task the next key of its project, in the order of self; a task whose project
        gives none loses its key, which a later key of the project would rename into a collision.
        Sudo: the key is the project's to give, and a portal user may neither read nor write it.
        """
        for task in self.sudo():
            key = task.project_id._get_next_task_key()
            if key or task.key:
                task.key = key
