from odoo import models
from odoo.fields import Domain

from .project_member import RESTRICTED_ACCESS_MODES

# The path from a record of each model to its project, "" for the project itself.
PROJECT_PATHS = {
    "project.project": "",
    "project.member": "project_id",
    "project.task": "project_id",
    "project.milestone": "project_id",
    "project.update": "project_id",
    "project.task.burndown.chart.report": "project_id",
}
# Changing them is a project setting.
PROJECT_SETTING_MODELS = ("project.project", "project.milestone", "project.update")
# The role a task operation other than read and write needs.
TASK_OPERATION_ROLE_FIELDS = {"create": "contributor_user_ids", "unlink": "admin_user_ids"}


class ResUsers(models.Model):
    _inherit = "res.users"

    def _project_member_access_domain(self, model_name, operation, grant=False):
        """Return the domain of an access row of model_name for operation, for this user.

        Without grant, the domain of a restriction: outside a Public or Private project,
        or allowed by the user's role. With grant, the domain of a permission: inside
        such a project and allowed, where no permission of core opens what the mode does.

        Field paths only, no ids read from the data: _access_domain caches the domain per
        user. A pull request is read through any of its tasks.
        """
        self.ensure_one()
        if model_name == "project.pull.request":
            readable_tasks = self._project_member_access_domain("project.task", "read")
            return Domain("task_ids", "=", False) | Domain("task_ids", "any", readable_tasks)
        project_path = PROJECT_PATHS[model_name]

        def on_project(field_name):
            return f"{project_path}.{field_name}" if project_path else field_name

        def held(role_field_name):
            return Domain(on_project(role_field_name), "in", [self.id])

        inside = Domain(on_project("access_mode"), "in", RESTRICTED_ACCESS_MODES)
        outside = Domain(on_project("access_mode"), "not in", RESTRICTED_ACCESS_MODES)
        if project_path:
            outside |= Domain(project_path, "=", False)
        is_project_manager = self._has_group("project.group_project_manager")
        if model_name == "project.task":
            readable = Domain("project_id", "access", "read")
        elif is_project_manager:
            readable = Domain.TRUE
        else:
            readable = Domain(on_project("access_mode"), "=", "public") | held("member_user_ids")

        if self._is_system():
            allow = Domain.TRUE
        elif self.share:
            allow = Domain.FALSE
        elif operation == "read":
            allow = readable
        elif model_name == "project.member" and operation == "create" and is_project_manager:
            # The rows of the first Admins are inserted once the project is Public or Private:
            # project.member create checks the Admins the project had before them.
            allow = Domain.TRUE
        elif model_name == "project.member":
            allow = held("admin_user_ids") | (outside if is_project_manager else Domain.FALSE)
        elif model_name in PROJECT_SETTING_MODELS:
            allow = held("admin_user_ids")
        elif model_name == "project.task" and operation in TASK_OPERATION_ROLE_FIELDS:
            allow = held(TASK_OPERATION_ROLE_FIELDS[operation])
        else:
            own_task = (
                Domain("create_uid", "=", self.id)
                | Domain("user_ids", "in", [self.id])
                | Domain("uat_user_ids", "in", [self.id])
            )
            allow = readable & (own_task | held("editor_user_ids"))

        if model_name == "project.member" and operation != "read":
            # Members of a Standard project are kept by Project Administrators, not by everyone.
            return allow
        if grant:
            return inside & allow
        return outside | allow
