import importlib.util
from pathlib import Path

from odoo.tests.common import tagged

from ..hooks import (
    LINK_IN_TEXT,
    LINK_WITHOUT_SCHEME,
    MEMORY_REFERENCE,
    ONE_LINK,
    OTHER_LINK,
    OTHER_TEXT,
    SEVERAL_LINKS,
    convert_old_pr_uri_values,
    post_init_hook,
)
from .common import ProjectAiDrivenCommon

URL = "https://github.com/owner/repo/pull/12"
OTHER_URL = "https://github.com/owner/repo/pull/13"
MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.1.0" / "post-migration.py"
CONVERTED_STATE_QUERIES = (
    "SELECT id, pr_uri, pr_legacy, resolution, write_date FROM project_task ORDER BY id",
    "SELECT id, url, repository, number, state FROM project_pull_request ORDER BY id",
    "SELECT task_id, pull_request_id FROM project_pull_request_task_rel ORDER BY task_id, pull_request_id",
)


@tagged("post_install", "-at_install")
class TestOldPrUriValues(ProjectAiDrivenCommon):
    """The column of pr_uri is put back by SQL in the state the module
    project_task_pull_request left it in: any text.
    """

    def _create_task_with_old_value(self, old_value, **values):
        task = self.env["project.task"].create({"name": "Old Task", "project_id": self.project.id, **values})
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET pr_uri = %s WHERE id = %s", (old_value, task.id))
        task.invalidate_recordset(["pr_uri"])
        return task

    def _converted_state(self):
        self.env.flush_all()
        state = []
        for query in CONVERTED_STATE_QUERIES:
            self.env.cr.execute(query)
            state.append(self.env.cr.fetchall())
        return state

    def assertKeptAsText(self, old_values, kind):
        tasks = self.env["project.task"].concat(*map(self._create_task_with_old_value, old_values))
        pull_request_count = self.env["project.pull.request"].search_count([])

        counts = convert_old_pr_uri_values(self.env)

        self.assertEqual(counts[kind], len(old_values))
        self.assertEqual(sum(counts.values()), len(old_values))
        self.assertEqual(self.env["project.pull.request"].search_count([]), pull_request_count)
        self.assertRecordValues(
            tasks,
            [
                {"pr_legacy": old_value, "pr_uri": False, "resolution": False, "pull_request_ids": []}
                for old_value in old_values
            ],
        )

    def test_one_pull_request_link(self):
        task = self._create_task_with_old_value(URL)
        closing_slash_task = self._create_task_with_old_value(" %s/ " % OTHER_URL)

        counts = convert_old_pr_uri_values(self.env)

        self.assertEqual(counts[ONE_LINK], 2)
        self.assertRecordValues(
            task | closing_slash_task,
            [
                {"pr_uri": URL, "pr_legacy": False, "resolution": "code"},
                {"pr_uri": OTHER_URL, "pr_legacy": False, "resolution": "code"},
            ],
        )
        self.assertRecordValues(
            task.pull_request_ids,
            [{"url": URL, "repository": "owner/repo", "number": 12, "state": False, "task_ids": task.ids}],
        )
        self.assertEqual(closing_slash_task.pull_request_ids.url, OTHER_URL)

    def test_one_link_with_an_upper_case_scheme_or_host(self):
        old_values = ("HTTPS://github.com/owner/repo/pull/12", " Https://GitHub.COM/owner/repo/pull/13/ ")
        tasks = self.env["project.task"].concat(*map(self._create_task_with_old_value, old_values))

        counts = convert_old_pr_uri_values(self.env)

        self.assertEqual(counts[ONE_LINK], len(old_values))
        self.assertEqual(sum(counts.values()), len(old_values))
        self.assertRecordValues(
            tasks,
            [
                {"pr_uri": URL, "pr_legacy": False, "resolution": "code"},
                {"pr_uri": OTHER_URL, "pr_legacy": False, "resolution": "code"},
            ],
        )
        self.assertEqual(tasks.pull_request_ids.mapped("state"), [False, False])

    def test_several_pull_request_links(self):
        old_value = "%s/changes,  %s https://gitlab.com/group/project/-/merge_requests/7" % (URL, OTHER_URL)
        task = self._create_task_with_old_value(old_value)

        counts = convert_old_pr_uri_values(self.env)

        self.assertEqual(counts[SEVERAL_LINKS], 1)
        self.assertRecordValues(task, [{"pr_uri": URL, "pr_legacy": old_value, "resolution": "code"}])
        self.assertEqual(
            task.pull_request_ids.mapped("url"),
            [URL, OTHER_URL, "https://gitlab.com/group/project/-/merge_requests/7"],
        )
        self.assertEqual(task.pull_request_ids.mapped("state"), [False, False, False])

    def test_pull_request_link_without_a_scheme(self):
        task = self._create_task_with_old_value("github.com/owner/repo/pull/12")

        counts = convert_old_pr_uri_values(self.env)

        self.assertEqual(counts[LINK_WITHOUT_SCHEME], 1)
        self.assertRecordValues(
            task,
            [{"pr_uri": URL, "pr_legacy": "github.com/owner/repo/pull/12", "resolution": "code"}],
        )
        self.assertRecordValues(task.pull_request_ids, [{"url": URL, "state": False}])

    def test_pull_request_link_with_something_before_or_after_it(self):
        old_values = (
            URL + "/files",
            URL + "/changes",
            URL + "/commits/0123456789abcdef0123456789abcdef01234567",
            URL + "@0123abcd",
            URL + " - first part delivered",
            URL + "?diff=split#discussion_r1",
            "\ufeff" + URL,
        )
        tasks = self.env["project.task"].concat(*map(self._create_task_with_old_value, old_values))

        counts = convert_old_pr_uri_values(self.env)

        self.assertEqual(counts[LINK_IN_TEXT], len(old_values))
        self.assertRecordValues(
            tasks,
            [{"pr_uri": URL, "pr_legacy": old_value, "resolution": "code"} for old_value in old_values],
        )
        self.assertRecordValues(tasks.pull_request_ids, [{"url": URL, "state": False, "task_ids": tasks.ids}])

    def test_link_that_is_not_a_pull_request(self):
        self.assertKeptAsText(
            (
                "https://github.com/owner/repo/commit/0123456789abcdef0123456789abcdef01234567",
                "https://github.com/owner/repo/tree/a-branch",
                "https://github.com/owner/repo",
                "https://example.com/web#id=12&model=project.task&view_type=form",
                "https://example.com/pull/1",
                "https://github.com/owner/repo/pull/1234567890",
            ),
            OTHER_LINK,
        )

    def test_memory_reference(self):
        self.assertKeptAsText(("memory://journal/2000-01-01#12", " memory://journal/2000-01-02#3\n"), MEMORY_REFERENCE)

    def test_other_text(self):
        self.assertKeptAsText(
            ("N/A", "None", "description", "in pr 12", "see the parent task", "-", "README.md/pull/3"),
            OTHER_TEXT,
        )

    def test_empty_value(self):
        tasks = self._create_task_with_old_value(None) | self._create_task_with_old_value("")
        state = self._converted_state()

        counts = convert_old_pr_uri_values(self.env)

        self.assertFalse(any(counts.values()))
        self.assertEqual(self._converted_state(), state)
        self.assertRecordValues(tasks, [{"pr_legacy": False, "resolution": False, "pull_request_ids": []}] * 2)

    def test_resolution_already_chosen_is_kept(self):
        task = self._create_task_with_old_value(URL, resolution="data")
        convert_old_pr_uri_values(self.env)
        self.assertRecordValues(task, [{"pr_uri": URL, "resolution": "data"}])
        self.assertEqual(task.pull_request_ids.url, URL)

    def test_known_pull_request_is_linked_as_it_is(self):
        pull_request = self.env["project.pull.request"].create({"url": URL, "state": "merged"})
        task = self._create_task_with_old_value(URL + "/files")

        convert_old_pr_uri_values(self.env)

        self.assertEqual(task.pull_request_ids, pull_request)
        self.assertEqual(pull_request.state, "merged")

    def test_same_link_on_two_tasks_shares_one_pull_request(self):
        tasks = self._create_task_with_old_value(URL) | self._create_task_with_old_value(URL + "/files")
        convert_old_pr_uri_values(self.env)
        self.assertEqual(self.env["project.pull.request"].search([("url", "=", URL)]).task_ids, tasks)

    def test_archived_task(self):
        task = self._create_task_with_old_value(URL, active=False)
        text_task = self._create_task_with_old_value("N/A", active=False)

        convert_old_pr_uri_values(self.env)

        self.assertRecordValues(
            task | text_task,
            [
                {"pr_uri": URL, "pr_legacy": False, "resolution": "code"},
                {"pr_uri": False, "pr_legacy": "N/A", "resolution": False},
            ],
        )
        self.assertEqual(task.pull_request_ids.url, URL)

    def test_second_run_changes_nothing(self):
        for old_value in (URL, URL + "/files", "%s %s" % (OTHER_URL, URL), "github.com/owner/repo/pull/14", "N/A"):
            self._create_task_with_old_value(old_value)
        convert_old_pr_uri_values(self.env)
        state = self._converted_state()

        counts = convert_old_pr_uri_values(self.env)

        self.assertFalse(any(counts.values()))
        self.assertEqual(self._converted_state(), state)

    def test_link_written_after_the_conversion_is_left_alone(self):
        self.task.pr_uri = URL
        state = self._converted_state()

        counts = convert_old_pr_uri_values(self.env)

        self.assertFalse(any(counts.values()))
        self.assertEqual(self._converted_state(), state)
        self.assertFalse(self.task.resolution)

    def test_old_value_found_later_joins_the_one_kept(self):
        task = self._create_task_with_old_value("N/A")
        convert_old_pr_uri_values(self.env)
        self.env.cr.execute("UPDATE project_task SET pr_uri = 'in the description' WHERE id = %s", (task.id,))

        convert_old_pr_uri_values(self.env)

        self.assertRecordValues(task, [{"pr_uri": False, "pr_legacy": "N/A\nin the description"}])

    def test_post_init_hook_converts(self):
        task = self._create_task_with_old_value(URL + "/files")
        post_init_hook(self.env.cr, self.env.registry)
        self.assertRecordValues(task, [{"pr_uri": URL, "pr_legacy": URL + "/files", "resolution": "code"}])

    def test_migration_script_converts_on_update_only(self):
        spec = importlib.util.spec_from_file_location("post_migration", MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        task = self._create_task_with_old_value(URL + "/files")

        script.migrate(self.env.cr, None)
        self.assertRecordValues(task, [{"pr_uri": URL + "/files", "pr_legacy": False, "resolution": False}])

        script.migrate(self.env.cr, "16.0.2.0.0")
        self.assertRecordValues(task, [{"pr_uri": URL, "pr_legacy": URL + "/files", "resolution": "code"}])
