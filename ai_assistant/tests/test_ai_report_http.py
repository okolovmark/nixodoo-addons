import json
import time
from datetime import datetime
from io import BytesIO
from unittest.mock import patch

import openpyxl
from lxml import html as lxml_html

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, new_test_user, tagged
from odoo.tests.common import ChromeBrowser
from odoo.tools import mute_logger
from odoo.tools.translate import code_translations

from .test_ai_report import BINDINGS, HOSTILE_PAGE, PAGE

REPORT_TITLE = "Partners by country"
XLSX_URL = "/report/xlsx/ai_assistant.ai_report_xlsx/%s"
WRITTEN_ON = datetime(2026, 1, 5, 9, 0)
# The test browser fulfils every request outside the test server through fetch_proxy.
PAGE_URL = "https://ai-report.test/page.html"


@tagged("post_install", "-at_install")
class TestAiReportHttp(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = new_test_user(cls.env, login="test_report_owner", name="Olga Owner")
        cls.recipient = new_test_user(cls.env, login="test_report_recipient", name="Rita Recipient")
        cls.stranger = new_test_user(cls.env, login="test_report_stranger_http", name="Sam Stranger")
        cls.administrator = new_test_user(
            cls.env, login="test_report_admin", name="Ada Admin", groups="base.group_user,base.group_system"
        )
        cls.partner = cls.env["res.partner"].create({"name": "AI Report Tour Partner"})
        conversation = cls.env["ai.conversation"].create(
            {"assistant": "app", "user_id": cls.owner.id, "res_model": "res.partner", "res_id": cls.partner.id}
        )
        exchange = cls.env["ai.assistant.exchange"].create(
            {
                "kind": "app",
                "user_id": cls.owner.id,
                "question": "Chart our partners by country",
                "answer": "Your report is saved.",
                "state": "answered",
                "conversation_id": conversation.id,
            }
        )
        spec = {
            "datasets": [
                {
                    "name": "partners",
                    "model": "res.partner",
                    "domain": json.dumps([["id", "=", cls.owner.partner_id.id]]),
                    "groupby": ["country_id"],
                    "measures": ["__count"],
                    "fields": [],
                    "order": "",
                    "limit": 0,
                    "formulas": [],
                }
            ],
            "merges": [],
        }
        data = {"partners": {"columns": ["country_id", "__count"], "rows": [{"country_id": False, "__count": 7}]}}
        cls.report = cls.env["ai.report"].create(
            {
                "name": REPORT_TITLE,
                "user_id": cls.owner.id,
                "conversation_id": conversation.id,
                "exchange_id": exchange.id,
                "company_ids": [Command.set(cls.owner.company_ids.ids)],
                "spec": json.dumps(spec),
                "data": json.dumps(data),
                "data_date": WRITTEN_ON,
                "conclusions_date": WRITTEN_ON,
                "page": '<h1>Partners</h1><p><b id="total"></b> partners have no country.</p>',
                "bindings": json.dumps(
                    {
                        "values": [
                            {
                                "id": "total",
                                "dataset": "partners",
                                "column": "__count",
                                "pick": "first",
                                "match_column": "",
                                "match_value": "",
                                "format": "integer",
                                "decimals": 0,
                            }
                        ],
                        "tables": [],
                        "charts": [],
                    }
                ),
            }
        )
        cls.report.with_user(cls.owner)._share_with(cls.recipient)

    def _rpc(self, model, method, args, kwargs=None):
        response = self.url_open(
            f"/web/dataset/call_kw/{model}/{method}",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "call",
                    "params": {"model": model, "method": method, "args": args, "kwargs": kwargs or {}},
                }
            ),
            headers={"Content-Type": "application/json"},
        )
        return response.json()

    def _error(self, model, method, args, kwargs=None):
        error = self._rpc(model, method, args, kwargs).get("error")
        return error and error["data"]["name"]

    def _xlsx(self):
        return self.url_open(XLSX_URL % self.report.id)

    @mute_logger("odoo.http")
    def test_a_recipient_reads_and_exports_but_never_changes_the_report_over_rpc(self):
        report_id = self.report.id
        self.authenticate(self.recipient.login, self.recipient.login)
        [row] = self._rpc("ai.report", "read", [[report_id], ["name", "data_date"]])["result"]
        self.assertEqual(row["name"], REPORT_TITLE)
        view = self._rpc("ai.report", "app_report", [[report_id]])["result"]
        self.assertEqual((view["is_owner"], view["can_refresh"], view["can_delete"]), (False, False, False))
        self.assertIn("sandbox=", view["frame_html"])
        self.assertEqual(
            [summary["id"] for summary in self._rpc("ai.report", "app_reports", [])["result"]], [report_id]
        )
        share = self._rpc("ai.report.share", "create", [{"report_id": report_id}])["result"]
        for model, method, args in (
            ("ai.report", "action_refresh", [[report_id]]),
            ("ai.report", "action_share", [[report_id]]),
            ("ai.report", "unlink", [[report_id]]),
            ("ai.report", "write", [[report_id], {"name": "Mine now"}]),
            ("ai.report.share", "action_share", [[share]]),
            ("ai.report.share.log", "search_read", [[["report_id", "=", report_id]], ["action"]]),
        ):
            with self.subTest(method=method, model=model):
                result = self._rpc(model, method, args)
                if method == "search_read":
                    self.assertEqual(result["result"], [], "the share log is the owner's")
                else:
                    self.assertEqual(result["error"]["data"]["name"], "odoo.exceptions.AccessError")
        self.env.invalidate_all()
        self.assertEqual(self.report.data_date, WRITTEN_ON)
        self.assertTrue(self.report.exists())

        response = self._xlsx()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        workbook = openpyxl.load_workbook(BytesIO(response.content))
        self.assertEqual([cell.value for cell in next(workbook["partners"].iter_rows(min_row=2))], [None, 7])

    @mute_logger("odoo.http")
    def test_over_rpc_a_recipient_reads_the_report_itself_and_the_owner_its_sharing_too(self):
        report_id = self.report.id
        owner_only = ["conversation_id", "exchange_id", "company_ids", "spec", "shared_user_ids"]
        self.authenticate(self.recipient.login, self.recipient.login)
        for name in owner_only:
            with self.subTest(name=name):
                self.assertEqual(self._error("ai.report", "read", [[report_id], [name]]), "odoo.exceptions.AccessError")
        [row] = self._rpc("ai.report", "read", [[report_id]])["result"]
        self.assertFalse(set(owner_only) & set(row))
        self.assertNotIn("Chart our partners by country", json.dumps(row), "nor the owner's question, by a link's name")
        for method, args, kwargs in (
            ("search_count", [[["spec", "ilike", "partners"]]], {}),
            ("search_count", [[["shared_user_ids", "in", [self.stranger.id]]]], {}),
            ("read_group", [[], ["conversation_id"], ["conversation_id"]], {}),
            ("read_group", [[], ["shared_user_ids"], ["shared_user_ids"]], {}),
        ):
            with self.subTest(method=method, args=args):
                self.assertEqual(self._error("ai.report", method, args, kwargs), "odoo.exceptions.AccessError")
        views = self._rpc("ai.report", "get_views", [], {"views": [[False, "form"], [False, "list"]]})["result"]
        for arch in (views["views"]["form"]["arch"], views["views"]["list"]["arch"]):
            self.assertFalse([name for name in owner_only if 'name="%s"' % name in arch])
        [summary] = self._rpc("ai.report", "app_reports", [])["result"]
        self.assertEqual(summary["shared_count"], 0)

        self.authenticate(self.owner.login, self.owner.login)
        view = self._rpc("ai.report", "app_report", [[report_id]])["result"]
        self.assertEqual((view["shared_user_names"], view["shared_count"]), (["Rita Recipient"], 1))
        defaults = self._rpc(
            "ai.report.share", "default_get", [["user_ids"]], {"context": {"default_report_id": report_id}}
        )["result"]
        self.assertEqual(defaults["user_ids"], [[6, False, self.recipient.ids]], "the share dialog starts from them")

        self.authenticate(self.administrator.login, self.administrator.login)
        [row] = self._rpc("ai.report", "read", [[report_id], owner_only])["result"]
        self.assertEqual(row["shared_user_ids"], self.recipient.ids)
        self.assertEqual(row["conversation_id"][1], "Chart our partners by country")

    @mute_logger("odoo.http")
    def test_a_refresh_error_carries_no_text_of_odoo_s_over_rpc(self):
        tools_cls = type(self.env["ai.assistant.tools"])
        secret = "Document type rule 'AI Secret Rule'; SELECT secret_column FROM res_partner"
        self.authenticate(self.owner.login, self.owner.login)
        with patch.object(tools_cls, "_report_data", side_effect=AccessError(secret)):
            error = self._rpc("ai.report", "action_refresh", [[self.report.id]])["error"]
        self.assertEqual(error["data"]["message"], "The report could not be refreshed: it reads data you may not read.")
        self.assertNotIn("AI Secret Rule", json.dumps(error), "nor in the traceback the error carries")

    @mute_logger("odoo.http")
    def test_a_stranger_reaches_nothing_of_the_report_over_rpc(self):
        report_id = self.report.id
        self.authenticate(self.stranger.login, self.stranger.login)
        for method, args in (
            ("read", [[report_id], ["name"]]),
            ("app_report", [[report_id]]),
            ("action_refresh", [[report_id]]),
            ("unlink", [[report_id]]),
        ):
            with self.subTest(method):
                self.assertEqual(self._error("ai.report", method, args), "odoo.exceptions.AccessError")
        self.assertEqual(self._rpc("ai.report", "search_read", [[["id", "=", report_id]], ["name"]])["result"], [])
        self.assertEqual(self._rpc("ai.report", "app_reports", [])["result"], [])
        response = self._xlsx()
        self.assertEqual(response.status_code, 403, "refused, not failed")
        self.assertFalse(response.content.startswith(b"PK"), "no workbook")

    @mute_logger("odoo.http")
    def test_an_administrator_reads_and_deletes_but_never_refreshes(self):
        report_id = self.report.id
        self.authenticate(self.administrator.login, self.administrator.login)
        self.assertEqual(self._rpc("ai.report", "read", [[report_id], ["name"]])["result"][0]["name"], REPORT_TITLE)
        self.assertEqual(self._error("ai.report", "action_refresh", [[report_id]]), "odoo.exceptions.AccessError")
        logs = self._rpc("ai.report.share.log", "search_read", [[["report_id", "=", report_id]], ["action"]])
        self.assertEqual([log["action"] for log in logs["result"]], ["share"])
        self.assertTrue(self._rpc("ai.report", "unlink", [[report_id]])["result"])
        self.assertFalse(self.report.exists())

    def test_the_owner_refreshes_over_rpc(self):
        self.authenticate(self.owner.login, self.owner.login)
        view = self._rpc("ai.report", "action_refresh", [[self.report.id]])["result"]
        self.assertTrue(view["is_stale"])
        self.env.invalidate_all()
        self.assertGreater(self.report.data_date, WRITTEN_ON)
        self.assertEqual(json.loads(self.report.data)["partners"]["rows"][0]["__count"], 1)

    def test_the_owner_opens_the_report_from_its_conversation_card(self):
        self.start_tour("/odoo", "ai_assistant_report_owner_tour", login=self.owner.login)
        self.env.invalidate_all()
        self.assertGreater(self.report.data_date, WRITTEN_ON, "the tour's Refresh recomputed the data")
        self.assertEqual(self.report.conclusions_date, WRITTEN_ON)

    def test_a_recipient_opens_the_report_with_export_only(self):
        self.start_tour("/odoo", "ai_assistant_report_recipient_tour", login=self.recipient.login)
        self.env.invalidate_all()
        self.assertEqual(self.report.data_date, WRITTEN_ON)

    def test_the_quick_panel_opens_the_report_in_the_app(self):
        self.start_tour(
            f"/odoo/res.partner/{self.partner.id}",
            "ai_assistant_report_panel_tour",
            login=self.owner.login,
        )

    def test_an_administrator_opens_any_report_from_its_form(self):
        self.start_tour(
            f"/odoo/ai.report/{self.report.id}",
            "ai_assistant_report_admin_tour",
            login=self.administrator.login,
        )


