import base64
import functools
import hashlib
import json
import logging
import re
from itertools import chain

from markupsafe import escape
from psycopg2 import errors as pg_errors

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools import file_open

from odoo.addons.base.models.assetsbundle import rjsmin

from ..utils.report_spec import ReportSpecError
from .ai_assistant_exchange import APP_ACTION_TAG, ASSISTANT_GROUP
from .ai_assistant_tools import AiAccessError, AiModelNotInstalledError, AiToolError, json_value

_logger = logging.getLogger(__name__)

APP_LIST_LIMIT = 200
ADMIN_GROUP = "base.group_system"
# A filter or a sort on these would tell their values; the code itself reads an answer's report_ids as superuser.
UNFILTERED_FIELDS = ("spec", "shared_user_ids", "company_id", "company_ids", "conversation_id", "exchange_id")
CHART_JS_PATH = "web/static/lib/Chart/Chart.js"
RENDERER_PATH = "ai_assistant/static/report_frame/report_renderer.js"
REPORT_DATA_ID = "ai-report-data"
EMPTY_BINDINGS = {"values": [], "tables": [], "charts": []}
# Both frames of a page: only Chart.js and the renderer run, by hash, nothing loads from outside, and
# the shell's default-src also stops the page navigating its own frame away, which the sandbox allows.
REPORT_CSP = (
    "default-src 'none'; script-src %s; style-src 'unsafe-inline'; img-src data: blob:; font-src data:;"
    " base-uri 'none'; form-action 'none'"
)
PAGE_BASE_STYLE = (
    "*,*::before,*::after{box-sizing:border-box}"
    "body{margin:0;padding:16px;font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;"
    "font-size:14px;line-height:1.5}"
    "canvas,img,svg,table{max-width:100%}"
    ".report-table-wrap{overflow-x:auto}"
    ".report-table{border-collapse:collapse;width:100%}"
    ".report-table th,.report-table td{padding:4px 8px;border-bottom:1px solid #dee2e6;text-align:left}"
    ".report-table .num{text-align:right;font-variant-numeric:tabular-nums}"
    ".report-table-note{margin:4px 0 0;font-size:12px;color:#6c757d}"
    ".report-partial{font-size:.75em;color:#6c757d}"
    ".report-chart{position:relative;height:320px}"
)
SHELL_STYLE = "html,body,iframe{margin:0;border:0;width:100%;height:100%;display:block}"


def inline_script(source):
    """Script text as a frame inlines it and hashes it: the HTML parser turns every CR into LF before either."""
    return source.replace("\r\n", "\n").replace("\r", "\n").replace("</script", "<\\/script")


def script_hash(source):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(source.encode()).digest()).decode()


@functools.lru_cache(maxsize=1)
def chart_js_source():
    """Chart.js as a page inlines it: minified as Odoo minifies its bundles, its licence header kept."""
    with file_open(CHART_JS_PATH) as chart_file:
        source = chart_file.read()
    licence = source[: source.index("*/") + 2]
    return inline_script("%s\n%s" % (licence, rjsmin(source)))


@functools.lru_cache(maxsize=1)
def renderer_source():
    """The renderer that fills a page's bindings, as a frame inlines it."""
    with file_open(RENDERER_PATH) as renderer_file:
        return inline_script(renderer_file.read())


@functools.lru_cache(maxsize=1)
def report_csp():
    """REPORT_CSP with the hashes of the only two scripts a report frame runs."""
    return REPORT_CSP % " ".join(script_hash(source) for source in (chart_js_source(), renderer_source()))


def script_json(value):
    """JSON to inline in a script element: nothing in it can close the element or the document."""
    text = json.dumps(value, ensure_ascii=False)
    for character, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        text = text.replace(character, escaped)
    return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


