from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestBundleRefreshNoticeQunit(HttpCase):
    def setUp(self):
        super().setUp()
        # On a neutralized database the banner is a body child, which QUnit's leftover check fails.
        self.env.ref("web.neutralize_banner").active = False

    def test_bundle_refresh_notice_qunit(self):
        self.browser_js("/web/tests?module=web_bundle_refresh_notice", "", "", login="admin", timeout=600)
