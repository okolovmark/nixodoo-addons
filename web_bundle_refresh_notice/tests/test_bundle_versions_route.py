import inspect
import json
from collections import defaultdict
from unittest.mock import patch

from lxml import html

from odoo.tests.common import HttpCase, new_test_user, tagged

from odoo.addons.base.models.assetsbundle import AssetsBundle

VERSIONS_URL = "/web_bundle_refresh_notice/versions"
# Generating a bundle the database has not built yet compiles all of its SCSS.
PAGE_TIMEOUT = 120


@tagged("post_install", "-at_install")
class TestBundleVersionsRoute(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal_user = new_test_user(cls.env, login="bundle_versions_internal", groups="base.group_user")

    def _call_versions_route(self):
        payload = {"id": 0, "jsonrpc": "2.0", "method": "call", "params": {}}
        response = self.url_open(
            VERSIONS_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _get_page_versions(self, page_url):
        """The `data-asset-version` values each tracked bundle's elements carry on the page."""
        response = self.url_open(page_url, timeout=PAGE_TIMEOUT)
        self.assertEqual(response.status_code, 200)
        page_root = html.fromstring(response.content)
        page_versions = defaultdict(set)
        for element in page_root.xpath("//script[@data-asset-bundle] | //link[@data-asset-bundle]"):
            page_versions[element.get("data-asset-bundle")].add(element.get("data-asset-version"))
        return {bundle_name: page_versions[bundle_name] for bundle_name in AssetsBundle.TRACKED_BUNDLES}

    def _assert_route_gives_page_versions(self, route_versions, page_versions):
        self.assertEqual(set(route_versions), set(AssetsBundle.TRACKED_BUNDLES))
        self.assertEqual(page_versions, {bundle_name: {version} for bundle_name, version in route_versions.items()})

    def test_the_versions_are_those_the_web_client_page_carries(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        page_versions = self._get_page_versions("/web")

        route_versions = self._call_versions_route()["result"]

        self._assert_route_gives_page_versions(route_versions, page_versions)

    def test_a_debug_assets_page_carries_the_versions_the_route_gives(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        page_versions = self._get_page_versions("/web?debug=assets")

        route_versions = self._call_versions_route()["result"]

        self._assert_route_gives_page_versions(route_versions, page_versions)

    def test_an_override_of_the_page_asset_nodes_gives_the_route_its_versions(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        actual_versions = self._call_versions_route()["result"]
        model_cls = self.env.registry["ir.qweb"]
        get_asset_nodes = model_cls._get_asset_nodes

        def get_asset_nodes_of_other_versions(qweb, *args, **kwargs):
            # New dicts: the nodes are the cached ones every page renders from.
            return [
                (tag, {**attributes, "data-asset-version": f"{attributes['data-asset-version']}-other"}, content)
                if "data-asset-version" in attributes
                else (tag, attributes, content)
                for tag, attributes, content in get_asset_nodes(qweb, *args, **kwargs)
            ]

        with patch.object(model_cls, "_get_asset_nodes", get_asset_nodes_of_other_versions):
            route_versions = self._call_versions_route()["result"]

        self.assertEqual(set(route_versions), set(AssetsBundle.TRACKED_BUNDLES))
        self.assertEqual(
            route_versions, {bundle_name: f"{version}-other" for bundle_name, version in actual_versions.items()}
        )

    def test_the_route_reads_the_cache_entries_of_the_page_asset_nodes(self):
        model_cls = self.env.registry["ir.qweb"]
        generate_asset_nodes_cache = model_cls._generate_asset_nodes_cache
        cache_signature = inspect.signature(generate_asset_nodes_cache)
        cache_keys = []

        def record_cache_key(qweb, *args, **kwargs):
            # The method's ormcache key: its arguments and the context's template cache keys.
            call_arguments = cache_signature.bind(qweb, *args, **kwargs)
            call_arguments.apply_defaults()
            cache_context = tuple(qweb.env.context.get(key) for key in qweb._get_template_cache_keys())
            cache_keys.append((*list(call_arguments.arguments.items())[1:], cache_context))
            return generate_asset_nodes_cache(qweb, *args, **kwargs)

        self.authenticate(self.internal_user.login, self.internal_user.login)
        with patch.object(model_cls, "_generate_asset_nodes_cache", record_cache_key):
            self.assertEqual(self.url_open("/web", timeout=PAGE_TIMEOUT).status_code, 200)
            page_cache_keys = set(cache_keys)
            cache_keys.clear()
            self._call_versions_route()
        route_cache_keys = set(cache_keys)

        self.assertEqual(len(route_cache_keys), len(AssetsBundle.TRACKED_BUNDLES))
        self.assertEqual(route_cache_keys - page_cache_keys, set(), "cache entries the page was not rendered from")

    def test_a_website_forced_in_the_session_gets_the_versions_of_its_own_page(self):
        if "website" not in self.env:
            self.skipTest("website is not installed")
        website = self.env["website"].create({"name": "Bundle versions website"})
        self.env["ir.asset"].create(
            {
                "name": "Bundle versions website: without the refresh notice",
                "bundle": "web.assets_backend",
                "directive": "remove",
                "path": "web_bundle_refresh_notice/static/src/bundle_refresh_notice_service.js",
                "website_id": website.id,
            }
        )
        website_editor = new_test_user(
            self.env,
            login="bundle_versions_website_editor",
            groups="base.group_user,website.group_website_restricted_editor,website.group_multi_website",
        )
        self.authenticate(website_editor.login, website_editor.login)
        generic_versions = self._call_versions_route()["result"]

        force_response = self.url_open(f"/website/force/{website.id}?path=/web", allow_redirects=False)
        self.assertEqual(force_response.status_code, 303)
        page_versions = self._get_page_versions("/web")
        route_versions = self._call_versions_route()["result"]

        self._assert_route_gives_page_versions(route_versions, page_versions)
        self.assertNotEqual(route_versions, generic_versions, "the forced website's own files give other versions")

    def test_the_client_reads_the_versions_the_route_gives_on_a_real_page(self):
        code = """
            (async () => {
                const { readLoadedBundles } =
                    odoo.__DEBUG__.services["@web_bundle_refresh_notice/bundle_refresh_notice_service"];
                const response = await fetch("%(url)s", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: {} }),
                });
                const { result: serverVersions } = await response.json();
                const trackedBundles = readLoadedBundles(document).filter(({ name }) =>
                    Object.hasOwn(serverVersions, name)
                );
                const loadedNames = new Set(trackedBundles.map(({ name }) => name));
                const isMatching =
                    %(tracked_names)s.every((name) => loadedNames.has(name)) &&
                    trackedBundles.every(({ name, version }) => serverVersions[name] === version);
                if (!isMatching) {
                    throw new Error(
                        `page ${JSON.stringify(trackedBundles)}, server ${JSON.stringify(serverVersions)}`
                    );
                }
                console.log("test successful");
            })()
        """ % {"url": VERSIONS_URL, "tracked_names": json.dumps(AssetsBundle.TRACKED_BUNDLES)}
        self.browser_js("/web", code, "odoo.isReady === true", login=self.internal_user.login, timeout=PAGE_TIMEOUT)

    def test_an_unauthenticated_request_gets_no_versions(self):
        self.authenticate(None, None)

        body = self._call_versions_route()

        self.assertNotIn("result", body)
        self.assertEqual(body["error"]["data"]["name"], "odoo.http.SessionExpiredException")
