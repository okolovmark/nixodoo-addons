from odoo.tests import HttpCase, tagged

VIS_TIMELINE_FILES = (
    "/project_team_dashboard/static/lib/vis-timeline/vis-timeline-graph2d.js",
    "/project_team_dashboard/static/lib/vis-timeline/vis-timeline-graph2d.css",
)


@tagged("post_install", "-at_install")
class TestGanttQunit(HttpCase):
    @classmethod
    def start_browser(cls):
        super().start_browser()
        # The Gantt converts between stored UTC and the browser's time zone: at UTC both coincide.
        cls.browser._websocket_request("Emulation.setTimezoneOverride", params={"timezoneId": "Asia/Tokyo"})

    def setUp(self):
        super().setUp()
        self.env.ref("web.neutralize_banner").active = False

    def test_gantt_qunit(self):
        self.browser_js(
            "/web/tests?module=project_team_dashboard",
            "",
            "",
            login="admin",
            timeout=600,
        )

    def test_the_gantt_library_is_served_from_this_module(self):
        for path in VIS_TIMELINE_FILES:
            self.assertEqual(self.url_open(path).status_code, 200, path)
