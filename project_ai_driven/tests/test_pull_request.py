from contextlib import contextmanager
from unittest.mock import patch

from lxml import etree
from psycopg2 import IntegrityError

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import Form, tagged
from odoo.tools import mute_logger

from ..models.project_pull_request import parse_pull_request_links, parse_single_pull_request_link
from .common import ProjectAiDrivenCommon

URL = "https://github.com/owner/repo/pull/12"
LINKS_WITHOUT_A_REPOSITORY = (
    "https://example.com/pull/1",
    "README.md/pull/3",
    "https://gitlab.com/-/merge_requests/7",
)
# ten digits overflow an integer column; the last two are digits of another script
LINKS_WITHOUT_AN_INTEGER_NUMBER = (
    "https://github.com/owner/repo/pull/1234567890",
    "https://github.com/owner/repo/pull/١٢",
    "https://github.com/owner/repo/pull/12٣",
)
# text: (canonical url, repository, number)
LINK_SHAPES = {
    URL: (URL, "owner/repo", 12),
    "https://gitlab.com/group/project/merge_requests/7": (
        "https://gitlab.com/group/project/merge_requests/7",
        "group/project",
        7,
    ),
    "https://gitlab.com/group/subgroup/project/-/merge_requests/7": (
        "https://gitlab.com/group/subgroup/project/-/merge_requests/7",
        "group/subgroup/project",
        7,
    ),
    "https://bitbucket.org/workspace/repo/pull-requests/3": (
        "https://bitbucket.org/workspace/repo/pull-requests/3",
        "workspace/repo",
        3,
    ),
    "http://git.example.com:3000/owner/repo/pulls/4": (
        "http://git.example.com:3000/owner/repo/pulls/4",
        "owner/repo",
        4,
    ),
    "HTTPS://GitHub.com/Owner/Repo/pull/12/": ("https://github.com/Owner/Repo/pull/12", "Owner/Repo", 12),
    URL + "/files": (URL, "owner/repo", 12),
    URL + "/commits/0123abcd": (URL, "owner/repo", 12),
    URL + "?diff=split#discussion_r1": (URL, "owner/repo", 12),
    URL + "@0123abcd": (URL, "owner/repo", 12),
    "https://github.com/owner/repo/pull/123456789": (
        "https://github.com/owner/repo/pull/123456789",
        "owner/repo",
        123456789,
    ),
}
NOT_PULL_REQUEST_LINKS = (
    "N/A",
    "in pr 12",
    "owner/repo/pull/12",
    "memory://journal/2000-01-01#12",
    "ftp://github.com/owner/repo/pull/12",
    "https://github.com/owner/repo",
    "https://github.com/owner/repo/pulls",
    "https://github.com/owner/repo/pull/new",
    "https://github.com/owner/repo/pull/12abc",
    "https://github.com/owner/repo/commit/0123abcd",
    "https://github.com/owner/repo/tree/a-branch",
    "https://example.com/web#id=12&model=project.task",
    *LINKS_WITHOUT_A_REPOSITORY,
    *LINKS_WITHOUT_AN_INTEGER_NUMBER,
    "",
    False,
)
NOT_ONE_LINK = (
    *NOT_PULL_REQUEST_LINKS[:-2],
    "github.com/owner/repo/pull/12",
    URL + " and some words",
    "see " + URL,
    URL + ", https://github.com/owner/repo/pull/13",
)