@tagged("post_install", "-at_install")
class TestAiReportRenderer(HttpCase):
    """The page's own document in the test browser, outside the sandbox, so the test can read what ran in it."""

    page_document = ""

    def fetch_proxy(self, url):
        if url != PAGE_URL:
            return super().fetch_proxy(url)
        response = self.make_fetch_proxy_response(self.page_document)
        response["responseHeaders"].append({"name": "content-type", "value": "text/html; charset=utf-8"})
        return response

    def _evaluate(self, expression):
        result = self.page_browser._websocket_request(
            "Runtime.evaluate", params={"expression": expression, "returnByValue": True, "awaitPromise": True}
        )
        return result["result"].get("value")

    def _open_page_document(self, report):
        self.page_document = lxml_html.fromstring(report._frame_html()).xpath("//iframe")[0].get("srcdoc")
        self.page_browser = ChromeBrowser(self)
        self.addCleanup(self.page_browser.cleanup.close)
        self.page_browser.navigate_to(PAGE_URL, wait_stop=True)
        deadline = time.time() + 20
        while self._evaluate("document.documentElement.getAttribute('data-report-rendered')") != "1":
            self.assertLess(time.time(), deadline, "the renderer never finished")
            time.sleep(0.2)

    def test_the_renderer_fills_the_bindings_and_nothing_the_model_wrote_runs(self):
        data = {
            "partners": {
                "columns": ["country_id", "__count"],
                "rows": [
                    {"country_id": [21, "Belgium"], "__count": 1200},
                    {"country_id": [75, "France"], "__count": 3},
                ],
                "has_more": True,
            }
        }
        owner = new_test_user(self.env, login="test_report_renderer", lang="en_US")
        report = (
            self.env["ai.report"]
            .with_user(owner)
            .sudo()
            .create(
                {
                    "name": "Partners by country",
                    "user_id": owner.id,
                    "spec": "{}",
                    "data": json.dumps(data),
                    "data_date": datetime(2026, 10, 13, 9, 0),
                    "conclusions_date": datetime(2026, 10, 13, 9, 0),
                    # Stored as written, not through report_save: the frame's policy alone must stop it.
                    "page": HOSTILE_PAGE,
                    "bindings": json.dumps(BINDINGS),
                }
            )
        )
        self._open_page_document(report.with_user(owner))
        state = self._evaluate(
            """JSON.stringify({
                total: document.getElementById("partners-total").textContent,
                header: [...document.querySelectorAll("#countries thead th")].map((cell) => cell.textContent),
                rows: [...document.querySelectorAll("#countries tbody tr")].map((row) => row.innerText.split("\\t")),
                total_row: [...document.querySelectorAll("#countries tfoot th")].map((cell) => cell.textContent),
                note: [...document.querySelectorAll(".report-table-note")].map((note) => note.textContent),
                canvases: document.querySelectorAll("#chart .report-chart canvas").length,
                charts: Object.keys(Chart.instances).length,
                labels: Object.values(Chart.instances)[0].data.labels,
                series: Object.values(Chart.instances)[0].data.datasets[0].data,
                pwned: typeof window.pwned,
                rtc: typeof window.rtc,
            })"""
        )
        self.assertEqual(
            json.loads(state),
            {
                "total": "1,203 (partial)",
                "header": ["Country", "Partners"],
                "rows": [["Belgium", "1,200"], ["France", "3"]],
                "total_row": ["Total", "1,203"],
                "note": ["Partial: the first 2 rows; more match.", "The first 2 rows: more match."],
                "canvases": 1,
                "charts": 1,
                "labels": ["Belgium", "France"],
                "series": [1200, 3],
                "pwned": "undefined",
                "rtc": "undefined",
            },
        )
        self._evaluate('document.getElementById("js-link").click()')
        time.sleep(0.5)
        self.assertEqual(self._evaluate("typeof window.pwned"), "undefined", "a javascript: link does not run")

    def test_the_renderer_notes_what_a_binding_s_limit_leaves_out(self):
        data = {
            "partners": {
                "columns": ["country_id", "__count"],
                "rows": [
                    {"country_id": [21, "Belgium"], "__count": 1200},
                    {"country_id": [75, "France"], "__count": 3},
                ],
                "has_more": False,
            }
        }
        bindings = {**BINDINGS, "tables": [{**BINDINGS["tables"][0], "limit": 1}]}
        owner = new_test_user(self.env, login="test_report_renderer_limit", lang="en_US")
        report = (
            self.env["ai.report"]
            .sudo()
            .create(
                {
                    "name": "Partners by country",
                    "user_id": owner.id,
                    "spec": "{}",
                    "data": json.dumps(data),
                    "data_date": datetime(2026, 10, 13, 9, 0),
                    "conclusions_date": datetime(2026, 10, 13, 9, 0),
                    "page": PAGE,
                    "bindings": json.dumps(bindings),
                }
            )
        )
        self._open_page_document(report.with_user(owner))
        state = self._evaluate(
            """JSON.stringify({
                total: document.getElementById("partners-total").textContent,
                rows: document.querySelectorAll("#countries tbody tr").length,
                total_row: [...document.querySelectorAll("#countries tfoot th")].map((cell) => cell.textContent),
                note: [...document.querySelectorAll(".report-table-note")].map((note) => note.textContent),
            })"""
        )
        self.assertEqual(
            json.loads(state),
            {"total": "1,203", "rows": 1, "total_row": ["Total", "1,200"], "note": ["The first 1 of 2 rows."]},
        )

    def test_the_renderer_writes_its_notes_in_the_reader_s_language(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        data = {
            "partners": {
                "columns": ["country_id", "__count"],
                "rows": [
                    {"country_id": [21, "Belgium"], "__count": 1200},
                    {"country_id": [75, "France"], "__count": 3},
                ],
                "has_more": True,
            }
        }
        bindings = {**BINDINGS, "tables": [{**BINDINGS["tables"][0], "limit": 1}]}
        owner = new_test_user(self.env, login="test_report_renderer_fr", lang="fr_FR")
        report = (
            self.env["ai.report"]
            .sudo()
            .create(
                {
                    "name": "Partenaires par pays",
                    "user_id": owner.id,
                    "spec": "{}",
                    "data": json.dumps(data),
                    "data_date": datetime(2026, 10, 13, 9, 0),
                    "conclusions_date": datetime(2026, 10, 13, 9, 0),
                    "page": PAGE,
                    "bindings": json.dumps(bindings),
                }
            )
        )
        french = {
            "(partial)": "(partiel)",
            "More rows match than the report's data holds.": "D'autres lignes correspondent.",
            "The first %(shown)s of the first %(total)s rows: more match.": (
                "Les %(shown)s premières des %(total)s premières lignes : il y en a d'autres."
            ),
            "Partial: the first %(shown)s rows; more match.": "Partiel : les %(shown)s premières lignes.",
        }
        with patch.object(code_translations, "get_python_translations", return_value=french):
            self._open_page_document(report.with_user(owner).with_context(lang="fr_FR"))
        state = self._evaluate(
            """JSON.stringify({
                mark: document.querySelector("#partners-total .report-partial").textContent,
                title: document.querySelector("#partners-total .report-partial").title,
                note: [...document.querySelectorAll(".report-table-note")].map((note) => note.textContent),
            })"""
        )
        self.assertEqual(
            json.loads(state),
            {
                "mark": " (partiel)",
                "title": "D'autres lignes correspondent.",
                "note": [
                    "Partiel : les 2 premières lignes.",
                    "Les 1 premières des 2 premières lignes : il y en a d'autres.",
                ],
            },
        )
