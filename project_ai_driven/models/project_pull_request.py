import re
from collections.abc import Set as AbstractSet
from typing import NamedTuple

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain


class PullRequestLink(NamedTuple):
    url: str  # canonical form
    repository: str
    number: int
    source: str  # as written in the text


# Without a scheme only a dotted host name counts, or any "word/pull/1" would be a link.
PULL_REQUEST_LINK = re.compile(
    r"""
    (?:
        (?P<scheme>(?i:https?))://(?P<host>[^\s/?#@]+)
        |
        (?<![\w.@/:-])(?P<bare_host>(?i:(?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?)
    )
    (?P<path>
        (?P<repository>(?:/[^\s/?#]+)+?)(?<!/-)  # a "/-" before the keyword separates, it names nothing
        (?:/-)?
        /(?:pull|merge_requests|pull-requests|pulls)
        /(?P<number>[0-9]{1,9})(?!\w)  # what an integer column takes; \d reads other scripts too
    )
    """,
    re.VERBOSE,
)
# What a link pasted from a page or a file brings along unseen: spaces, U+FEFF, U+200B.
UNSEEN_AROUND_LINK = re.compile(r"^[\s\ufeff\u200b]+|[\s\ufeff\u200b]+$")
# The name a record shows, which its url never matches: a url holds no "#".
SHOWN_NAME = re.compile(r"\s*(?P<repository>[^\s#]+)#(?P<number>[0-9]{1,9})\s*")


def parse_pull_request_links(text):
    """Return the pull request links text holds, each one once, in reading
    order. A link written without a scheme is read as https.
    """
    links = {}
    for match in PULL_REQUEST_LINK.finditer(text or ""):
        scheme = (match["scheme"] or "https").lower()
        host = (match["host"] or match["bare_host"]).lower()
        url = "%s://%s%s" % (scheme, host, match["path"])
        links.setdefault(
            url,
            PullRequestLink(url, match["repository"].strip("/"), int(match["number"]), match[0]),
        )
    return list(links.values())


def parse_single_pull_request_link(text):
    """Return the link when text is one http(s) url of a pull request and
    nothing else, None for any other text.
    """
    links = parse_pull_request_links(text)
    link_text = UNSEEN_AROUND_LINK.sub("", text or "")
    if len(links) != 1 or "://" not in links[0].source:
        return None
    if not link_text.startswith(links[0].source) or re.search(r"\s", link_text):
        return None
    return links[0]


class ProjectPullRequest(models.Model):
    _name = "project.pull.request"
    _description = "Pull Request"
    _rec_name = "url"

    url = fields.Char(string="URL", required=True)
    repository = fields.Char(compute="_compute_repository", store=True)
    number = fields.Integer(compute="_compute_repository", store=True)
    state = fields.Selection([("open", "Open"), ("merged", "Merged"), ("closed", "Closed")], default="open")
    merge_date = fields.Datetime()
    deploy_date = fields.Datetime()
    task_ids = fields.Many2many(
        "project.task",
        "project_pull_request_task_rel",
        "pull_request_id",
        "task_id",
        string="Tasks",
    )

    _url_unique = models.Constraint("UNIQUE(url)", "A pull request with this link already exists.")

    @api.depends("url")
    def _compute_repository(self):
        for record in self:
            link = parse_single_pull_request_link(record.url)
            record.repository = link and link.repository
            record.number = link and link.number

    @api.depends("repository", "number", "url")
    def _compute_display_name(self):
        for record in self:
            # no number: a record still being typed in a form, whose url is not a link yet
            record.display_name = "%s#%s" % (record.repository, record.number) if record.number else record.url or ""

    @api.model
    def _search_display_name(self, operator, value):
        shown_name_domain = self._get_shown_name_domain(operator, value)
        if shown_name_domain is None:
            return super()._search_display_name(operator, value)
        return shown_name_domain

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._with_canonical_url(vals) for vals in vals_list])

    def write(self, vals):
        return super().write(self._with_canonical_url(vals))

    @api.model
    def _get_shown_name_domain(self, operator, value):
        """Return the domain of the records shown as value, or None when value is
        not written the way a record is shown, "owner/repo#12".
        """
        if operator in Domain.NEGATIVE_OPERATORS:
            return None
        # "=" reaches a search method as "in", with the names in a collection
        names = value if operator == "in" else [value]
        if not isinstance(names, (list, tuple, AbstractSet)):
            return None
        shown_names = [isinstance(name, str) and SHOWN_NAME.fullmatch(name) for name in names]
        if not shown_names or not all(shown_names):
            return None
        repository_operator = "=" if operator == "in" else operator
        return Domain.OR(
            Domain("repository", repository_operator, shown_name["repository"])
            & Domain("number", "=", int(shown_name["number"]))
            for shown_name in shown_names
        )

    @api.model
    def _with_canonical_url(self, vals):
        if not vals.get("url"):
            return vals
        link = parse_single_pull_request_link(vals["url"])
        if not link:
            raise ValidationError(self.env._('"%s" is not a pull request link.', vals["url"]))
        return dict(vals, url=link.url)

    @api.model
    def _find_or_create(self, link):
        return self.search([("url", "=", link.url)]) or self.create({"url": link.url})