@tagged("post_install", "-at_install")
class TestPullRequestLinkParser(ProjectAiDrivenCommon):
    def test_link_shapes(self):
        for text, expected_link in LINK_SHAPES.items():
            links = parse_pull_request_links(text)
            self.assertEqual([link[:3] for link in links], [expected_link], text)
            self.assertEqual(parse_single_pull_request_link(text), links[0], text)

    def test_text_without_a_link(self):
        for text in NOT_PULL_REQUEST_LINKS:
            self.assertEqual(parse_pull_request_links(text), [], text)
            self.assertIsNone(parse_single_pull_request_link(text), text)

    def test_link_without_a_scheme_is_read_as_https(self):
        links = parse_pull_request_links("github.com/owner/repo/pull/12")
        self.assertEqual([link[:3] for link in links], [(URL, "owner/repo", 12)])
        self.assertEqual(links[0].source, "github.com/owner/repo/pull/12")

    def test_several_links_in_reading_order_each_once(self):
        text = "%s/files, https://github.com/owner/repo/pull/3\n%s github.com/owner/other/pull/1" % (URL, URL)
        self.assertEqual(
            [link.url for link in parse_pull_request_links(text)],
            [URL, "https://github.com/owner/repo/pull/3", "https://github.com/owner/other/pull/1"],
        )

    def test_link_inside_text(self):
        for text in ("see %s." % URL, "(%s)" % URL, "\ufeff" + URL, URL + " - first part"):
            self.assertEqual([link.url for link in parse_pull_request_links(text)], [URL], text)

    def test_single_link_ignores_what_cannot_be_seen_around_it(self):
        for text in (" %s\n" % URL, "\ufeff" + URL, URL + "\u200b"):
            self.assertEqual(parse_single_pull_request_link(text).url, URL, repr(text))

    def test_single_link_is_nothing_else(self):
        for text in NOT_ONE_LINK:
            self.assertIsNone(parse_single_pull_request_link(text), text)


