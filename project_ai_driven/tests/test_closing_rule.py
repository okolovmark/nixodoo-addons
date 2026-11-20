from lxml import etree

from odoo import Command
from odoo.tests import Form

from .common import ProjectAiDrivenCommon

RESOLUTIONS_WITHOUT_CODE = ("data", "configuration", "no_change")


class TestClosingRule(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_progress = cls._create_stage("In Progress", 1)
        cls.stage_review = cls._create_stage("Review", 2)
        cls.stage_done = cls._create_stage("Done", 3)
        cls.project.pr_required_states = cls.stage_done
        cls.task.stage_id = cls.stage_progress
        cls.pull_request = cls.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/1"})

    def _create_task(self, **values):
        return self.env["project.task"].create(
            {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_progress.id, **values},
        )

    def _create_closed_task(self, **values):
        return self._create_task(
            stage_id=self.stage_done.id,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
            **values,
        )

    def _create_recurring_task(self, stage):
        """Return a recurring task in stage, with a Resolution and a pull request."""
        self.project.allow_recurring_tasks = True
        return self._create_task(
            stage_id=stage.id,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
            recurring_task=True,
            repeat_type="forever",
        )

    def _create_project(self, name, **values):
        """With the stages of self.project: a task moved there keeps its stage."""
        return self.env["project.project"].create(
            {"name": name, "type_ids": [Command.set((self.stage_progress | self.stage_done).ids)], **values},
        )

    def _put_in_the_marked_stage(self, task):
        """As a task that sat in the stage before the project marked it."""
        self.project.pr_required_states = False
        task.stage_id = self.stage_done
        self.project.pr_required_states = self.stage_done

    def _close_last_occurrence(self, task):
        """Set the state of a recurring task to done, on which core creates its
        next occurrence, and return that one.
        """
        task.state = "1_done"
        return task.recurrence_id.task_ids - task

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

    def test_task_state_is_not_part_of_the_rule(self):
        for state in ("1_done", "1_canceled"):
            self.task.state = state
            self.assertRecordValues(self.task, [{"state": state, "stage_id": self.stage_progress.id}])
        with self.assertRefused("Choose a Resolution"):
            self.task.write({"state": "1_done", "stage_id": self.stage_done.id})

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

    def test_batch_create_with_one_task_refused_names_it_alone(self):
        names = ["Batch Task in Progress", "Batch Task Done without Resolution", "Batch Task Done with Data"]
        with self.assertRefused("Choose a Resolution", names[1]) as refusal:
            self.env["project.task"].create(
                [
                    {"name": names[0], "project_id": self.project.id, "stage_id": self.stage_progress.id},
                    {"name": names[1], "project_id": self.project.id, "stage_id": self.stage_done.id},
                    {
                        "name": names[2],
                        "project_id": self.project.id,
                        "stage_id": self.stage_done.id,
                        "resolution": "data",
                    },
                ]
            )
        for name in (names[0], names[2]):
            self.assertNotIn(name, refusal.exception.args[0])

    def test_several_tasks_in_one_write(self):
        resolved_task = self._create_task(resolution="data")
        linked_task = self._create_task(pull_request_ids=[Command.link(self.pull_request.id)])
        (resolved_task | linked_task).write({"stage_id": self.stage_done.id})
        self.assertEqual((resolved_task | linked_task).mapped("resolution"), ["data", "code"])
        with self.assertRefused(self.task.name):
            (self.task | self._create_task(resolution="data")).write({"stage_id": self.stage_done.id})

    def test_project_change_that_moves_the_stage_runs_the_rule(self):
        other_project = self.env["project.project"].create(
            {"name": "Other AI-driven Project", "is_ai_driven": True, "type_ids": [Command.clear()]},
        )
        other_project.pr_required_states = self._create_stage("Delivered", 1, project=other_project)

        with self.assertRefused("Choose a Resolution"):
            self.task.project_id = other_project
        # on its own: a module that writes the new project first would leave the Resolution behind
        self.task.resolution = "data"
        self.task.project_id = other_project

        self.assertEqual(self.task.stage_id, other_project.pr_required_states)

    def test_project_change_into_a_stage_the_new_project_marks(self):
        plain_project = self._create_project("Plain Project")
        task = self._create_task(name="Plain Task", project_id=plain_project.id, stage_id=self.stage_done.id)

        with self.assertRefused("Choose a Resolution", "Plain Task"):
            task.project_id = self.project
        self.assertEqual(task.project_id, plain_project)
        task.resolution = "data"
        task.project_id = self.project

        self.assertRecordValues(task, [{"project_id": self.project.id, "stage_id": self.stage_done.id}])

    def test_project_change_between_flagged_projects_that_keeps_a_marked_stage(self):
        other_project = self._create_project(
            "Other AI-driven Project", is_ai_driven=True, pr_required_states=[Command.set(self.stage_done.ids)]
        )
        code_task = self._create_closed_task()
        linked_task = self._create_task(pull_request_ids=[Command.link(self.pull_request.id)])
        self._put_in_the_marked_stage(linked_task)

        (code_task | linked_task).project_id = other_project

        self.assertRecordValues(
            code_task | linked_task,
            [
                {"project_id": other_project.id, "stage_id": self.stage_done.id, "resolution": "code"},
                {"project_id": other_project.id, "stage_id": self.stage_done.id, "resolution": "code"},
            ],
        )

    def test_project_change_that_recomputes_a_stage_not_marked_is_not_checked(self):
        plain_project = self._create_project("Plain Project")
        task = self._create_task(project_id=plain_project.id, stage_id=self.stage_done.id)
        other_project = self.env["project.project"].create(
            {"name": "Other AI-driven Project", "is_ai_driven": True, "type_ids": [Command.clear()]},
        )
        other_backlog = self._create_stage("Backlog", 1, project=other_project)
        other_project.pr_required_states = self._create_stage("Delivered", 2, project=other_project)

        task.project_id = other_project

        self.assertRecordValues(
            task, [{"project_id": other_project.id, "stage_id": other_backlog.id, "resolution": False}]
        )

    def test_project_change_to_a_project_not_flagged_is_not_checked(self):
        # marked too: only the flag keeps the task out of the rule
        plain_project = self._create_project("Plain Project", pr_required_states=[Command.set(self.stage_done.ids)])
        self._put_in_the_marked_stage(self.task)

        self.task.project_id = plain_project

        self.assertRecordValues(
            self.task, [{"project_id": plain_project.id, "stage_id": self.stage_done.id, "resolution": False}]
        )

    def test_project_written_with_the_same_project_is_not_checked(self):
        self._put_in_the_marked_stage(self.task)

        self.task.write({"project_id": self.project.id})
        self.task.write({"project_id": self.project.id, "stage_id": self.stage_done.id, "name": "Renamed"})

        self.assertRecordValues(self.task, [{"name": "Renamed", "stage_id": self.stage_done.id, "resolution": False}])

    def test_subtask_given_a_project_of_its_own_is_checked(self):
        plain_project = self._create_project("Plain Project")
        parent = self._create_task(name="Parent Task", project_id=plain_project.id)
        subtask = self._create_task(
            name="Subtask", parent_id=parent.id, project_id=plain_project.id, stage_id=self.stage_done.id
        )

        with self.assertRefused("Choose a Resolution", "Subtask"):
            subtask.project_id = self.project
        self.assertEqual(subtask.project_id, plain_project)
        subtask.resolution = "data"
        subtask.project_id = self.project

        self.assertRecordValues(
            subtask, [{"project_id": self.project.id, "stage_id": self.stage_done.id, "display_in_project": True}]
        )

    def test_subtask_that_follows_its_parent_into_a_marked_stage_is_not_checked(self):
        plain_project = self._create_project("Plain Project")
        parent = self._create_task(name="Parent Task", project_id=plain_project.id, resolution="data")
        subtask = self._create_task(
            name="Subtask", parent_id=parent.id, project_id=plain_project.id, stage_id=self.stage_done.id
        )

        parent.project_id = self.project

        self.assertRecordValues(
            subtask, [{"project_id": self.project.id, "stage_id": self.stage_done.id, "resolution": False}]
        )

    def test_task_without_a_project_skips_the_rule(self):
        private_task = self.env["project.task"].create(
            {"name": "Private Task", "user_ids": [Command.link(self.user_dev.id)]},
        )
        self.assertRecordValues(private_task, [{"project_id": False, "resolution": False}])
        # taken out of its project, a task leaves its stage: a move the rule is run on
        self.task.write({"project_id": False, "user_ids": [Command.link(self.user_dev.id)]})
        self.assertRecordValues(self.task, [{"project_id": False, "stage_id": False, "resolution": False}])

    def test_duplicated_project_keeps_what_closed_its_tasks(self):
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

        self.assertRecordValues(
            duplicated_project,
            [{"is_ai_driven": True, "pr_required_states": self.stage_done.ids}],
        )
        duplicated_task = duplicated_project.tasks.filtered(lambda task: task.name == "Closed Task")
        self.assertRecordValues(
            duplicated_task | duplicated_task.child_ids,
            [
                {
                    "name": "Closed Task",
                    "project_id": duplicated_project.id,
                    "stage_id": self.stage_done.id,
                    "resolution": "configuration",
                    "pull_request_ids": self.pull_request.ids,
                },
                {
                    "name": "Closed Subtask",
                    "project_id": duplicated_project.id,
                    "stage_id": self.stage_done.id,
                    "resolution": "code",
                    "pull_request_ids": subtask_pull_request.ids,
                },
            ],
        )
        self.assertEqual(self.pull_request.task_ids, closed_task | duplicated_task)
        open_task = duplicated_project.tasks.filtered(lambda task: task.name == self.task.name)
        self.assertRecordValues(
            open_task,
            [{"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": []}],
        )

    def test_duplicated_project_refuses_a_task_closed_with_neither(self):
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.pr_required_states = self.stage_done

        with self.assertRefused("Choose a Resolution", self.task.name):
            self.project.copy()

    def test_copy_that_keeps_the_stage_takes_what_its_default_sets(self):
        copied_task = self._create_closed_task().copy(
            {"stage_id": self.stage_done.id, "resolution": "no_change", "pull_request_ids": [Command.clear()]},
        )
        self.assertRecordValues(
            copied_task,
            [{"stage_id": self.stage_done.id, "resolution": "no_change", "pull_request_ids": []}],
        )

    def test_plain_duplicate_of_a_closed_task_takes_what_closed_it(self):
        copied_task = self._create_closed_task().copy()
        self.assertRecordValues(
            copied_task,
            [
                {
                    "stage_id": self.stage_done.id,
                    "resolution": "code",
                    "pull_request_ids": self.pull_request.ids,
                    "pr_uri": self.pull_request.url,
                },
            ],
        )

    def test_plain_duplicate_of_a_task_closed_with_neither_is_refused(self):
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.pr_required_states = self.stage_done

        with self.assertRefused("Choose a Resolution"):
            self.task.copy()

    def test_duplicate_into_a_stage_not_marked_takes_nothing(self):
        copied_task = self._create_closed_task().copy({"stage_id": self.stage_review.id})
        self.assertRecordValues(
            copied_task,
            [{"stage_id": self.stage_review.id, "resolution": False, "pull_request_ids": [], "pr_uri": False}],
        )

    def test_duplicate_of_a_task_without_a_stage_takes_nothing(self):
        private_task = self.env["project.task"].create(
            {
                "name": "Private Task",
                "user_ids": [Command.link(self.user_dev.id)],
                "resolution": "code",
                "pull_request_ids": [Command.link(self.pull_request.id)],
            },
        )
        stageless_task = self._create_task(
            stage_id=False,
            resolution="data",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )
        self.assertFalse((private_task | stageless_task).stage_id)

        copied_tasks = (private_task | stageless_task).copy()

        self.assertRecordValues(
            copied_tasks,
            [{"stage_id": False, "resolution": False, "pull_request_ids": [], "pr_uri": False}] * 2,
        )

    def test_duplicate_into_another_marked_stage_is_a_new_task_to_the_rule(self):
        self.project.pr_required_states = self.stage_done | self.stage_review
        closed_task = self._create_closed_task()

        with self.assertRefused("Choose a Resolution"):
            closed_task.copy({"stage_id": self.stage_review.id})
        with self.assertRefused("Add a pull request"):
            closed_task.copy({"stage_id": self.stage_review.id, "resolution": "code"})
        with self.assertRefused("Choose a Resolution"):
            self.task.copy({"stage_id": self.stage_done.id})
        copied_task = closed_task.copy({"stage_id": self.stage_review.id, "resolution": "data"})

        self.assertRecordValues(
            copied_task,
            [{"stage_id": self.stage_review.id, "resolution": "data", "pull_request_ids": []}],
        )

    def test_several_tasks_duplicated_in_one_call_each_take_their_own(self):
        other_pull_request = self.env["project.pull.request"].create({"url": "https://github.com/owner/repo/pull/2"})
        closed_task = self._create_closed_task()
        data_task = self._create_task(
            stage_id=self.stage_done.id,
            resolution="data",
            pull_request_ids=[Command.link(other_pull_request.id)],
        )

        copied_tasks = (closed_task | self.task | data_task).copy()

        self.assertRecordValues(
            copied_tasks,
            [
                {"stage_id": self.stage_done.id, "resolution": "code", "pull_request_ids": self.pull_request.ids},
                {"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": []},
                {"stage_id": self.stage_done.id, "resolution": "data", "pull_request_ids": other_pull_request.ids},
            ],
        )

    def test_duplicate_of_a_task_takes_its_subtasks_with_what_closed_them(self):
        parent_task = self._create_task(name="Parent Task")
        self._create_closed_task(name="Closed Subtask", parent_id=parent_task.id)

        copied_task = parent_task.copy()

        self.assertRecordValues(
            copied_task | copied_task.child_ids,
            [
                {"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": []},
                {"stage_id": self.stage_done.id, "resolution": "code", "pull_request_ids": self.pull_request.ids},
            ],
        )

    def test_task_created_from_a_template_takes_no_resolution_and_no_pull_request(self):
        task_template = self._create_task(
            stage_id=self.stage_review.id,
            is_template=True,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )

        task = self.env["project.task"].browse(task_template.action_create_from_template())

        self.assertRecordValues(
            task,
            [
                {
                    "is_template": False,
                    "stage_id": self.stage_review.id,
                    "resolution": False,
                    "pull_request_ids": [],
                    "pr_uri": False,
                },
            ],
        )
        self.assertEqual(self.pull_request.task_ids, task_template)

    def test_task_created_from_a_template_in_a_marked_stage_is_refused(self):
        task_template = self._create_closed_task(is_template=True)

        with self.assertRefused("Choose a Resolution"):
            task_template.action_create_from_template()
        self.assertRecordValues(
            task_template.copy(),
            [{"is_template": True, "stage_id": self.stage_done.id, "resolution": "code"}],
        )

    def _create_project_template(self):
        """Core gives a project created from a template the stages of the
        template, its tasks keep their stage, and the marked stages are copied.
        """
        return self.env["project.project"].create(
            {
                "name": "AI-driven Project Template",
                "is_template": True,
                "is_ai_driven": True,
                "type_ids": [Command.set((self.stage_progress | self.stage_review | self.stage_done).ids)],
                "pr_required_states": [Command.set(self.stage_done.ids)],
            },
        )

    def test_project_created_from_a_template_has_tasks_without_resolution_or_pull_request(self):
        project_template = self._create_project_template()
        self._create_task(
            name="Reviewed Task",
            project_id=project_template.id,
            stage_id=self.stage_review.id,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )

        project = project_template.action_create_from_template({"name": "Project From Template"})

        self.assertRecordValues(project, [{"is_template": False, "pr_required_states": self.stage_done.ids}])
        self.assertRecordValues(
            project.tasks,
            [
                {
                    "name": "Reviewed Task",
                    "stage_id": self.stage_review.id,
                    "resolution": False,
                    "pull_request_ids": [],
                },
            ],
        )

    def test_project_template_with_a_task_in_a_marked_stage_is_refused(self):
        project_template = self._create_project_template()
        self._create_closed_task(project_id=project_template.id)

        with self.assertRefused("Choose a Resolution"):
            project_template.action_create_from_template({"name": "Project From Template"})

    def test_template_tasks_enter_a_marked_stage_without_resolution(self):
        template_tasks = self._create_template_tasks(self.stage_done, self._create_project_template())

        template_tasks.stage_id = self.stage_progress
        template_tasks.stage_id = self.stage_done

        self.assertRecordValues(
            template_tasks,
            [{"stage_id": self.stage_done.id, "resolution": False, "pull_request_ids": []}] * 3,
        )

    def test_task_leaving_template_status_with_its_stage_meets_the_rule(self):
        """The exemption is read after the write: a write that ends template
        status and moves the stage is checked.
        """
        task_template, _subtask, project_template_task = self._create_template_tasks(
            self.stage_progress, self._create_project_template()
        )
        leaving_writes = (
            (task_template, {"is_template": False, "stage_id": self.stage_done.id}),
            (project_template_task, {"project_id": self.project.id, "stage_id": self.stage_done.id}),
        )
        for task, values in leaving_writes:
            with self.subTest(task=task.name), self.assertRefused("Choose a Resolution"):
                task.write(values)

    def test_task_of_a_project_template_moved_with_its_marked_stage_meets_the_rule(self):
        """A task of a project template moved to a flagged project that shares
        and marks its stage keeps the stage, and is checked: it is no longer a
        template.
        """
        *_task_templates, project_template_task = self._create_template_tasks(
            self.stage_done, self._create_project_template()
        )
        with self.assertRefused("Choose a Resolution", project_template_task.name):
            project_template_task.project_id = self.project

        project_template_task.write({"project_id": self.project.id, "resolution": "data"})
        self.assertRecordValues(
            project_template_task,
            [{"project_id": self.project.id, "stage_id": self.stage_done.id, "has_project_template": False}],
        )

    def test_template_created_from_a_project_takes_no_resolution_and_no_pull_request(self):
        # in the stage before the project marked it
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.pr_required_states = self.stage_done
        closed_task = self._create_closed_task(name="Closed Task")
        closed_subtask = self._create_closed_task(name="Closed Subtask", parent_id=closed_task.id)
        self._create_closed_task(name="Archived Task").active = False

        action = self.project.action_create_template_from_project()

        project_template = self.env["project.project"].browse(action["params"]["project_id"])
        copied_tasks = project_template.with_context(active_test=False).tasks
        self.assertEqual(
            sorted(copied_tasks.mapped("name")),
            sorted([self.task.name, "Closed Task", "Closed Subtask", "Archived Task"]),
        )
        self.assertRecordValues(
            copied_tasks,
            [
                {
                    "has_project_template": True,
                    "stage_id": self.stage_done.id,
                    "resolution": False,
                    "pull_request_ids": [],
                    "pr_uri": False,
                },
            ]
            * 4,
        )
        self.assertRecordValues(
            closed_task | closed_subtask,
            [{"resolution": "code", "pull_request_ids": self.pull_request.ids}] * 2,
        )
        with self.assertRefused("Choose a Resolution"):
            project_template.action_create_from_template({"name": "Project From Template"})

    def test_duplicated_project_template_keeps_what_closed_its_tasks(self):
        project_template = self._create_project_template()
        self._create_closed_task(project_id=project_template.id)

        duplicated_template = project_template.copy()

        self.assertRecordValues(
            duplicated_template.tasks,
            [{"has_project_template": True, "resolution": "code", "pull_request_ids": self.pull_request.ids}],
        )

    def test_task_converted_to_a_template_keeps_its_resolution_and_pull_request(self):
        closed_task = self._create_closed_task()

        closed_task.action_convert_to_template()

        self.assertRecordValues(
            closed_task,
            [
                {
                    "is_template": True,
                    "stage_id": self.stage_done.id,
                    "resolution": "code",
                    "pull_request_ids": self.pull_request.ids,
                    "pr_uri": self.pull_request.url,
                },
            ],
        )

    def test_next_occurrence_of_a_recurring_task_is_a_new_task_to_the_rule(self):
        closed_task = self._create_recurring_task(self.stage_done)

        next_task = self._close_last_occurrence(closed_task)

        self.assertRecordValues(
            next_task,
            [{"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": [], "pr_uri": False}],
        )
        with self.assertRefused("Choose a Resolution"):
            next_task.stage_id = self.stage_done
        next_task.write({"stage_id": self.stage_done.id, "resolution": "no_change"})
        self.assertRecordValues(
            closed_task | next_task,
            [
                {"stage_id": self.stage_done.id, "resolution": "code", "pull_request_ids": self.pull_request.ids},
                {"stage_id": self.stage_done.id, "resolution": "no_change", "pull_request_ids": []},
            ],
        )

    def test_next_occurrence_of_a_recurring_task_with_a_subtask(self):
        closed_task = self._create_recurring_task(self.stage_done)
        self._create_closed_task(name="Closed Subtask", parent_id=closed_task.id)

        next_task = self._close_last_occurrence(closed_task)

        next_subtask = next_task.child_ids
        self.assertRecordValues(
            next_task | next_subtask,
            [{"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": [], "pr_uri": False}] * 2,
        )
        self.assertEqual(next_subtask.name, "Closed Subtask")
        with self.assertRefused("Choose a Resolution"):
            next_subtask.stage_id = self.stage_done
        next_subtask.write({"stage_id": self.stage_done.id, "pr_uri": self.pull_request.url})
        self.assertRecordValues(next_subtask, [{"stage_id": self.stage_done.id, "resolution": "code"}])

    def test_next_occurrence_that_keeps_the_stage_takes_nothing_of_what_closed_the_last(self):
        # an occurrence starts in the first stage of its project
        self.project.pr_required_states = self.stage_progress
        closed_task = self._create_recurring_task(self.stage_progress)
        self._create_task(
            name="Subtask",
            parent_id=closed_task.id,
            resolution="code",
            pull_request_ids=[Command.link(self.pull_request.id)],
        )

        next_task = self._close_last_occurrence(closed_task)

        self.assertEqual(next_task.child_ids.name, "Subtask")
        self.assertRecordValues(
            next_task | next_task.child_ids,
            [{"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": [], "pr_uri": False}] * 2,
        )
        self.assertRecordValues(
            closed_task | closed_task.child_ids,
            [{"resolution": "code", "pull_request_ids": self.pull_request.ids}] * 2,
        )

    def test_a_recurring_task_set_done_or_cancelled_gives_its_next_occurrence_the_marked_first_stage(self):
        """The next occurrence and its sub-task start in the first stage
        without a Resolution: their creation is not checked.
        """
        self.project.pr_required_states = self.stage_progress
        for closed_state in ("1_done", "1_canceled"):
            with self.subTest(closed_state=closed_state):
                recurring_task = self._create_recurring_task(self.stage_review)
                self._create_task(name="Subtask", parent_id=recurring_task.id, stage_id=self.stage_review.id)

                recurring_task.state = closed_state

                next_task = recurring_task.recurrence_id.task_ids - recurring_task
                self.assertRecordValues(recurring_task, [{"stage_id": self.stage_review.id, "state": closed_state}])
                self.assertEqual(next_task.child_ids.name, "Subtask")
                self.assertRecordValues(
                    next_task | next_task.child_ids,
                    [{"stage_id": self.stage_progress.id, "resolution": False, "pull_request_ids": []}] * 2,
                )

    def test_the_next_occurrence_moved_back_into_a_marked_stage_is_checked(self):
        self.project.pr_required_states = self.stage_progress
        next_task = self._close_last_occurrence(self._create_recurring_task(self.stage_review))
        next_task.stage_id = self.stage_review

        with self.assertRefused("Choose a Resolution"):
            next_task.stage_id = self.stage_progress
        self.assertEqual(next_task.stage_id, self.stage_review)

    def test_a_recurring_task_closed_into_a_marked_stage_is_checked(self):
        self.project.write({"allow_recurring_tasks": True, "pr_required_states": self.stage_progress | self.stage_done})
        recurring_task = self._create_task(stage_id=self.stage_review.id, recurring_task=True, repeat_type="forever")

        with self.assertRefused("Choose a Resolution"):
            recurring_task.write({"stage_id": self.stage_done.id, "state": "1_done"})

        self.assertRecordValues(recurring_task, [{"stage_id": self.stage_review.id, "state": "01_in_progress"}])
        self.assertEqual(recurring_task.recurrence_id.task_ids, recurring_task)

    def test_the_next_occurrences_come_back_without_their_exemption(self):
        self.project.pr_required_states = self.stage_progress
        recurring_task = self._create_recurring_task(self.stage_review)

        next_task = self.env["project.task.recurrence"]._create_next_occurrences(recurring_task)

        self.assertEqual(next_task.stage_id, self.stage_progress)
        with self.assertRefused("Choose a Resolution"):
            next_task.env["project.task"].create(
                {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_progress.id}
            )

    def test_no_value_a_caller_can_send_exempts_a_task_from_the_rule(self):
        self.project.pr_required_states = self.stage_progress
        for value in (True, 1, "1"):
            with self.subTest(value=value), self.assertRefused("Choose a Resolution"):
                self.env["project.task"].with_context(project_ai_driven_next_occurrence=value).create(
                    {"name": "Task", "project_id": self.project.id, "stage_id": self.stage_progress.id}
                )

    def test_user_who_cannot_read_the_project(self):
        """The rule is called as the write of a task calls it. The write is left
        out: a module that restricts who moves a task may read the project.
        """
        self.project.pr_required_states = False
        self.task.stage_id = self.stage_done
        self.project.write({"pr_required_states": self.stage_done.ids, "privacy_visibility": "followers"})
        self.task.user_ids = self.user_dev
        task = self.task.with_user(self.user_dev)
        task.check_access("write")
        self.assertFalse(task.project_id.has_access("read"))

        # read again as the user: what the test itself read is in a cache he shares
        self.env.invalidate_all()
        with self.assertRefused("Choose a Resolution"):
            task._apply_closing_rule()
        self.task.pull_request_ids = self.pull_request
        self.env.invalidate_all()
        task._apply_closing_rule()

        self.assertEqual(self.task.resolution, "code")

    def test_context_does_not_skip_the_rule(self):
        task = self.task.sudo().with_context(tracking_disable=True, mail_notrack=True, install_mode=True)
        with self.assertRefused("Choose a Resolution"):
            task.stage_id = self.stage_done

    def test_resolution_set_by_the_rule_is_tracked(self):
        self._flush_tracking()
        self.task.write({"stage_id": self.stage_done.id, "pull_request_ids": [Command.link(self.pull_request.id)]})
        tracking_texts = self._tracking_texts(self.task)
        self.assertEqual(len(tracking_texts), 1)
        self.assertIn("None → Code (Resolution)", tracking_texts[0])

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

    def test_marked_stages_are_a_tasks_management_setting_of_the_project_stages(self):
        arch = etree.fromstring(self.env["project.project"].get_view(view_type="form")["arch"])
        setting_ids = arch.xpath("//group[@name='group_tasks_managment']/setting/@id")
        flag_position = setting_ids.index("ai_driven_project_setting")
        self.assertEqual(
            setting_ids[flag_position : flag_position + 3],
            ["ai_driven_project_setting", "develops_this_odoo_setting", "pr_required_states_setting"],
            "the flag, the develops-this-Odoo setting, then the stages",
        )
        setting = arch.xpath("//setting[@id='pr_required_states_setting']")[0]
        stages_field = setting.xpath("field[@name='pr_required_states']")[0]
        self.assertEqual(setting.get("invisible"), "not is_ai_driven")
        self.assertEqual(
            (stages_field.get("widget"), stages_field.get("domain"), stages_field.get("options")),
            ("many2many_tags", "[('project_ids', '=', id)]", "{'no_create': True}"),
        )
