import re

from odoo import http
from odoo.http import request

from odoo.addons.base.models.assetsbundle import AssetsBundle

# `ir.asset._get_asset_bundle_url`, with the website segment `website` adds to it.
BUNDLE_FILE_URL = re.compile(r"/web/assets/(?:\d+/)?(?P<version>[^/]+)/(?P<file>[^/]+)")
# The `t-call-assets` of a tracked bundle in `web.webclient_bootstrap`: its script, its stylesheet (a dark
# mode page loads `web.assets_web_dark`'s in its place, a bundle that includes this one).
PAGE_ASSET_CALLS = (
    {"css": False, "js": True, "media": False},
    {"css": True, "js": False, "media": "screen"},
)


class BundleRefreshNoticeController(http.Controller):
    # A background call: on a session awaiting an identity check, core's `rpc` patch would open the lock
    # dialog for it, and its answer, in the URLs of every web client page too, is not confidential.
    @http.route("/web_bundle_refresh_notice/versions", type="jsonrpc", auth="user", readonly=True, check_identity=False)
    def get_bundle_versions(self):
        """`{file: version}` of each file of a bundle Odoo tracks for its refresh notice, as a non-debug web client
        page rendered now names it in its URL: Odoo versions a bundle's script and stylesheet apart. The client
        compares the route's answers with each other, so they leave the session's debug mode and website aside: a
        debug=assets tab is compared too, and the route reads the page's own `_generate_asset_links_cache` entries.
        Asked with the arguments of the page's `t-call-assets`, so the `_get_asset_nodes` overrides apply here too.
        """
        qweb = request.env["ir.qweb"]
        versions = {}
        for bundle_name in AssetsBundle.TRACKED_BUNDLES:
            for asset_call in PAGE_ASSET_CALLS:
                for _tag, attributes in qweb._get_asset_nodes(bundle_name, debug=False, **asset_call):
                    file_url = BUNDLE_FILE_URL.fullmatch(attributes.get("src") or attributes.get("href") or "")
                    if file_url:
                        versions[file_url["file"]] = file_url["version"]
        return versions
