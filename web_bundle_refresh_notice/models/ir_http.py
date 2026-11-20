import re

from odoo import models

from odoo.addons.base.models.assetsbundle import AssetsBundle

# `ir.asset._get_asset_bundle_url`, with the website segment `website` adds to it.
BUNDLE_FILE_URL = re.compile(r"/web/assets/(?:\d+/)?(?P<version>[^/]+)/(?P<file>[^/]+)")
# The `t-call-assets` of a tracked bundle in `web.webclient_bootstrap`: its script, its stylesheet (a dark
# mode page loads `web.assets_web_dark`'s in its place, a bundle that includes this one).
PAGE_ASSET_CALLS = (
    {"css": False, "js": True, "media": False},
    {"css": True, "js": False, "media": "screen"},
)


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    # The web client page only: `session_info` has many callers (Point of Sale, `/web/session/*`, portals)
    # that never run the notice, and a broken bundle would then fail them all.
    def webclient_rendering_context(self):
        rendering_context = super().webclient_rendering_context()
        rendering_context["session_info"]["bundle_versions"] = self._get_bundle_versions()
        return rendering_context

    def _get_bundle_versions(self):
        """`{file: version}` of each file of a bundle Odoo tracks for its refresh notice, as a non-debug web client
        page without a website names it in its URL: Odoo versions a bundle's script and stylesheet apart. A page
        carries it as its tab's baseline and the versions route answers it later, so it leaves the session's debug
        mode and the website the page is rendered for aside: a debug=assets tab and the website editor are compared
        too, and every caller reads the plain page's own `_generate_asset_links_cache` entries. Asked with the
        arguments of the page's `t-call-assets`, so the `_get_asset_nodes` overrides apply here too.
        """
        context = dict(self.env.context)
        # `website`'s web client route sets it on the website editor's page; a route that is no website route, the
        # versions one among them, gets the website as `host_id` instead.
        context.pop("website_id", None)
        qweb = self.env["ir.qweb"].with_context(context)
        versions = {}
        for bundle_name in AssetsBundle.TRACKED_BUNDLES:
            for asset_call in PAGE_ASSET_CALLS:
                for _tag, attributes in qweb._get_asset_nodes(bundle_name, debug=False, **asset_call):
                    file_url = BUNDLE_FILE_URL.fullmatch(attributes.get("src") or attributes.get("href") or "")
                    if file_url:
                        versions[file_url["file"]] = file_url["version"]
        return versions
