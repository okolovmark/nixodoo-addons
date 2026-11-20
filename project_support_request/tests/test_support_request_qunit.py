from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestSupportRequestQunit(HttpCase):
    @classmethod
    def start_browser(cls):
        super().start_browser()
        # A create date read as local time is off by the zone's offset: at UTC both coincide.
        cls.browser._websocket_request("Emulation.setTimezoneOverride", params={"timezoneId": "Asia/Tokyo"})

    def setUp(self):
        super().setUp()
        # On a neutralized database the banner is a body child, which QUnit's leftover check fails.
        self.env.ref("web.neutralize_banner").active = False

    def test_support_request_qunit(self):
        self.browser_js("/web/tests?module=project_support_request", "", "", login="admin", timeout=600)