@tagged("post_install", "-at_install")
class TestPullRequest(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pull_request = cls.env["project.pull.request"].create({"url": URL})
        cls.other_task = cls.env["project.task"].create({"name": "Other Task", "project_id": cls.project.id})

    @contextmanager
    def _record_calls(self, model_name, method_name, read_call):
        """Yield the list of what read_call returns for the arguments of each call of the method."""
        model_cls = self.env.registry[model_name]
        method = getattr(model_cls, method_name)
        calls = []

        def recorded_method(records, *args, **kwargs):
            calls.append(read_call(*args, **kwargs))
            return method(records, *args, **kwargs)

        with patch.object(model_cls, method_name, recorded_method):
            yield calls

    def _count_find_or_create(self):
        """Yield the urls of the links _find_or_create is called with."""
        return self._record_calls("project.pull.request", "_find_or_create", lambda link: link.url)

    def _record_task_writes(self):
        """Yield the names of the fields of each write of tasks."""
        return self._record_calls("project.task", "write", lambda vals: set(vals))

    def test_url_is_stored_in_canonical_form(self):
        pull_request = self.env["project.pull.request"].create({"url": " HTTPS://GitHub.com/owner/repo/pull/13/files "})
        self.assertRecordValues(
            pull_request,
            [
                {
                    "url": "https://github.com/owner/repo/pull/13",
                    "repository": "owner/repo",
                    "number": 13,
                    "display_name": "owner/repo#13",
                },
            ],
        )
        pull_request.url = "https://gitlab.com/group/project/-/merge_requests/7#note_1"
        self.assertRecordValues(
            pull_request,
            [
                {
                    "url": "https://gitlab.com/group/project/-/merge_requests/7",
                    "repository": "group/project",
                    "number": 7,
                    "display_name": "group/project#7",
                },
            ],
        )

    def test_url_that_is_not_a_pull_request_link_is_refused(self):
        for url in ("https://github.com/owner/repo/commit/0123abcd", "N/A", URL + " " + URL + "/files"):
            with self.assertRefused("is not a pull request link"):
                self.env["project.pull.request"].create({"url": url})
            with self.assertRefused("is not a pull request link"):
                self.pull_request.url = url

    @mute_logger("odoo.sql_db")
    def test_url_is_unique(self):
        with self.assertRaises(IntegrityError):
            self.env["project.pull.request"].create({"url": URL + "/files"})

    def test_new_pull_request_is_open(self):
        created_pull_request = self.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/13"})
        self.assertEqual(created_pull_request.state, "open")
        pull_request_form = Form(self.env["project.pull.request"])
        self.assertEqual(pull_request_form.state, "open")
        pull_request_form.url = "https://github.com/owner/repo/pull/14"
        self.assertEqual(pull_request_form.save().state, "open")

    def test_record_without_a_number_shows_its_url_or_nothing(self):
        PullRequest = self.env["project.pull.request"]
        self.assertFalse(PullRequest.new({}).display_name)
        self.assertEqual(PullRequest.new({"url": "not a link yet"}).display_name, "not a link yet")
        self.assertEqual(PullRequest.new({"url": URL + "/files"}).display_name, "owner/repo#12")
        self.assertFalse(Form(PullRequest).display_name)

    def test_found_by_a_part_of_its_url(self):
        found_pull_requests = self.env["project.pull.request"].name_search("owner/repo/pull/12")
        self.assertEqual(found_pull_requests, [(self.pull_request.id, "owner/repo#12")])

    def test_found_by_the_name_it_shows(self):
        PullRequest = self.env["project.pull.request"]
        same_number_pull_request = PullRequest.create({"url": "https://github.com/owner/other/pull/12"})
        next_pull_request = PullRequest.create({"url": "https://github.com/owner/repo/pull/13"})
        # a database may hold pull requests of its own
        created_here = [("id", "in", (self.pull_request | same_number_pull_request | next_pull_request).ids)]
        shown_name = (self.pull_request.id, "owner/repo#12")

        for name in ("owner/repo#12", " Owner/Repo#12 ", "repo#12"):
            self.assertEqual(PullRequest.name_search(name, args=created_here), [shown_name], name)
        self.assertEqual(
            PullRequest.name_search("owner/#12", args=created_here),
            [shown_name, (same_number_pull_request.id, "owner/other#12")],
        )
        self.assertEqual(PullRequest.name_search("owner/repo#12", args=created_here, operator="="), [shown_name])
        self.assertFalse(PullRequest.name_search("repo#12", args=created_here, operator="="))
        self.assertFalse(PullRequest.name_search("owner/repo#1", args=created_here))
        self.assertFalse(PullRequest.name_search("owner/repo#12", args=[("id", "=", next_pull_request.id)]))

    def test_negative_operator_excludes_the_name_it_shows(self):
        PullRequest = self.env["project.pull.request"]
        same_number_pull_request = PullRequest.create({"url": "https://github.com/owner/other/pull/12"})
        next_pull_request = PullRequest.create({"url": "https://github.com/owner/repo/pull/13"})
        created_here = [("id", "in", (self.pull_request | same_number_pull_request | next_pull_request).ids)]
        shown_name = (self.pull_request.id, "owner/repo#12")
        other_names = [(same_number_pull_request.id, "owner/other#12"), (next_pull_request.id, "owner/repo#13")]

        for operator in ("not ilike", "not like", "!="):
            self.assertEqual(
                PullRequest.name_search("owner/repo#12", args=created_here, operator=operator), other_names, operator
            )
        self.assertEqual(PullRequest.name_search("repo#12", args=created_here, operator="not ilike"), other_names)
        self.assertEqual(
            PullRequest.name_search("repo#12", args=created_here, operator="!="), [shown_name, *other_names]
        )

    def test_list_of_urls_and_shown_names(self):
        PullRequest = self.env["project.pull.request"]
        url_pull_request = PullRequest.create({"url": "https://github.com/owner/repo/pull/13"})
        other_pull_request = PullRequest.create({"url": "https://github.com/owner/other/pull/12"})
        created_here = [("id", "in", (self.pull_request | url_pull_request | other_pull_request).ids)]
        names = [url_pull_request.url, "owner/repo#12", "not a link"]

        self.assertEqual(
            PullRequest.name_search(names, args=created_here, operator="in"),
            [(self.pull_request.id, "owner/repo#12"), (url_pull_request.id, "owner/repo#13")],
        )
        self.assertEqual(
            PullRequest.name_search(names, args=created_here, operator="not in"),
            [(other_pull_request.id, "owner/other#12")],
        )

    def test_tuple_one_name_and_empty_list_with_in_and_not_in(self):
        PullRequest = self.env["project.pull.request"]
        url_pull_request = PullRequest.create({"url": "https://github.com/owner/repo/pull/13"})
        other_pull_request = PullRequest.create({"url": "https://github.com/owner/other/pull/12"})
        created_here = [("id", "in", (self.pull_request | url_pull_request | other_pull_request).ids)]
        shown_name = (self.pull_request.id, "owner/repo#12")
        url_name = (url_pull_request.id, "owner/repo#13")
        other_name = (other_pull_request.id, "owner/other#12")
        # (names, found with in, found with not in)
        cases = (
            (("owner/repo#12", url_pull_request.url), [shown_name, url_name], [other_name]),
            ("owner/repo#12", [shown_name], [url_name, other_name]),
            (url_pull_request.url, [url_name], [shown_name, other_name]),
            ([], [], [shown_name, url_name, other_name]),
        )

        for names, found_with_in, found_with_not_in in cases:
            with self.subTest(names=names):
                self.assertEqual(PullRequest.name_search(names, args=created_here, operator="in"), found_with_in)
                self.assertEqual(
                    PullRequest.name_search(names, args=created_here, operator="not in"), found_with_not_in
                )
                self.assertEqual(
                    list(PullRequest._name_search(names, args=created_here, operator="in")),
                    [pull_request_id for pull_request_id, __ in found_with_in],
                )

    def test_task_found_by_the_name_its_pull_request_shows(self):
        task_domain = [("id", "=", self.task.id), ("pull_request_ids", "ilike", "owner/repo#12")]
        self.assertFalse(self.env["project.task"].search(task_domain))
        self.task.pull_request_ids = self.pull_request
        self.assertEqual(self.env["project.task"].search(task_domain), self.task)

    def test_task_excluded_by_the_name_its_pull_request_shows(self):
        self.task.pull_request_ids = self.pull_request
        tasks_here = [("id", "in", (self.task | self.other_task).ids)]
        Task = self.env["project.task"]
        # core passes the positive operator to the name search of a many2many and negates its result
        for operator in ("not ilike", "!="):
            excluding_domain = [*tasks_here, ("pull_request_ids", operator, "owner/repo#12")]
            self.assertEqual(Task.search(excluding_domain), self.other_task, operator)
        self.assertEqual(Task.search([*tasks_here, ("pull_request_ids", "in", "owner/repo#12")]), self.task)
        self.assertEqual(Task.search([*tasks_here, ("pull_request_ids", "not in", "owner/repo#12")]), self.other_task)

    def test_pr_uri_reads_the_pull_request_created_first(self):
        self.assertFalse(self.task.pr_uri)
        later_pull_request = self.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/13"})
        self.task.pull_request_ids = later_pull_request
        self.assertEqual(self.task.pr_uri, later_pull_request.url)
        self.task.pull_request_ids = [Command.link(self.pull_request.id)]
        self.assertEqual(self.task.pr_uri, URL)
        self.task.pull_request_ids = [Command.unlink(self.pull_request.id)]
        self.assertEqual(self.task.pr_uri, later_pull_request.url)
        self.task.pull_request_ids = [Command.clear()]
        self.assertFalse(self.task.pr_uri)

    def test_pr_uri_of_a_task_open_in_a_form(self):
        later_pull_request = self.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/13"})
        self.task.pull_request_ids = later_pull_request | self.pull_request
        edited_task = self.env["project.task"].new(origin=self.task)
        edited_task.pull_request_ids = [Command.unlink(self.pull_request.id), Command.link(self.pull_request.id)]
        self.assertEqual(
            edited_task.pull_request_ids._origin.ids,
            [later_pull_request.id, self.pull_request.id],
            "linked last, created first",
        )
        self.assertEqual(edited_task.pr_uri, URL)

    def test_pr_uri_follows_the_url_of_the_pull_request(self):
        self.task.pull_request_ids = self.pull_request
        self.pull_request.url = "https://github.com/owner/repo/pull/99"
        self.assertEqual(self.task.pr_uri, "https://github.com/owner/repo/pull/99")

    def test_writing_a_new_link_creates_an_open_pull_request(self):
        self.task.pr_uri = "https://github.com/owner/repo/pull/13/files"
        self.assertRecordValues(
            self.task.pull_request_ids,
            [{"url": "https://github.com/owner/repo/pull/13", "state": "open", "task_ids": self.task.ids}],
        )
        self.assertEqual(self.task.pr_uri, "https://github.com/owner/repo/pull/13")

    def test_writing_a_known_link_links_its_pull_request(self):
        self.pull_request.state = "merged"
        pull_request_count = self.env["project.pull.request"].search_count([])

        self.task.pr_uri = URL + "/"

        self.assertEqual(self.task.pull_request_ids, self.pull_request)
        self.assertEqual(self.pull_request.state, "merged")
        self.assertEqual(self.env["project.pull.request"].search_count([]), pull_request_count)

    def test_writing_a_link_already_on_the_task_changes_nothing(self):
        self.task.pr_uri = URL
        self.task.pr_uri = URL
        self.assertEqual(self.task.pull_request_ids, self.pull_request)
        self.env.cr.execute("SELECT count(*) FROM project_pull_request_task_rel WHERE task_id = %s", (self.task.id,))
        self.assertEqual(self.env.cr.fetchone(), (1,))

    def test_writing_a_second_link_keeps_the_first(self):
        self.task.pr_uri = URL
        self.task.pr_uri = "https://github.com/owner/repo/pull/13"
        self.assertEqual(
            self.task.pull_request_ids.mapped("url"),
            [URL, "https://github.com/owner/repo/pull/13"],
        )
        self.assertEqual(self.task.pr_uri, URL)

    def test_writing_what_is_not_one_link_is_refused(self):
        for text in NOT_ONE_LINK:
            with self.assertRefused("is not a pull request link", "Data, Configuration or No change"):
                self.task.pr_uri = text
            with self.assertRefused("is not a pull request link"):
                self.env["project.task"].create({"name": "Task", "project_id": self.project.id, "pr_uri": text})
        self.assertFalse(self.task.pull_request_ids)

    def test_writing_an_empty_value_changes_nothing(self):
        self.task.pr_uri = URL
        for empty_value in (False, ""):
            self.task.pr_uri = empty_value
            self.assertEqual(self.task.pull_request_ids, self.pull_request)
            self.assertEqual(self.task.pr_uri, URL)
        self.other_task.pr_uri = False
        self.assertFalse(self.other_task.pull_request_ids)

    def test_same_link_on_two_tasks_shares_one_pull_request(self):
        (self.task | self.other_task).write({"pr_uri": URL})
        created_task = self.env["project.task"].create(
            {"name": "Created Task", "project_id": self.project.id, "pr_uri": URL},
        )
        self.assertEqual(self.pull_request.task_ids, self.task | self.other_task | created_task)
        self.assertEqual(self.env["project.pull.request"].search_count([("url", "=", URL)]), 1)

    def test_one_link_written_on_many_tasks_is_looked_up_once(self):
        tasks = self.env["project.task"].create(
            [{"name": "Task %s" % number, "project_id": self.project.id} for number in range(10)]
        )
        with self._count_find_or_create() as looked_up_urls:
            tasks.write({"pr_uri": "https://github.com/owner/repo/pull/13"})
        self.assertEqual(looked_up_urls, ["https://github.com/owner/repo/pull/13"])
        self.assertEqual(len(tasks.pull_request_ids), 1)
        self.assertEqual(tasks.pull_request_ids.task_ids, tasks)

    def test_two_links_on_two_groups_of_tasks_are_looked_up_once_each(self):
        new_url = "https://github.com/owner/repo/pull/13"
        with self._count_find_or_create() as looked_up_urls:
            tasks = self.env["project.task"].create(
                [
                    {"name": "Task %s" % number, "project_id": self.project.id, "pr_uri": url}
                    for url in (URL, new_url)
                    for number in range(3)
                ]
            )
        self.assertEqual(looked_up_urls, [URL, new_url])
        self.assertEqual(self.pull_request.task_ids, tasks[:3])
        self.assertRecordValues(tasks[3:].pull_request_ids, [{"url": new_url}])
        self.assertEqual(tasks[3:].pull_request_ids.task_ids, tasks[3:])

    def test_spellings_of_one_link_are_looked_up_once(self):
        spellings = (URL, URL + "/files", "HTTPS://GitHub.com/owner/repo/pull/12/")
        with self._count_find_or_create() as looked_up_urls:
            tasks = self.env["project.task"].create(
                [
                    {"name": "Task %s" % number, "project_id": self.project.id, "pr_uri": spelling}
                    for number, spelling in enumerate(spellings)
                ]
            )
        self.assertEqual(looked_up_urls, [URL])
        self.assertEqual(self.pull_request.task_ids, tasks)

    def test_batch_with_one_text_that_is_not_a_link_looks_up_nothing(self):
        vals_list = [
            {"name": "Linked Task", "project_id": self.project.id, "pr_uri": URL},
            {"name": "Unlinked Task", "project_id": self.project.id, "pr_uri": "see the ticket"},
        ]
        with self._count_find_or_create() as looked_up_urls:
            with self.assertRefused('"see the ticket" is not a pull request link') as refusal:
                self.env["project.task"].create(vals_list)
        self.assertNotIn(URL, refusal.exception.args[0])
        self.assertEqual(looked_up_urls, [])
        self.assertFalse(self.pull_request.task_ids)

    def test_link_is_written_only_on_the_tasks_without_it(self):
        self.task.pull_request_ids = self.pull_request
        with self._record_task_writes() as written_fields:
            self.task.pr_uri = URL + "/files"
        self.assertEqual([field_names for field_names in written_fields if "pull_request_ids" in field_names], [])

        with self._record_task_writes() as written_fields:
            (self.task | self.other_task).write({"pr_uri": URL})
        self.assertEqual(len([field_names for field_names in written_fields if "pull_request_ids" in field_names]), 1)
        self.assertEqual(self.pull_request.task_ids, self.task | self.other_task)

    def test_pr_uri_is_stored_and_searchable(self):
        self.task.pr_uri = URL + "/files"
        self.env.flush_all()
        self.env.cr.execute("SELECT pr_uri FROM project_task WHERE id = %s", (self.task.id,))
        self.assertEqual(self.env.cr.fetchone(), (URL,))
        self.assertEqual(self.env["project.task"].search([("pr_uri", "=", URL)]), self.task)
        self.assertEqual(self.env["project.task"].search([("pr_uri", "ilike", "owner/repo/pull/12")]), self.task)
        self.assertNotIn(self.task, self.env["project.task"].search([("pr_uri", "=", False)]))

    def test_pr_uri_over_rpc_as_a_project_user(self):
        task = self.task.with_user(self.user_dev)
        task.write({"pr_uri": "https://github.com/owner/repo/pull/13"})
        self.assertEqual(task.read(["pr_uri"]), [{"id": task.id, "pr_uri": "https://github.com/owner/repo/pull/13"}])
        self.assertEqual(task.pull_request_ids.create_uid, self.user_dev)
        self.assertFalse(task.fields_get(["pr_uri"], ["readonly"])["pr_uri"]["readonly"])

    def test_pr_uri_is_tracked(self):
        self._flush_tracking()
        self.task.pr_uri = URL + "/files"
        self._flush_tracking()
        tracking_values = self.task.message_ids.tracking_value_ids.filtered(lambda value: value.field.name == "pr_uri")
        self.assertRecordValues(tracking_values, [{"old_value_char": False, "new_value_char": URL}])

    def test_copied_task_starts_without_pull_requests(self):
        self.task.write({"pr_uri": URL, "resolution": "code"})
        self.env.cr.execute("UPDATE project_task SET pr_legacy = 'old value' WHERE id = %s", (self.task.id,))
        self.task.invalidate_recordset(["pr_legacy"])
        copied_task = self.task.copy()
        self.assertRecordValues(
            copied_task,
            [{"pull_request_ids": [], "pr_uri": False, "resolution": False, "pr_legacy": False}],
        )

    def test_task_form_lists_the_pull_requests(self):
        self.task.pull_request_ids = self.pull_request
        with Form(self.task) as task_form:
            self.assertEqual(task_form.pull_request_ids[:], self.pull_request)
            task_form.pull_request_ids.remove(id=self.pull_request.id)
        self.assertFalse(self.task.pull_request_ids)

    def test_phone_gets_cards_where_the_desktop_gets_a_list(self):
        """Below 768px the web client opens the first kanban of an action, and
        the inline kanban of a field that has one.
        """
        PullRequest = self.env["project.pull.request"]
        kanban_view = self.env.ref("project_ai_driven.project_pull_request_view_kanban")
        self.assertEqual(self.env.ref("project_ai_driven.project_pull_request_action").view_mode, "tree,kanban,form")
        self.assertEqual(PullRequest.get_view(view_type="kanban")["id"], kanban_view.id)

        form_arch = etree.fromstring(PullRequest.get_view(view_type="form")["arch"])
        self.assertEqual(len(form_arch.xpath("//field[@name='task_ids']/kanban")), 1)
        self.assertEqual(len(form_arch.xpath("//field[@name='task_ids']/tree")), 1)
        task_form_arch = etree.fromstring(self.env["project.task"].get_view(view_type="form")["arch"])
        # no kanban of its own: the mode is what makes the phone take the one of the model
        pull_requests_field = task_form_arch.xpath("//field[@name='pull_request_ids']")[0]
        self.assertEqual(
            (pull_requests_field.get("mode"), len(pull_requests_field.xpath("kanban"))),
            ("tree,kanban", 0),
        )
        self.assertEqual(task_form_arch.xpath("//field[@name='pr_legacy']/@class"), ["text-break"])

    def test_group_by_filter_does_not_share_its_name_with_a_field(self):
        search_arch = etree.fromstring(self.env["project.pull.request"].get_view(view_type="search")["arch"])
        field_names = set(search_arch.xpath("//field/@name"))
        self.assertIn("repository", field_names)
        self.assertFalse(field_names & set(search_arch.xpath("//filter/@name")))
        self.assertEqual(
            search_arch.xpath("//filter[@name='group_by_repository']/@context"),
            ["{'group_by': 'repository'}"],
        )


@tagged("post_install", "-at_install")
class TestPullRequestAccess(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pull_request = cls.env["project.pull.request"].create({"url": URL})
        cls.user_internal = cls._create_user("Internal User", "project_ai_driven_internal", "internal@example.com")
        cls.user_portal = (
            cls.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Portal User",
                    "login": "project_ai_driven_portal",
                    "email": "portal@example.com",
                    "groups_id": [Command.set(cls.env.ref("base.group_portal").ids)],
                },
            )
        )

    def test_internal_user_reads_only(self):
        pull_request = self.pull_request.with_user(self.user_internal)
        self.assertEqual(pull_request.read(["url"]), [{"id": pull_request.id, "url": URL}])
        with self.assertRaises(AccessError):
            pull_request.state = "merged"
        with self.assertRaises(AccessError):
            pull_request.create({"url": "https://github.com/owner/repo/pull/13"})
        with self.assertRaises(AccessError):
            pull_request.unlink()

    def test_portal_user_has_no_access(self):
        pull_request = self.pull_request.with_user(self.user_portal)
        with self.assertRaises(AccessError):
            pull_request.read(["url"])
        with self.assertRaises(AccessError):
            pull_request.search([])
        with self.assertRaises(AccessError):
            pull_request.create({"url": "https://github.com/owner/repo/pull/13"})

    def test_project_user_creates_and_writes(self):
        pull_requests = self.env["project.pull.request"].with_user(self.user_dev)
        pull_request = pull_requests.create({"url": "https://github.com/owner/repo/pull/13"})
        pull_request.write({"state": "merged", "merge_date": "2000-01-01 12:00:00"})
        self.assertEqual(pull_request.read(["state"]), [{"id": pull_request.id, "state": "merged"}])
        with self.assertRaises(AccessError):
            pull_request.unlink()