class AiReport(models.Model):
    """A report the AI Assistant built in the app: the model wrote the specification and the page, Odoo computed
    the data as the owner. A snapshot: Refresh recomputes the data, never the page and its conclusions.

    A colleague it is shared with gets the report itself: its title, owner,
    dates, page and data. Where it came from, whom it is shared with, its
    specification and companies carry ADMIN_GROUP; nobody else filters, sorts
    or groups by them. The owner gets whom it is shared with from the app, and
    the AI Assistant reads the specification for them through report_get,
    both after an ownership check.
    """

    _name = "ai.report"
    _description = "AI report"
    _order = "id desc"

    name = fields.Char(string="Title", required=True, readonly=True)
    user_id = fields.Many2one("res.users", string="Owner", required=True, readonly=True, index=True, ondelete="cascade")
    conversation_id = fields.Many2one(
        "ai.conversation",
        string="AI conversation",
        readonly=True,
        index="btree_not_null",
        ondelete="set null",
        groups=ADMIN_GROUP,
    )
    exchange_id = fields.Many2one(
        "ai.assistant.exchange",
        string="AI Assistant answer",
        readonly=True,
        index="btree_not_null",
        ondelete="set null",
        groups=ADMIN_GROUP,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Current company",
        readonly=True,
        groups=ADMIN_GROUP,
        help="The company that was current when the report was built: its queries run with it as the current one,"
        " as long as the owner still works in it.",
    )
    company_ids = fields.Many2many(
        "res.company",
        string="Companies",
        readonly=True,
        groups=ADMIN_GROUP,
        help="The companies selected when the report was built: it reads in these, as far as the owner still may.",
    )
    spec = fields.Text(
        string="Specification",
        readonly=True,
        groups=ADMIN_GROUP,
        help="The datasets, formulas and merges the model wrote, in JSON; Odoo runs them as the owner.",
    )
    data = fields.Text(readonly=True, help="The computed data the page shows, in JSON.")
    data_date = fields.Datetime(required=True, readonly=True)
    conclusions_date = fields.Datetime(
        required=True,
        readonly=True,
        help="The data date the page and its conclusions were written on; a Refresh leaves it.",
    )
    page = fields.Text(
        readonly=True,
        help="The page as the model wrote it, HTML and CSS without scripts. It is only ever shown in a sandboxed"
        " frame.",
    )
    bindings = fields.Text(
        readonly=True,
        help="The page's values, tables and charts, each an element id bound to a dataset, in JSON; the frame's"
        " renderer fills them from the data.",
    )
    shared_user_ids = fields.Many2many(
        "res.users",
        "ai_report_shared_user_rel",
        "report_id",
        "user_id",
        string="Shared with",
        readonly=True,
        groups=ADMIN_GROUP,
        help="They see the owner's snapshot and may export it; only the owner refreshes, shares or deletes it.",
    )
    share_log_ids = fields.One2many("ai.report.share.log", "report_id", string="Share log", readonly=True)
    is_stale = fields.Boolean(
        string="Conclusions older than the data",
        compute="_compute_is_stale",
        help="Refreshed since the model wrote the page: its conclusions read data of an earlier date.",
    )

    @api.depends("data_date", "conclusions_date")
    def _compute_is_stale(self):
        for report in self:
            report.is_stale = report.data_date != report.conclusions_date

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None, **kwargs):
        self._check_hidden_terms(domain, order)
        return super()._search(domain, offset, limit, order, **kwargs)

    @api.model
    def _read_group(self, domain, groupby=(), aggregates=(), having=(), offset=0, limit=None, order=None):
        """Refuse what `_check_hidden_terms` refuses before Odoo groups: it names the groups as superuser."""
        self._check_hidden_terms(domain, order, [*groupby, *aggregates])
        return super()._read_group(domain, groupby, aggregates, having=having, offset=offset, limit=limit, order=order)

    @api.model
    def _read_grouping_sets(self, domain, grouping_sets, aggregates=(), order=None):
        """As `_read_group`: the web client's grouping sets reach the SQL by this path, not that one."""
        self._check_hidden_terms(domain, order, [*chain(*grouping_sets), *aggregates])
        return super()._read_grouping_sets(domain, grouping_sets, aggregates, order)

    def action_refresh(self):
        """Run the specification again as the owner, without the model, and keep its data with the new date.

        The page and its conclusions stay as written. Only the owner
        refreshes, administrators included. It reads in the report's
        companies that the owner still works in, whatever companies the
        switcher selects now, its current company first while the owner
        still works in it. A failure is told as the tools tell the model:
        their own texts as they are, Odoo's by kind only, for they may name
        records read as superuser or quote SQL. A model no longer installed
        is told in plain words: the tools' text for it guides the model.
        """
        self._check_owner()
        # Both read past the res.company rule, which keeps a many2many to the companies the switcher selects.
        report = self.sudo()
        company_ids = ((report.company_id | report.company_ids) & self.user_id.sudo().company_ids).ids
        if not company_ids:
            raise UserError(self.env._("The report reads in companies you no longer work in: it cannot be refreshed."))
        tools = self.env["ai.assistant.engine"]._assistant_tools(self.user_id, company_ids)
        # Raised "from None": an RPC error carries its traceback, which would print the cause's text.
        try:
            with tools._call_cache(), tools._tool_scope():
                data = tools._report_data(json.loads(report.spec))
        except AiModelNotInstalledError as error:
            raise UserError(
                self.env._(
                    "The report could not be refreshed: the model %s it reads is not installed any more.",
                    error.model_name,
                )
            ) from None
        except (AiAccessError, AiToolError, ReportSpecError) as error:
            raise UserError(self.env._("The report could not be refreshed: %s", str(error))) from None
        except AccessError:
            raise UserError(self.env._("The report could not be refreshed: it reads data you may not read.")) from None
        except pg_errors.QueryCanceled:
            raise UserError(
                self.env._("The report could not be refreshed: its queries took longer than the assistant allows.")
            ) from None
        except Exception:  # noqa: BLE001 -- as in the tools: Odoo's own text may name records or quote SQL
            _logger.warning("ai assistant: AI report %s could not be refreshed", self.id, exc_info=True)
            raise UserError(self.env._("The report could not be refreshed because of an unexpected error.")) from None
        self.sudo().write({"data": json.dumps(data, ensure_ascii=False), "data_date": fields.Datetime.now()})
        return self.app_report()

    def action_share(self):
        """The dialog that shares the report with chosen colleagues."""
        self._check_owner()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Share AI report"),
            "res_model": "ai.report.share",
            "views": [(False, "form")],
            "target": "new",
            "context": {"default_report_id": self.id},
        }

    def action_open(self):
        """The report in the AI Assistant app."""
        self.ensure_one()
        return {"type": "ir.actions.client", "tag": APP_ACTION_TAG, "params": {"report_id": self.id}}

    @api.model
    def app_reports(self):
        """The current employee's AI reports and those shared with them, the latest first."""
        self.browse().check_access("read")
        # Searched as superuser for the recipients' many2many, by the domain of the record rule itself.
        reports = self.sudo().search(
            ["|", ("user_id", "=", self.env.uid), ("shared_user_ids", "in", self.env.uid)], limit=APP_LIST_LIMIT
        )
        return [report._app_summary() for report in reports.with_env(self.env)]

    def app_report(self):
        """The report as the app shows it, its page framed by `_frame_html`."""
        self.ensure_one()
        self.check_access("read")
        is_owner = self.user_id == self.env.user
        return {
            **self._app_summary(),
            "conclusions_date": self.conclusions_date,
            "shared_user_names": self._shared_users().mapped("name") if is_owner else [],
            "frame_html": self._frame_html(),
        }

    def _app_summary(self):
        self.ensure_one()
        is_owner = self.user_id == self.env.user
        is_administrator = self.env.user.has_group(ADMIN_GROUP)
        return {
            "id": self.id,
            "name": self.name,
            "owner_name": self.user_id.name,
            "is_owner": is_owner,
            "data_date": self.data_date,
            "is_stale": self.is_stale,
            "shared_count": len(self.sudo().shared_user_ids) if is_owner or is_administrator else 0,
            "can_refresh": is_owner,
            "can_share": is_owner,
            "can_delete": is_owner or is_administrator,
        }

    def _check_owner(self):
        """Fail unless the current user may read the report and owns it."""
        self.ensure_one()
        self.check_access("read")
        if self.user_id != self.env.user:
            raise AccessError(self.env._("Only the owner of an AI report refreshes or shares it."))

    def _shared_users(self):
        """The colleagues the report is shared with, for its owner only."""
        self._check_owner()
        return self.sudo().shared_user_ids.with_env(self.env)

    @api.model
    def _check_hidden_terms(self, domain, order, specs=()):
        """Refuse a filter or a sort on UNFILTERED_FIELDS, and a group or an aggregate (`specs`, as read_group
        takes them) on any field the user may not read, whose groups read_group would name as superuser."""
        if self.env.su or self.env.user.has_group(ADMIN_GROUP):
            return
        names = {condition.field_expr.split(".")[0] for condition in Domain(domain or []).iter_conditions()}
        names |= {term.split()[0].split(".")[0] for term in (order or "").split(",") if term.strip()}
        hidden_names = names & set(UNFILTERED_FIELDS)
        for name in {token for spec in specs for token in re.findall(r"\w+", spec)}:
            field = self._fields.get(name)
            if field and not self.has_field_access(field, "read"):
                hidden_names.add(name)
        if hidden_names:
            raise AccessError(
                self.env._(
                    "Only administrators filter, sort or group AI reports by %s.", ", ".join(sorted(hidden_names))
                )
            )

    def _frame_html(self):
        """The document of the frame the app shows the report in.

        A shell holding the page's own frame, both sandboxed to scripts only
        and under `report_csp()`. The page gets its data as a JSON block, and
        Chart.js and the renderer inline, the only scripts the policy lets
        run: whatever script the page holds is refused, and nothing in the
        frame may reach Odoo or anywhere else.
        """
        self.ensure_one()
        report_data = {
            "title": self.name,
            "lang": (self.env.user.lang or "en_US").replace("_", "-"),
            "data_date": json_value(self.data_date),
            "conclusions_date": json_value(self.conclusions_date),
            "datasets": json.loads(self.data or "{}"),
            "bindings": json.loads(self.bindings or "null") or EMPTY_BINDINGS,
            "notes": self._renderer_notes(),
        }
        csp = report_csp()
        page_document = (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f"<title>{escape(self.name)}</title><style>{PAGE_BASE_STYLE}</style>"
            f'<script type="application/json" id="{REPORT_DATA_ID}">{script_json(report_data)}</script>'
            f"<script>{chart_js_source()}</script><script>{renderer_source()}</script>"
            f"</head><body>{self.page or ''}</body></html>"
        )
        return (
            '<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
            f"<style>{SHELL_STYLE}</style></head><body>"
            f'<iframe sandbox="allow-scripts" title="{escape(self.name)}" srcdoc="{escape(page_document)}"></iframe>'
            "</body></html>"
        )

    def _renderer_notes(self):
        """The notes the renderer writes under a figure, table or chart, in the reader's language: it fills in the
        row counts %(shown)s and %(total)s."""
        return {
            "partial_mark": self.env._("(partial)"),
            "partial_title": self.env._("More rows match than the report's data holds."),
            "table_partial": self.env._("The first %(shown)s rows: more match."),
            "table_cut_partial": self.env._("The first %(shown)s of the first %(total)s rows: more match."),
            "table_cut": self.env._("The first %(shown)s of %(total)s rows."),
            "chart_partial": self.env._("Partial: the first %(shown)s rows; more match."),
        }

    def _share_with(self, users):
        """Share the report with exactly `users`: those added see it, those left out no longer do; each logged."""
        current_users = self._shared_users()
        added_users = users - current_users
        refused_users = added_users.filtered(
            lambda user: user.share or not user.active or user == self.user_id or not user.has_group(ASSISTANT_GROUP)
        )
        if refused_users:
            raise UserError(
                self.env._(
                    "An AI report is shared with colleagues who use the AI Assistant, not with %s.",
                    ", ".join(refused_users.mapped("name")),
                )
            )
        removed_users = current_users - users
        self.sudo().shared_user_ids = [Command.set(users.ids)]
        self.env["ai.report.share.log"].sudo().create(
            [
                {
                    "report_id": self.id,
                    "report_name": self.name,
                    "user_id": self.env.uid,
                    "target_user_id": user.id,
                    "action": action,
                }
                for action, changed_users in (("share", added_users), ("unshare", removed_users))
                for user in changed_users
            ]
        )

    @api.model
    def _create_in_own_cursor(self, values):
        """Create a report in a transaction of its own and return its id, so that it outlives a failure later in
        the answer that saved it."""
        with self.env.registry.cursor() as cr:
            report = self.with_env(self.env(cr=cr, su=True)).create(values)
            return report.id

    @api.model
    def _unlink_in_own_cursor(self, report_ids):
        """Delete reports `_create_in_own_cursor` created, in a transaction of their own as well."""
        with self.env.registry.cursor() as cr:
            self.with_env(self.env(cr=cr, su=True)).browse(report_ids).exists().unlink()


class AiReportShareLog(models.Model):
    """One share or unshare of an AI report: who, with whom, when. It outlives the report, as an audit."""

    _name = "ai.report.share.log"
    _description = "AI report share log"
    _order = "id desc"

    report_id = fields.Many2one("ai.report", string="AI report", readonly=True, index=True)
    report_name = fields.Char(string="AI report title", readonly=True, help="The title, kept when the report goes.")
    user_id = fields.Many2one("res.users", string="By", required=True, readonly=True, ondelete="cascade")
    target_user_id = fields.Many2one("res.users", string="With", required=True, readonly=True, ondelete="cascade")
    action = fields.Selection([("share", "Shared"), ("unshare", "Unshared")], required=True, readonly=True)
