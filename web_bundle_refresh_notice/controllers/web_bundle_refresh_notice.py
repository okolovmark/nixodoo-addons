from odoo import http
from odoo.http import request

from odoo.addons.base.models.assetsbundle import AssetsBundle


class BundleRefreshNoticeController(http.Controller):
    @http.route("/web_bundle_refresh_notice/versions", type="json", auth="user")
    def get_bundle_versions(self):
        """The `data-asset-version` of each bundle Odoo tracks for its refresh notice, as a web client page
        rendered now carries it. Asked with the arguments of the page's script `t-call-assets`, so the
        `_get_asset_nodes` overrides (a website forced in the session) apply here too, and outside debug=assets
        the route reads the page's own `_generate_asset_nodes_cache` entry.
        """
        qweb = request.env["ir.qweb"]
        versions = {}
        for bundle_name in AssetsBundle.TRACKED_BUNDLES:
            asset_nodes = qweb._get_asset_nodes(
                bundle_name, css=False, js=True, debug=request.session.debug, media=False
            )
            for _tag, attributes, _content in asset_nodes:
                if attributes.get("data-asset-bundle") == bundle_name:
                    versions[bundle_name] = attributes["data-asset-version"]
        return versions
