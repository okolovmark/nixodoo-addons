from odoo import http
from odoo.http import request


class BundleRefreshNoticeController(http.Controller):
    # A background call: on a session awaiting an identity check, core's `rpc` patch would open the lock
    # dialog for it, and its answer, in the URLs of every web client page too, is not confidential.
    @http.route("/web_bundle_refresh_notice/versions", type="jsonrpc", auth="user", readonly=True, check_identity=False)
    def get_bundle_versions(self):
        """The bundle versions a web client page rendered now carries as its baseline (`session.bundle_versions`)."""
        return request.env["ir.http"]._get_bundle_versions()
