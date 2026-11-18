from odoo import models
from odoo.osv import expression

from .project_member import RESTRICTED_ACCESS_MODES

# Per model of a rule: the path to its project and the path to its task, "" for the record itself.
ACCESS_PATHS = {
    "project.project": ("", None),
    "project.member": ("project_id", None),
    "project.task": ("project_id", ""),
    "project.task.checklist.item": ("project_id", "task_id"),
    "project.task.question": ("project_id", "task_id"),
    "project.task.testcase": ("project_id", "task_id"),
    "project.task.session": ("project_id", "task_id"),
    "project.pull.request": ("task_ids.project_id", "task_ids"),
    "report.project.task.user": ("project_id", None),
    "project.task.burndown.chart.report": ("project_id", None),
    "project.milestone": ("project_id", None),
    "project.update": ("project_id", None),
}
# Changing them is a project setting.
PROJECT_SETTING_MODELS = ("project.project", "project.milestone", "project.update")
# The role a task operation other than read and write needs.
TASK_OPERATION_ROLE_FIELDS = {"create": "contributor_user_ids", "unlink": "admin_user_ids"}


class ResUsers(models.Model):
    _inherit = "res.users"

    def _project_member_access_domain(self, model_name, operation, grant=False):
        """Return the domain of a record rule of model_name for operation, for this user.

        Without grant, the global rule: outside a Public or Private project, or allowed
        by the user's role. With grant, the rule of internal users: inside such a project
        and allowed, so that no group rule of another module denies it.

        Field paths only, and constant leaves for constant branches: ir.rule caches the
        evaluated domain per user, and check_access_rule evaluates it in filtered_domain.
        On a path through many tasks each leaf means "one of them": such a rule ORs its leaves only.
        """
        self.ensure_one()
        project_path, task_path = ACCESS_PATHS[model_name]

        def on_project(field_name):
            return "%s.%s" % (project_path, field_name) if project_path else field_name

        def on_task(field_name):
            return "%s.%s" % (task_path, field_name) if task_path else field_name

        def held(role_field_name):
            return [(on_project(role_field_name), "in", [self.id])]

        inside = [(on_project("access_mode"), "in", RESTRICTED_ACCESS_MODES)]
        outside = [(on_project("access_mode"), "not in", RESTRICTED_ACCESS_MODES)]
        if project_path:
            outside = expression.OR([[(project_path, "=", False)], outside])
        if task_path and self.env[model_name]._fields[task_path].type == "many2many":
            outside = expression.OR([[(task_path, "=", False)], outside])
        is_project_manager = self.has_group("project.group_project_manager")
        readable = (
            expression.TRUE_DOMAIN
            if is_project_manager
            else expression.OR([[(on_project("access_mode"), "=", "public")], held("member_user_ids")])
        )

        if self._is_system():
            allow = expression.TRUE_DOMAIN
        elif self.share:
            allow = expression.FALSE_DOMAIN
        elif operation == "read":
            allow = readable
        elif model_name == "project.member":
            allow = expression.OR([held("admin_user_ids"), outside if is_project_manager else expression.FALSE_DOMAIN])
        elif model_name in PROJECT_SETTING_MODELS:
            allow = held("admin_user_ids")
        elif model_name == "project.task" and operation in TASK_OPERATION_ROLE_FIELDS:
            allow = expression.AND([readable, held(TASK_OPERATION_ROLE_FIELDS[operation])])
        else:
            own = expression.OR(
                [
                    [(on_task("create_uid"), "=", self.id)],
                    [(on_task("user_ids"), "in", [self.id])],
                    [(on_task("uat_user_ids"), "in", [self.id])],
                ]
            )
            allow = expression.AND([readable, expression.OR([own, held("editor_user_ids")])])

        if model_name == "project.member" and operation != "read":
            # Members of a Standard project are kept by Project Administrators, not by everyone.
            return allow
        if grant:
            return expression.AND([inside, allow])
        return expression.OR([outside, allow])
