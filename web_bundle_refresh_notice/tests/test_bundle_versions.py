import inspect
import json
import re
from collections import defaultdict
from unittest.mock import patch

from lxml import html

from odoo.http.session import session_store
from odoo.tests.common import HttpCase, JsonRpcException, new_test_user, tagged

from odoo.addons.base.models.assetsbundle import AssetsBundle

VERSIONS_URL = "/web_bundle_refresh_notice/versions"
SESSION_INFO = re.compile(r"odoo\.__session_info__ = (?P<session_info>.*);$", re.MULTILINE)


@tagged("post_install", "-at_install")
class TestBundleVersions(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.internal_user = new_test_user(cls.env, login="bundle_versions_internal", groups="base.group_user")
        cls.tracked_files = {
            f"{bundle_name}.min.{extension}"
            for bundle_name in AssetsBundle.TRACKED_BUNDLES
            for extension in ("js", "css")
        }

    def _open_page(self, page_url):
        """The versions the URLs of the page give each file of a tracked bundle, and the baseline its session
        carries."""
        response = self.url_open(page_url)
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.content)
        url_versions = defaultdict(set)
        for url in page.xpath("//script/@src | //link/@href"):
            if url.startswith("/web/assets/"):
                version, file_name = url.rsplit("/", 2)[1:]
                bundle_name = self.env["ir.asset"]._parse_bundle_name(file_name, version == "debug")[0]
                if bundle_name in AssetsBundle.TRACKED_BUNDLES:
                    url_versions[file_name].add(version)
        session_script = page.xpath("string(//script[contains(., 'odoo.__session_info__')])")
        session_info = json.loads(SESSION_INFO.search(session_script)["session_info"])
        return url_versions, session_info.get("bundle_versions")

    def test_the_versions_are_those_the_web_client_page_carries(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        page_versions, page_baseline = self._open_page("/odoo")

        route_versions = self.make_jsonrpc_request(VERSIONS_URL)

        self.assertEqual(
            set(route_versions), self.tracked_files, "the script and the stylesheet of each tracked bundle"
        )
        self.assertEqual(page_versions, {file_name: {version} for file_name, version in route_versions.items()})
        self.assertEqual(page_baseline, route_versions, "the page's session carries what the route answers")

    def test_the_session_info_of_another_caller_carries_no_versions(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)

        session_info = self.make_jsonrpc_request("/web/session/get_session_info")

        self.assertEqual(session_info["uid"], self.internal_user.id)
        self.assertNotIn("bundle_versions", session_info)

    def test_the_versions_are_asked_without_the_website_of_the_context(self):
        asked_contexts = []

        def record_context(qweb, *args, **kwargs):
            asked_contexts.append(qweb.env.context)
            return []

        with patch.object(self.env.registry["ir.qweb"], "_get_asset_nodes", record_context):
            self.env["ir.http"].with_context(website_id=1)._get_bundle_versions()

        self.assertTrue(asked_contexts)
        self.assertEqual([context for context in asked_contexts if "website_id" in context], [])

    def test_a_session_in_debug_assets_gets_the_versions_of_a_normal_session(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        normal_versions = self.make_jsonrpc_request(VERSIONS_URL)

        debug_page_versions, debug_page_baseline = self._open_page("/odoo?debug=assets")
        self.assertEqual(session_store().get(self.session.sid).debug, "assets")
        self.assertEqual(set().union(*debug_page_versions.values()), {"debug"}, "the page loads unversioned files")
        debug_versions = self.make_jsonrpc_request(VERSIONS_URL)

        self.assertEqual(set(debug_versions), self.tracked_files)
        self.assertEqual(debug_versions, normal_versions)
        self.assertEqual(debug_page_baseline, normal_versions)

    def test_an_override_of_the_page_asset_nodes_gives_the_session_and_the_route_their_versions(self):
        self.authenticate(self.internal_user.login, self.internal_user.login)
        actual_versions = self.make_jsonrpc_request(VERSIONS_URL)
        model_cls = self.env.registry["ir.qweb"]
        get_asset_nodes = model_cls._get_asset_nodes

        def get_other_version_url(url):
            url_start, version, file_name = url.rsplit("/", 2)
            return f"{url_start}/{version}-other/{file_name}"

        def get_asset_nodes_of_other_versions(qweb, *args, **kwargs):
            return [
                (
                    tag,
                    {
                        name: get_other_version_url(value) if name in ("src", "href") else value
                        for name, value in attributes.items()
                    },
                )
                for tag, attributes in get_asset_nodes(qweb, *args, **kwargs)
            ]

        with patch.object(model_cls, "_get_asset_nodes", get_asset_nodes_of_other_versions):
            route_versions = self.make_jsonrpc_request(VERSIONS_URL)
            _page_versions, page_baseline = self._open_page("/odoo")

        other_versions = {file_name: f"{version}-other" for file_name, version in actual_versions.items()}
        self.assertEqual(set(route_versions), self.tracked_files)
        self.assertEqual(route_versions, other_versions)
        self.assertEqual(page_baseline, other_versions)

    def test_the_baseline_and_the_route_read_the_cache_entries_of_the_page_asset_links(self):
        qweb_cls = self.env.registry["ir.qweb"]
        http_cls = self.env.registry["ir.http"]
        generate_asset_links_cache = qweb_cls._generate_asset_links_cache
        webclient_rendering_context = http_cls.webclient_rendering_context
        cache_signature = inspect.signature(generate_asset_links_cache)
        cache_keys = []
        baseline_cache_keys = []
        call_depth = 0
        is_in_rendering_context = False

        def record_cache_key(qweb, *args, **kwargs):
            nonlocal call_depth
            # The callers' own calls only: generating a stylesheet's links asks for its fonts' links too.
            if not call_depth:
                # The method's ormcache key: its arguments, the assets params as sorted items.
                call_arguments = cache_signature.bind(qweb, *args, **kwargs)
                call_arguments.apply_defaults()
                (baseline_cache_keys if is_in_rendering_context else cache_keys).append(
                    tuple(
                        (name, tuple(sorted(value.items())) if name == "assets_params" else value)
                        for name, value in list(call_arguments.arguments.items())[1:]
                    )
                )
            call_depth += 1
            try:
                return generate_asset_links_cache(qweb, *args, **kwargs)
            finally:
                call_depth -= 1

        def record_rendering_context(ir_http):
            nonlocal is_in_rendering_context
            is_in_rendering_context = True
            try:
                return webclient_rendering_context(ir_http)
            finally:
                is_in_rendering_context = False

        self.authenticate(self.internal_user.login, self.internal_user.login)
        with (
            patch.object(qweb_cls, "_generate_asset_links_cache", record_cache_key),
            patch.object(http_cls, "webclient_rendering_context", record_rendering_context),
        ):
            self.assertEqual(self.url_open("/odoo").status_code, 200)
            page_cache_keys = set(cache_keys)
            cache_keys.clear()
            self.make_jsonrpc_request(VERSIONS_URL)
        route_cache_keys = set(cache_keys)

        for caller, caller_cache_keys in (("baseline", set(baseline_cache_keys)), ("route", route_cache_keys)):
            with self.subTest(caller=caller):
                self.assertEqual(len(caller_cache_keys), len(self.tracked_files), "one entry per tracked file")
                self.assertEqual(caller_cache_keys - page_cache_keys, set(), "entries the page was not rendered from")

    def test_a_session_on_the_website_editor_gets_the_versions_of_a_plain_session(self):
        if self.env["ir.module.module"]._get("website").state != "installed":
            self.skipTest("website is not installed")
        website = self.env["website"].create({"name": "Bundle versions website"})
        stylesheet_path = next(
            path
            for path, *_details in self.env["ir.asset"]._get_asset_paths("web.assets_web", {})
            if path.endswith(".scss")
        )
        self.env["ir.asset"].create(
            {
                "name": "Bundle versions website: a stylesheet of its own",
                "bundle": "web.assets_web",
                "directive": "remove",
                "path": stylesheet_path,
                "website_id": website.id,
            }
        )
        website_editor = new_test_user(
            self.env,
            login="bundle_versions_website_editor",
            groups="base.group_user,website.group_website_restricted_editor,website.group_multi_website",
        )
        self.authenticate(website_editor.login, website_editor.login)
        plain_versions = self.make_jsonrpc_request(VERSIONS_URL)

        self.authenticate(website_editor.login, website_editor.login)
        editor_page_versions, editor_page_baseline = self._open_page(
            f"/website/force/{website.id}?path=/odoo/action-website.website_preview"
        )
        editor_versions = self.make_jsonrpc_request(VERSIONS_URL)

        self.assertNotEqual(
            editor_page_versions["web.assets_web.min.css"],
            {plain_versions["web.assets_web.min.css"]},
            "the website editor page loads the stylesheet of its website",
        )
        self.assertEqual(set(editor_versions), self.tracked_files)
        self.assertEqual(editor_versions, plain_versions)
        self.assertEqual(editor_page_baseline, plain_versions)

    def test_a_session_awaiting_an_identity_check_still_gets_the_versions(self):
        self.env["ir.config_parameter"].set_bool("base.session_check_device", True)
        # Another device already in the session makes the test's own an untrusted one.
        self.authenticate(
            self.internal_user.login,
            self.internal_user.login,
            session_extra={"_devices": {"another device": {"trusted": True}}},
        )
        with self.assertRaises(JsonRpcException) as caught:
            self.make_jsonrpc_request("/web/session/get_session_info")
        self.assertEqual(str(caught.exception), "odoo.http.session.CheckIdentityException")

        self.assertEqual(set(self.make_jsonrpc_request(VERSIONS_URL)), self.tracked_files)

    def test_an_unauthenticated_request_gets_no_versions(self):
        self.authenticate(None, None)

        with self.assertRaises(JsonRpcException) as caught:
            self.make_jsonrpc_request(VERSIONS_URL)

        self.assertEqual(caught.exception.code, 100)
        self.assertEqual(str(caught.exception), "odoo.http.session.SessionExpiredException")
