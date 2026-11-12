from unittest import SkipTest

from odoo import Command
from odoo.tests.common import Form, tagged

from .common import PULL_REQUEST_MODULE, ProjectAiDrivenCommon

RESOLUTIONS_WITHOUT_CODE = ("data", "configuration", "no_change")


@tagged("post_install", "-at_install")
class TestClosingRule(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._skip_next_to_pull_request_module()
        cls.stage_progress = cls._create_stage("In Progress", 1)
        cls.stage_review = cls._create_stage("Review", 2)
        cls.stage_done = cls._create_stage("Done", 3)
        cls.project.pr_required_states = cls.stage_done
        cls.task.stage_id = cls.stage_progress
        cls.pull_request = cls.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/1"})

    @classmethod
    def _create_stage(cls, name, sequence, project=None):
        return cls.env["project.task.type"].create(
            {"name": name, "sequence": sequence, "project_ids": [Command.link((project or cls.project).id)]},
        )

    def _create_task(self, **values):
        return self.env["project.task"].create(
            {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_progress.id, **values},
        )

    def test_no_resolution_and_no_pull_request_is_refused(self):
        with self.assertRefused("Choose a Resolution", self.task.name):
            self.task.stage_id = self.stage_done
        self.assertEqual(self.task.stage_id, self.stage_progress)

    def test_no_resolution_with_a_pull_request_becomes_code(self):
        self.task.pull_request_ids = self.pull_request
        self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "resolution": "code"}])

    def test_code_without_a_pull_request_is_refused(self):
        self.task.resolution = "code"
        with self.assertRefused("Add a pull request"):
            self.task.stage_id = self.stage_done

    def test_code_with_a_pull_request_passes(self):
        self.task.write({"resolution": "code", "pull_request_ids": [Command.link(self.pull_request.id)]})
        self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "resolution": "code"}])

    def test_other_resolutions_pass_without_a_pull_request(self):
        for resolution in RESOLUTIONS_WITHOUT_CODE:
            task = self._create_task(resolution=resolution)
            task.stage_id = self.stage_done
            self.assertRecordValues(task, [{"stage_id": self.stage_done.id, "resolution": resolution}])

    def test_other_resolutions_keep_their_pull_request(self):
        for resolution in RESOLUTIONS_WITHOUT_CODE:
            task = self._create_task(resolution=resolution, pull_request_ids=[Command.link(self.pull_request.id)])
            task.stage_id = self.stage_done
            self.assertRecordValues(task, [{"stage_id": self.stage_done.id, "resolution": resolution}])
            self.assertEqual(task.pull_request_ids, self.pull_request, resolution)

    def test_stage_not_marked_lets_everything_through(self):
        self.task.stage_id = self.stage_review
        self.task.write({"resolution": "code", "stage_id": self.stage_progress.id})
        self.assertRecordValues(self.task, [{"stage_id": self.stage_progress.id, "resolution": "code"}])

    def test_project_not_flagged_lets_everything_through(self):
        self.project.is_ai_driven = False
        self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "resolution": False}])

    def test_project_without_marked_stage_lets_everything_through(self):
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "resolution": False}])

    def test_write_that_keeps_the_stage_leaves_the_task_alone(self):
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.pr_required_states = self.stage_done

        self.task.write({"name": "Renamed", "stage_id": self.stage_done.id})
        self.task.resolution = "code"

        self.assertRecordValues(self.task, [{"name": "Renamed", "resolution": "code", "pull_request_ids": []}])

    def test_resolution_arrives_with_the_stage(self):
        self.task.write({"stage_id": self.stage_done.id, "resolution": "configuration"})
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "resolution": "configuration"}])

    def test_pull_request_arrives_with_the_stage(self):
        self.task.write({"stage_id": self.stage_done.id, "pr_uri": "https://github.com/owner/repo/pull/2"})
        self.assertRecordValues(self.task, [{"stage_id": self.stage_done.id, "resolution": "code"}])
        self.assertEqual(self.task.pull_request_ids.url, "https://github.com/owner/repo/pull/2")

    def test_create_in_a_marked_stage(self):
        with self.assertRefused("Choose a Resolution"):
            self._create_task(stage_id=self.stage_done.id)
        with self.assertRefused("Add a pull request"):
            self._create_task(stage_id=self.stage_done.id, resolution="code")
        no_change_task = self._create_task(stage_id=self.stage_done.id, resolution="no_change")
        self.assertEqual(no_change_task.stage_id, self.stage_done)
        code_task = self._create_task(stage_id=self.stage_done.id, pr_uri=self.pull_request.url)
        self.assertRecordValues(code_task, [{"resolution": "code", "pull_request_ids": self.pull_request.ids}])

    def test_several_tasks_in_one_write(self):
        resolved_task = self._create_task(resolution="data")
        linked_task = self._create_task(pull_request_ids=[Command.link(self.pull_request.id)])
        (resolved_task | linked_task).write({"stage_id": self.stage_done.id})
        self.assertEqual((resolved_task | linked_task).mapped("resolution"), ["data", "code"])
        with self.assertRefused(self.task.name):
            (self.task | self._create_task(resolution="data")).write({"stage_id": self.stage_done.id})

    def test_project_change_that_moves_the_stage_runs_the_rule(self):
        other_project = self.env["project.project"].create({"name": "Other AI-driven Project", "is_ai_driven": True})
        other_project.pr_required_states = self._create_stage("Delivered", 1, project=other_project)

        with self.assertRefused("Choose a Resolution"):
            self.task.project_id = other_project
        # on its own: a module that writes the new project first would leave the Resolution behind
        self.task.resolution = "data"
        self.task.project_id = other_project

        self.assertEqual(self.task.stage_id, other_project.pr_required_states)

    def test_task_without_a_project_skips_the_rule(self):
        private_task = self.env["project.task"].create(
            {"name": "Private Task", "user_ids": [Command.link(self.user_dev.id)]},
        )
        self.assertRecordValues(private_task, [{"project_id": False, "resolution": False}])
        # taken out of its project, a task leaves its stage: a move the rule is run on
        self.task.write({"project_id": False, "user_ids": [Command.link(self.user_dev.id)]})
        self.assertRecordValues(self.task, [{"project_id": False, "stage_id": False, "resolution": False}])

    def test_duplicated_project_keeps_what_closed_its_tasks(self):
        self.project.allow_subtasks = True
        subtask_pull_request = self.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/2"})
        closed_task = self._create_task(
            name="Closed Task",
            stage_id=self.stage_done.id,
            resolution="configuration",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )
        self._create_task(
            name="Closed Subtask",
            parent_id=closed_task.id,
            stage_id=self.stage_done.id,
            resolution="code",
            pull_request_ids=[Command.link(subtask_pull_request.id)],
        )

        duplicated_project = self.project.copy()

        self.assertEqual(duplicated_project.pr_required_states, self.stage_done)
        duplicated_task = duplicated_project.tasks.filtered(lambda task: task.name == "Closed Task")
        self.assertRecordValues(
            duplicated_task | duplicated_task.child_ids,
            [
                {
                    "name": "Closed Task",
                    "stage_id": self.stage_done.id,
                    "resolution": "configuration",
                    "pull_request_ids": self.pull_request.ids,
                },
                {
                    "name": "Closed Subtask",
                    "stage_id": self.stage_done.id,
                    "resolution": "code",
                    "pull_request_ids": subtask_pull_request.ids,
                },
            ],
        )
        self.assertEqual(self.pull_request.task_ids, closed_task | duplicated_task)

    def test_duplicated_project_refuses_a_task_closed_with_neither(self):
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.pr_required_states = self.stage_done

        with self.assertRefused("Choose a Resolution", self.task.name):
            self.project.copy()

    def test_copy_that_keeps_the_stage_takes_what_its_default_sets(self):
        closed_task = self._create_task(
            stage_id=self.stage_done.id,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )
        copied_task = closed_task.copy(
            {"stage_id": self.stage_done.id, "resolution": "no_change", "pull_request_ids": [Command.clear()]},
        )
        self.assertRecordValues(
            copied_task,
            [{"stage_id": self.stage_done.id, "resolution": "no_change", "pull_request_ids": []}],
        )

    def test_plain_duplicate_of_a_closed_task_carries_nothing(self):
        closed_task = self._create_task(
            stage_id=self.stage_done.id,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )
        copied_task = closed_task.copy()
        self.assertRecordValues(
            copied_task,
            [{"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": [], "pr_uri": False}],
        )

    def test_user_who_cannot_read_the_project(self):
        """The rule is called as the write of a task calls it. The write is left
        out: a module that restricts who moves a task may read the project.
        """
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.write({"pr_required_states": self.stage_done.ids, "privacy_visibility": "followers"})
        task = self.task.with_user(self.user_dev)

        with self.assertRefused("Choose a Resolution"):
            task._apply_closing_rule()
        self.task.pull_request_ids = self.pull_request
        task._apply_closing_rule()

        self.assertEqual(self.task.resolution, "code")

    def test_context_does_not_skip_the_rule(self):
        task = self.task.sudo().with_context(tracking_disable=True, mail_notrack=True, install_mode=True)
        with self.assertRefused("Choose a Resolution"):
            task.stage_id = self.stage_done

    def test_resolution_set_by_the_rule_is_tracked(self):
        self._flush_tracking()
        self.task.write({"stage_id": self.stage_done.id, "pull_request_ids": [Command.link(self.pull_request.id)]})
        self._flush_tracking()
        tracking_value = self.task.message_ids.tracking_value_ids.filtered(
            lambda value: value.field.name == "resolution"
        )
        self.assertRecordValues(tracking_value, [{"old_value_char": False, "new_value_char": "Code"}])

    def test_task_form_shows_the_resolution_on_flagged_projects_only(self):
        with Form(self.task) as task_form:
            task_form.resolution = "data"
        self.assertEqual(self.task.resolution, "data")
        self.project.is_ai_driven = False
        with self.assertRaises(AssertionError), Form(self.task) as task_form:
            task_form.resolution = "code"

    def test_project_form_shows_the_stages_on_flagged_projects_only(self):
        # never saved: the project form may require fields of other modules
        project_form = Form(self.project)
        project_form.pr_required_states.add(self.stage_review)
        self.assertEqual(project_form.pr_required_states[:], self.stage_done | self.stage_review)
        self.project.is_ai_driven = False
        with self.assertRaises(AssertionError):
            Form(self.project).pr_required_states.add(self.stage_progress)


@tagged("post_install", "-at_install")
class TestSkipNextToPullRequestModule(ProjectAiDrivenCommon):
    def test_skips_only_while_the_module_is_installed(self):
        Module = self.env["ir.module.module"]
        module = Module.search([("name", "=", PULL_REQUEST_MODULE)]) or Module.create({"name": PULL_REQUEST_MODULE})
        module.state = "installed"
        with self.assertRaises(SkipTest) as skip:
            self._skip_next_to_pull_request_module()
        self.assertIn("its constraint refuses a task without pr_uri in a marked stage", str(skip.exception))
        for state in ("to install", "uninstallable", "uninstalled"):
            module.state = state
            self.assertNotSkipped(state)
        # an installed module cannot lose its row: under another name, the module has none
        module.write({"name": "never_" + PULL_REQUEST_MODULE, "state": "installed"})
        self.assertNotSkipped("without a row")

    def assertNotSkipped(self, module_state):
        try:
            self._skip_next_to_pull_request_module()
        except SkipTest as skip:
            # raised from here, a skip would mark this test skipped instead of failed
            self.fail("skipped with the module %s: %s" % (module_state, skip))
