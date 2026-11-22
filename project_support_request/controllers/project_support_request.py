from odoo import Command, _, http
from odoo.http import request

# The English text of the route's own `_()` literal, kept for the tests of dependent modules.
NO_SUPPORT_PROJECT_ERROR = (
    "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo"
    " on the AI-driven project that develops it, or restores the archived one that carries it."
)
TICKET_FIELDS = ("key", "name", "task_type", "stage_id", "priority_level", "create_date", "sequence")


class SupportRequestController(http.Controller):
    def _get_support_project_id(self):
        """The id of the active project flagged Develops This Odoo, else 0; 0 as well for a user
        who is not internal, as only internal users file requests. Sudo: an internal user files
        requests, a reader of that project or not.
        """
        if not request.env.user._is_internal():
            return 0
        support_project = request.env["project.project"].sudo().search([("develops_this_odoo", "=", True)], limit=1)
        return support_project.id or 0

    @http.route("/project_support_request/systray", type="json", auth="user", methods=["POST"])
    def get_systray_requests(self, tab="bugs", limit=5, **kwargs):
        """The user's open requests (UAT user, unfolded stage) of the active tab, "bugs" or
        "others", with the counts of both tabs; `limit` 0 returns them all.
        """
        project_id = self._get_support_project_id()
        if not project_id:
            return {
                "configured": False,
                "bugs_count": 0,
                "others_count": 0,
                "tickets": [],
                "total": 0,
            }

        Task = request.env["project.task"]
        base_domain = [
            ("project_id", "=", project_id),
            ("stage_id.fold", "=", False),
            ("uat_user_ids", "in", [request.env.uid]),
        ]
        bugs_count = Task.search_count(base_domain + [("task_type", "=", "bug")])
        others_count = Task.search_count(base_domain + [("task_type", "!=", "bug")])
        if tab == "bugs":
            tab_domain = base_domain + [("task_type", "=", "bug")]
        else:
            tab_domain = base_domain + [("task_type", "!=", "bug")]
        total = bugs_count if tab == "bugs" else others_count

        tickets_data = Task.search_read(tab_domain, TICKET_FIELDS, order="sequence", limit=limit or None)
        for ticket in tickets_data:
            ticket["stage_name"] = ticket.pop("stage_id")[1]
            ticket["task_type"] = ticket["task_type"] or ""
            ticket["key"] = ticket["key"] or ""
            ticket["create_date"] = str(ticket["create_date"])

        return {
            "configured": True,
            "bugs_count": bugs_count,
            "others_count": others_count,
            "tickets": tickets_data,
            "total": total,
        }

    @http.route("/project_support_request/config", type="json", auth="user", methods=["POST"])
    def get_config(self, **kwargs):
        """The support project, the user and the task types the widget builds its domains and
        its type picker from, labelled in the user's language.
        """
        project_id = self._get_support_project_id()
        task_type_field = request.env["project.task"]._fields["task_type"]
        task_types = [
            {"value": value, "label": label} for value, label in task_type_field._description_selection(request.env)
        ]
        return {
            "project_id": project_id or False,
            "uid": request.env.uid,
            "task_types": task_types,
        }

    @http.route("/project_support_request/create", type="json", auth="user", methods=["POST"])
    def create_request(self, name="", task_type="bug", description="", **kwargs):
        """File a request in the first unfolded stage of the support project, the user as its
        UAT user and nobody assigned. Sudo: the Support button is open to every internal user,
        project rights or not.
        """
        if not request.env.user._is_internal():
            return {"error": _("Only internal users file support requests.")}
        project_id = self._get_support_project_id()
        if not project_id:
            return {
                "error": _(
                    "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo"
                    " on the AI-driven project that develops it, or restores the archived one that carries it."
                )
            }

        name = (name or "").strip()
        if not name:
            return {"error": _("Support request title is required.")}

        if task_type not in request.env["project.task"]._fields["task_type"].get_values(request.env):
            return {"error": _("Invalid task type: %s", task_type)}

        first_stage = (
            request.env["project.task.type"]
            .sudo()
            .search(
                [("project_ids", "in", [project_id]), ("fold", "=", False)],
                order="sequence",
                limit=1,
            )
        )
        vals = {
            "name": name,
            "project_id": project_id,
            "task_type": task_type,
            "description": description or False,
            "uat_user_ids": [Command.link(request.env.uid)],
            # sudo keeps env.uid, so the task's default assignee would be the filer
            "user_ids": [Command.clear()],
        }
        if first_stage:
            vals["stage_id"] = first_stage.id

        task = request.env["project.task"].sudo().create(vals)
        return {
            "ticket_id": task.id,
            "ticket_key": task.key or "",
        }
