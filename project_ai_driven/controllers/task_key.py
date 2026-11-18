from odoo import http
from odoo.http import request
from odoo.tools.sql import escape_psql

RECORD_URL = "/web#id=%s&view_type=form&model=%s&action=%s"


class TaskKeyController(http.Controller):
    @http.route("/projects/<string:key>", type="http", auth="user")
    def open_project(self, key, **kwargs):
        return self._redirect_to_record("project.project", key, "project.open_view_project_all_config")

    @http.route("/tasks/<string:key>", type="http", auth="user")
    def open_task(self, key, **kwargs):
        return self._redirect_to_record("project.task", key, "project.action_view_task")

    def _redirect_to_record(self, model_name, key, action_xmlid):
        """Redirect to the form of the record of key the user can read, or to the form of no record.
        The key matches whatever its case, and as it is: % and _ are no wildcards.
        """
        record = request.env[model_name].search([("key", "=ilike", escape_psql(key))], limit=1)
        action = request.env.ref(action_xmlid)
        return request.redirect(RECORD_URL % (record.id or -1, model_name, action.id), code=301)
