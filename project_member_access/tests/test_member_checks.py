import unittest

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import tagged

from .common import MemberAccessCommon

PULL_REQUEST_URL = "https://example.com/org/repo/pull/1"
OTHER_PULL_REQUEST_URL = "https://example.com/org/repo/pull/2"
THIRD_PULL_REQUEST_URL = "https://example.com/org/repo/pull/3"


def grant_pull_request_unlink(env):
    # no group deletes a pull request here; another module may grant it
    env["ir.model.access"].create(
        {
            "name": "Pull request deletion",
            "model_id": env["ir.model"]._get_id("project.pull.request"),
            "group_id": env.ref("project.group_project_user").id,
            "perm_read": True,
            "perm_unlink": True,
        }
    )


@tagged("post_install", "-at_install")
class TestMemberChecks(MemberAccessCommon):
    def test_only_an_admin_changes_assignees(self):
        self._lead(self.public_project, self.editor)
        with self.assertRefused(AccessError, "Only an Admin of Public"):
            self.public_task.with_user(self.editor).write({"user_ids": [Command.link(self.editor.id)]})
        self._lead(self.public_project, self.admin)
        self.public_task.with_user(self.admin).write({"user_ids": [Command.link(self.editor.id)]})
        self.assertEqual(self.public_task.user_ids, self.editor)

    def test_writing_the_same_assignees_is_no_change(self):
        self._lead(self.private_project, self.editor)
        self.private_reader_task.with_user(self.editor).write({"user_ids": [Command.set(self.reader.ids)]})
        self.assertEqual(self.private_reader_task.user_ids, self.reader)

    def test_every_form_of_an_assignee_change_takes_an_admin(self):
        """Core's write replaces the assignees by ids given without commands, and clears them by False."""
        task = self._create_task(self.public_project, self.contributor).with_env(self.env)
        assignees = self.reader | self.editor
        task.user_ids = assignees
        self._lead(self.public_project, self.contributor)
        changes = (
            [self.reader.id],
            (self.reader.id,),
            False,
            [Command.set(self.reader.ids)],
            [Command.clear()],
            [Command.create({"name": "Created", "login": "member_access_created"})],
        )
        for user_ids in changes:
            with (
                self.subTest(user_ids=user_ids),
                self.assertRefused(AccessError, "Only an Admin of Public changes the assignees"),
            ):
                task.with_user(self.contributor).write({"user_ids": user_ids})
        for user_ids in (assignees.ids, tuple(assignees.ids), [Command.link(self.reader.id)]):
            task.with_user(self.contributor).write({"user_ids": user_ids, "name": "Same assignees"})
            self.assertEqual(task.user_ids, assignees)

    def test_a_refused_write_updates_no_user(self):
        """An update of a user through a people field changes no member of it: the check
        applies nothing, and core refuses the update to a user who may not write users.
        """
        task = self._create_task(self.public_project, self.contributor).with_env(self.env)
        self._lead(self.public_project, self.contributor)
        for field_name, user in (("user_ids", self.outsider), ("uat_user_ids", self.employee)):
            with self.subTest(field=field_name):
                name = user.name
                # unittest's: Odoo's rolls back what ran before the refusal
                with unittest.TestCase.assertRaises(self, AccessError):
                    task.with_user(self.contributor).write({field_name: [Command.update(user.id, {"name": "Renamed"})]})
                self.assertEqual(user.name, name)

    def test_an_update_masks_no_removal_of_people(self):
        task = self._create_task(self.public_project, self.contributor).with_env(self.env)
        task.write({"user_ids": [Command.set((self.reader | self.contributor).ids)]})
        self._lead(self.public_project, self.contributor)
        own_update = Command.update(self.contributor.id, {})
        with self.assertRefused(AccessError, "Only an Admin of Public changes the assignees"):
            task.with_user(self.contributor).write({"user_ids": [Command.set(self.reader.ids), own_update]})
        with self.assertRefused(AccessError, "Only an Admin of Public changes the assignees"):
            task.with_user(self.contributor).write({"user_ids": [Command.unlink(self.contributor.id), own_update]})
        task.write({"user_ids": [Command.set(self.editor.ids)], "uat_user_ids": [Command.set(self.contributor.ids)]})
        with self.assertRefused(AccessError, "Editor or Admin of Public, sets"):
            task.with_user(self.contributor).write({"uat_user_ids": [Command.unlink(self.contributor.id), own_update]})
        self.assertEqual(task.uat_user_ids, self.contributor)

    def test_only_an_admin_creates_a_task_with_assignees(self):
        with self.assertRefused(AccessError, "Only an Admin of Private"):
            self._create_task(self.private_project, self.contributor, user_ids=[Command.set(self.contributor.ids)])
        task = self._create_task(self.private_project, self.admin, user_ids=[Command.set(self.contributor.ids)])
        self.assertEqual(task.user_ids, self.contributor)

    def test_an_assignee_moves_the_stage_of_their_task(self):
        self.private_reader_task.with_user(self.reader).write({"stage_id": self.stage_done.id})
        self.assertEqual(self.private_reader_task.stage_id, self.stage_done)

    def test_a_contributor_does_not_move_the_stage_of_a_task_they_are_not_assigned(self):
        task = self._create_task(self.public_project, self.contributor)
        self._lead(self.public_project, self.contributor)
        with self.assertRefused(AccessError, "or an Editor or Admin of Public"):
            task.with_user(self.contributor).write({"stage_id": self.stage_done.id})

    def test_an_editor_moves_the_stage_of_any_task(self):
        self._lead(self.public_project, self.editor)
        self.public_task.with_user(self.editor).write({"stage_id": self.stage_done.id})
        self.assertEqual(self.public_task.stage_id, self.stage_done)

    def test_moving_a_task_in_takes_a_contributor(self):
        for user in self.reader | self.outsider:
            task = self._create_task(self.standard_project, user)
            with self.assertRefused(AccessError, "Only a Contributor, Editor or Admin of Public"):
                task.with_user(user).write({"project_id": self.public_project.id})
        task = self._create_task(self.standard_project, self.contributor)
        task.with_user(self.contributor).write({"project_id": self.public_project.id})
        self.assertEqual(task.project_id, self.public_project)

    def test_moving_a_task_with_assignees_in_takes_an_admin(self):
        task = self._create_task(self.standard_project, user_ids=[Command.set((self.contributor | self.admin).ids)])
        with self.assertRefused(AccessError, "Only an Admin of Public"):
            task.with_user(self.contributor).write({"project_id": self.public_project.id})
        task.with_user(self.admin).write({"project_id": self.public_project.id})
        self.assertEqual(task.project_id, self.public_project)

    def test_standard_projects_keep_no_role_check(self):
        task = self._create_task(self.standard_project, self.outsider)
        self._lead(self.standard_project, self.outsider)
        task.with_user(self.outsider).write(
            {"stage_id": self.stage_done.id, "user_ids": [Command.set(self.outsider.ids)]}
        )
        self.assertEqual((task.stage_id, task.user_ids), (self.stage_done, self.outsider))

    def test_a_settings_administrator_acts_as_admin(self):
        self._lead(self.private_project, self.sysadmin)
        self.private_task.with_user(self.sysadmin).write(
            {"user_ids": [Command.set(self.reader.ids)], "stage_id": self.stage_done.id}
        )
        self.assertEqual(self.private_task.user_ids, self.reader)

    def test_stages_of_a_public_or_private_project_are_kept_by_its_admins(self):
        Stage = self.env["project.task.type"].with_user(self.manager)
        with self.assertRefused(AccessError, "Only an Admin of"):
            self.stage_todo.with_user(self.manager).write({"fold": True})
        with self.assertRefused(AccessError, "Only an Admin of Private"):
            Stage.create({"name": "Review", "project_ids": [Command.link(self.private_project.id)]})
        with self.assertRefused(AccessError, "Only an Admin of"):
            self.stage_done.with_user(self.manager).unlink()
        self.env["project.member"].create(
            {"project_id": self.public_project.id, "user_id": self.manager.id, "role": "admin"}
        )
        self.stage_todo.with_user(self.manager).write({"fold": True})
        self.assertTrue(self.stage_todo.fold, "an Admin of one of its projects")

    def test_other_stages_are_left_alone(self):
        standard_stage = self.env["project.task.type"].create(
            {"name": "Standard only", "project_ids": [Command.link(self.standard_project.id)]}
        )
        standard_stage.with_user(self.manager).write({"fold": True})
        self.assertTrue(standard_stage.fold)

    def test_a_sub_task_follows_its_parent_in_only_as_the_role_allows(self):
        parent = self._create_task(self.standard_project, self.contributor)
        sub_task = self.env["project.task"].create(
            {"name": "Sub-task", "parent_id": parent.id, "user_ids": [Command.set(self.outsider.ids)]}
        )
        self.assertEqual(sub_task.project_id, self.standard_project)
        with self.assertRefused(ValidationError, "Not members: Outsider"):
            parent.with_user(self.contributor).write({"project_id": self.private_project.id})
        with self.assertRefused(ValidationError, "Not members: Outsider"):
            parent.with_user(self.admin).write({"project_id": self.private_project.id})
        self.assertEqual((parent | sub_task).project_id, self.standard_project)
        # their own sub-task: a module writing the sub-tasks of a moved task as the user would meet the rules first
        sub_task.user_ids = self.contributor
        with self.assertRefused(AccessError, "Only an Admin of Private moves a task with assignees into it"):
            parent.with_user(self.contributor).write({"project_id": self.private_project.id})
        self.assertEqual((parent | sub_task).project_id, self.standard_project)
        parent.with_user(self.admin).write({"project_id": self.private_project.id})
        self.assertEqual((parent | sub_task).project_id, self.private_project)

    def _create_sub_task(self, user):
        """A sub-task of a Standard task, without a project of its own: it follows its parent's."""
        parent = self._create_task(self.standard_project, user)
        sub_task = self._create_task(self.standard_project, user, parent_id=parent.id)
        self.assertFalse(sub_task.display_project_id)
        return sub_task

    def test_a_new_parent_moves_a_sub_task_in_only_as_the_role_allows(self):
        sub_task = self._create_sub_task(self.reader)
        with self.assertRefused(AccessError, "Only a Contributor, Editor or Admin of Private"):
            sub_task.with_user(self.reader).write({"parent_id": self.private_reader_task.id})
        self.assertEqual(sub_task.project_id, self.standard_project)
        sub_task = self._create_sub_task(self.contributor)
        sub_task.with_user(self.contributor).write({"parent_id": self.private_task.id})
        self.assertEqual(sub_task.project_id, self.private_project)

    def test_a_display_project_moves_a_sub_task_in_only_as_the_role_allows(self):
        sub_task = self._create_sub_task(self.reader)
        with self.assertRefused(AccessError, "Only a Contributor, Editor or Admin of Private"):
            sub_task.with_user(self.reader).write({"display_project_id": self.private_project.id})
        self.assertEqual(sub_task.project_id, self.standard_project)

    def _create_carried_sub_task(self, parent, **values):
        """A sub-task of parent assigned to the Contributor, without a project of its own: it follows its parent's."""
        values = {"name": "Carried", "user_ids": [Command.set(self.contributor.ids)], **values}
        sub_task = self._create_task(parent.project_id, parent_id=parent.id, **values)
        self.assertFalse(sub_task.display_project_id)
        return sub_task

    def test_a_parent_moved_out_does_not_carry_sub_tasks_one_may_not_edit(self):
        """The assignee of the parent edits it, not the sub-task: once out, the sub-task
        would be read and edited as its new project allows.
        """
        parent = self.private_reader_task
        sub_task = self._create_carried_sub_task(parent)
        self.assertFalse(self._allowed(sub_task, self.reader, "write"))
        project_of_the_reader = self.env["project.project"].create(
            {
                "name": "Of the reader",
                "access_mode": "public",
                "member_ids": [Command.create({"user_id": self.reader.id, "role": "admin"})],
            }
        )
        for project in self.standard_project | project_of_the_reader:
            with self.subTest(project=project.name):
                with self.assertRefused(AccessError, "Sub-tasks you may not edit would leave Private", "Carried"):
                    parent.with_user(self.reader).write({"project_id": project.id})

    def _create_own_sub_task(self):
        """A sub-task of a Private task assigned to the Reader, carrying a sub-task of its own."""
        own_sub_task = self._create_task(
            self.private_project, parent_id=self.private_task.id, user_ids=[Command.set(self.reader.ids)]
        )
        self._create_carried_sub_task(own_sub_task)
        return own_sub_task

    def test_a_task_given_a_parent_outside_does_not_carry_sub_tasks_one_may_not_edit(self):
        own_sub_task = self._create_own_sub_task()
        with self.assertRefused(AccessError, "Sub-tasks you may not edit would leave Private", "Carried"):
            own_sub_task.with_user(self.reader).write({"parent_id": self.standard_task.id})

    def test_a_task_given_a_display_project_outside_does_not_carry_sub_tasks_one_may_not_edit(self):
        own_sub_task = self._create_own_sub_task()
        with self.assertRefused(AccessError, "Sub-tasks you may not edit would leave Private", "Carried"):
            own_sub_task.with_user(self.reader).write({"display_project_id": self.standard_project.id})

    def test_a_parent_moved_out_carries_the_sub_tasks_one_edits(self):
        for user in self.editor | self.admin:
            with self.subTest(user=user.login):
                parent = self._create_task(self.private_project, user)
                sub_task = self._create_carried_sub_task(parent)
                parent.with_user(user).write({"project_id": self.standard_project.id})
                self.assertEqual((parent | sub_task).project_id, self.standard_project)
        own_sub_task = self._create_carried_sub_task(
            self.private_reader_task, user_ids=[Command.set(self.reader.ids)], name="Own"
        )
        self.private_reader_task.with_user(self.reader).write({"project_id": self.standard_project.id})
        self.assertEqual(own_sub_task.project_id, self.standard_project)

    def test_a_parent_moved_between_standard_projects_carries_its_sub_tasks(self):
        other_standard_project = self.env["project.project"].create({"name": "Other Standard"})
        parent = self._create_task(self.standard_project, self.outsider)
        sub_task = self._create_carried_sub_task(parent)
        parent.with_user(self.outsider).write({"project_id": other_standard_project.id})
        self.assertEqual((parent | sub_task).project_id, other_standard_project)

    def test_a_project_the_user_does_not_read_is_not_named(self):
        sub_task = self._create_sub_task(self.outsider)
        with self.assertRefused(AccessError, "Only a Contributor, Editor or Admin of a Public or Private project"):
            sub_task.with_user(self.outsider).write({"parent_id": self.private_task.id})

    def test_a_batch_mixing_standard_and_restricted_tasks_is_refused_whole(self):
        for project in self.standard_project | self.public_project:
            self._lead(project, self.editor)
        tasks = self.standard_task | self.public_task
        with self.assertRefused(AccessError, "Only an Admin of Public"):
            tasks.with_user(self.editor).write({"user_ids": [Command.set(self.editor.ids)]})
        self.assertFalse(tasks.user_ids)
        tasks.with_user(self.editor).write({"stage_id": self.stage_done.id})
        self.assertEqual(tasks.stage_id, self.stage_done)

    def test_a_settings_administrator_creates_a_task_with_assignees(self):
        task = self._create_task(self.private_project, self.sysadmin, user_ids=[Command.set(self.reader.ids)])
        self.assertEqual(task.user_ids, self.reader)

    def test_uat_users_are_changed_by_an_assignee_an_editor_or_an_admin(self):
        task = self._create_task(self.public_project, self.contributor).with_env(self.env)
        uat_users = self.outsider | self.admin
        task.write({"user_ids": [Command.set(self.reader.ids)], "uat_user_ids": [Command.set(uat_users.ids)]})
        changes = (
            [Command.link(self.editor.id)],
            [Command.unlink(self.admin.id)],
            [Command.clear()],
            [Command.set([])],
            False,
            [self.outsider.id],
            (self.outsider.id,),
        )
        no_changes = ([Command.set(uat_users.ids)], [Command.link(self.outsider.id)], [], uat_users.ids)
        # the creator and a UAT user edit the task, so the UAT users check refuses them
        for user in self.contributor | self.outsider:
            for uat_user_ids in changes:
                with (
                    self.subTest(user=user.login, uat_user_ids=uat_user_ids),
                    self.assertRefused(AccessError, "or an Editor or Admin of Public, sets its UAT users"),
                ):
                    task.with_user(user).write({"uat_user_ids": uat_user_ids})
            for uat_user_ids in no_changes:
                task.with_user(user).write({"uat_user_ids": uat_user_ids, "name": "Same UAT users"})
                self.assertEqual(task.uat_user_ids, uat_users)
        for user in self.reader | self.editor | self.admin:
            task.with_user(user).write({"uat_user_ids": [Command.set(user.ids)]})
            self.assertEqual(task.uat_user_ids, user)
        task.with_user(self.reader).write({"uat_user_ids": False})
        self.assertFalse(task.uat_user_ids)

    def test_a_task_is_created_with_uat_users_by_editors_only(self):
        for project in self.public_project | self.private_project:
            with self.subTest(project=project.name):
                with self.assertRefused(AccessError, "or an Editor or Admin of %s, sets its UAT users" % project.name):
                    self._create_task(project, self.contributor, uat_user_ids=[Command.set(self.reader.ids)])
                with self.assertRefused(AccessError, "sets its UAT users"):
                    self.env["project.task"].with_context(
                        default_project_id=project.id, default_uat_user_ids=[Command.link(self.reader.id)]
                    ).with_user(self.contributor).create({"name": "Tested by default"})
                for user in self.editor | self.admin:
                    task = self._create_task(project, user, uat_user_ids=[Command.set(self.reader.ids)])
                    self.assertEqual(task.uat_user_ids, self.reader)

    def test_a_contributor_duplicates_a_task_with_uat_users(self):
        task = self._create_task(self.public_project, self.contributor).with_env(self.env)
        task.uat_user_ids = self.reader
        task_copy = task.with_user(self.contributor).copy()
        self.assertFalse(task_copy.uat_user_ids, "a copy takes no UAT users")

    def test_moving_a_task_with_uat_users_in_takes_an_editor(self):
        uat_users = [Command.set(self.reader.ids)]
        task = self._create_task(self.standard_project, self.contributor, uat_user_ids=uat_users)
        with self.assertRefused(AccessError, "Only an Editor or Admin of Public moves a task with UAT users"):
            task.with_user(self.contributor).write({"project_id": self.public_project.id})
        task = self._create_task(self.standard_project, self.editor, uat_user_ids=uat_users)
        task.with_user(self.editor).write({"project_id": self.public_project.id})
        self.assertEqual(task.project_id, self.public_project)

    def test_uat_users_of_a_standard_task_keep_no_role_check(self):
        task = self._create_task(self.standard_project, self.outsider, uat_user_ids=[Command.set(self.reader.ids)])
        task.with_user(self.outsider).write({"uat_user_ids": [Command.set(self.outsider.ids)]})
        self.assertEqual(task.uat_user_ids, self.outsider)

    def test_a_pull_request_of_a_private_task_is_changed_by_its_editors(self):
        grant_pull_request_unlink(self.env)
        pull_request = self.env["project.pull.request"].create(
            {"url": PULL_REQUEST_URL, "task_ids": [Command.set(self.private_task.ids)]}
        )
        pull_request.with_user(self.editor).write({"state": "merged"})
        pull_request.with_user(self.admin).write({"url": OTHER_PULL_REQUEST_URL})
        self.assertEqual((pull_request.state, pull_request.url), ("merged", OTHER_PULL_REQUEST_URL))
        pull_request.with_user(self.editor).unlink()
        self.assertFalse(pull_request.exists())

    def test_a_pull_request_of_standard_tasks_is_changed_by_any_project_user(self):
        grant_pull_request_unlink(self.env)
        self.standard_project.privacy_visibility = "followers"
        self.assertFalse(
            self.standard_task.with_user(self.outsider)._filter_access_rules("write"), "a task the user cannot edit"
        )
        pull_request = self.env["project.pull.request"].create(
            {"url": PULL_REQUEST_URL, "task_ids": [Command.set(self.standard_task.ids)]}
        )
        pull_request.with_user(self.outsider).write({"state": "merged", "url": OTHER_PULL_REQUEST_URL})
        self.assertEqual((pull_request.state, pull_request.url), ("merged", OTHER_PULL_REQUEST_URL))
        pull_request.with_user(self.outsider).unlink()
        self.assertFalse(pull_request.exists())

    def test_a_task_not_edited_is_not_linked_from_the_pull_request_side(self):
        kept_pull_request = self.env["project.pull.request"].create(
            {"url": THIRD_PULL_REQUEST_URL, "task_ids": [Command.set((self.public_task | self.private_task).ids)]}
        )
        PullRequest = self.env["project.pull.request"].with_user(self.outsider)
        pull_request = PullRequest.create({"url": PULL_REQUEST_URL})
        for task in self.public_task | self.private_task:
            links = {
                "write": lambda task=task: pull_request.write({"task_ids": [Command.link(task.id)]}),
                "create": lambda task=task: PullRequest.create(
                    {"url": OTHER_PULL_REQUEST_URL, "task_ids": [Command.link(task.id)]}
                ),
            }
            for link_name, link in links.items():
                # the savepoint: a link left by a failed subtest would refuse the next one
                with self.subTest(project=task.project_id.name, link=link_name), self.env.cr.savepoint():
                    with self.assertRefused(AccessError, "a task you cannot edit"):
                        link()
                    self.assertEqual(task.pull_request_ids, kept_pull_request)

    def test_an_editor_links_a_task_of_their_project_from_the_pull_request_side(self):
        PullRequest = self.env["project.pull.request"].with_user(self.editor)
        PullRequest.create({"url": PULL_REQUEST_URL}).write({"task_ids": [Command.link(self.public_task.id)]})
        PullRequest.create({"url": OTHER_PULL_REQUEST_URL, "task_ids": [Command.link(self.public_task.id)]})
        self.assertEqual(
            set(self.public_task.pull_request_ids.mapped("url")), {PULL_REQUEST_URL, OTHER_PULL_REQUEST_URL}
        )

    def test_a_pull_request_is_linked_from_an_own_task(self):
        private_pull_request = self.env["project.pull.request"].create(
            {"url": PULL_REQUEST_URL, "task_ids": [Command.set(self.private_task.ids)]}
        )
        for user, project in ((self.outsider, self.standard_project), (self.contributor, self.public_project)):
            with self.subTest(user=user.login):
                new_url = "https://example.com/org/%s/pull/1" % user.login
                own_task = self._create_task(project, user)
                own_task.pr_uri = PULL_REQUEST_URL
                own_task.pr_uri = new_url
                self.assertEqual(set(own_task.pull_request_ids.mapped("url")), {PULL_REQUEST_URL, new_url})
                self.assertIn(own_task, private_pull_request.task_ids)


@tagged("post_install", "-at_install")
class TestRefusalsNameNothingUnread(MemberAccessCommon):
    """A user refused by the access rules learns nothing of the records refused."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.secret_project = cls.env["project.project"].create(
            {
                "name": "Secret Plans",
                "access_mode": "private",
                "member_ids": [Command.create({"user_id": cls.admin.id, "role": "admin"})],
            }
        )
        cls.secret_stage = cls.env["project.task.type"].create(
            {"name": "Secret Stage", "project_ids": [Command.link(cls.secret_project.id)]}
        )
        cls.secret_task = cls._create_task(cls.secret_project, name="Secret Task", stage_id=cls.secret_stage.id)

    def assertNamesNothing(self, exception_class, operation):
        with self.assertRaises(exception_class) as refusal:
            operation()
        self.assertNotIn("Secret", str(refusal.exception))

    def test_a_stage_written_on_a_task_not_written(self):
        for user in self.portal | self.outsider | self.employee:
            with self.subTest(user=user.login):
                self.assertNamesNothing(
                    AccessError,
                    lambda user=user: self.secret_task.with_user(user).write({"stage_id": self.stage_done.id}),
                )

    def test_a_member_row_not_written(self):
        admin_row = self.secret_project.member_ids
        self.assertNamesNothing(AccessError, lambda: admin_row.with_user(self.outsider).write({"role": "editor"}))
        self.assertNamesNothing(AccessError, lambda: admin_row.with_user(self.outsider).unlink())

    def test_a_member_row_not_created(self):
        followers_project = self.env["project.project"].create(
            {"name": "Secret Followers", "privacy_visibility": "followers"}
        )
        self.assertFalse(followers_project.with_user(self.outsider)._filter_access_rules("read"))
        Member = self.env["project.member"].with_user(self.outsider)
        for project in self.secret_project | followers_project:
            with self.subTest(project=project.name):
                self.assertNamesNothing(
                    AccessError,
                    lambda project=project: Member.create(
                        {"project_id": project.id, "user_id": self.outsider.id, "role": "admin"}
                    ),
                )

    def test_a_stage_not_written(self):
        self.assertNamesNothing(AccessError, lambda: self.secret_stage.with_user(self.outsider).write({"fold": True}))
        self.assertNamesNothing(AccessError, lambda: self.secret_stage.with_user(self.outsider).unlink())

    def test_a_task_moved_in_with_non_members(self):
        # by its parent, written by id: core reads neither the parent nor its project as the user
        parent = self._create_task(self.standard_project)
        sub_task = self.env["project.task"].create(
            {"name": "Planted", "parent_id": parent.id, "user_ids": [Command.set(self.outsider.ids)]}
        )
        self.assertNamesNothing(
            UserError, lambda: sub_task.with_user(self.outsider).write({"parent_id": self.secret_task.id})
        )
        self.assertEqual(sub_task.project_id, self.standard_project)

    def test_a_sub_task_carried_out_of_a_project_not_read(self):
        """A sub-task given a Private project by id, unlike its parent's, leaves it when its
        parent moves and core finds it among the parent's sub-tasks in the cache, as after its
        create: read as the user from the database, they leave it out and it stays.
        """
        parent = self._create_task(self.standard_project, self.outsider)
        self._create_task(self.secret_project, name="Secret Sub-task", parent_id=parent.id)
        other_standard_project = self.env["project.project"].create({"name": "Other Standard"})
        # unittest's: Odoo's clears the cache
        with unittest.TestCase.assertRaises(self, AccessError) as refusal:
            parent.with_user(self.outsider).write({"project_id": other_standard_project.id})
        self.assertIn("Sub-tasks you may not edit would leave a Public or Private project", str(refusal.exception))
        self.assertNotIn("Secret", str(refusal.exception))

    def test_a_pull_request_of_a_task_not_written(self):
        grant_pull_request_unlink(self.env)
        pull_request = self.env["project.pull.request"].create(
            {"url": PULL_REQUEST_URL, "task_ids": [Command.set(self.secret_task.ids)]}
        )
        # the link of a hidden pull request is accepted on an own task, which lets its user read it
        self._create_task(self.standard_project, self.outsider).pr_uri = PULL_REQUEST_URL
        as_outsider = pull_request.with_user(self.outsider)
        changes = {
            "url": lambda: as_outsider.write({"url": OTHER_PULL_REQUEST_URL}),
            "state": lambda: as_outsider.write({"state": "merged"}),
            "deletion": as_outsider.unlink,
        }
        for change_name, change in changes.items():
            with self.subTest(change=change_name):
                self.assertNamesNothing(AccessError, change)
        self.assertEqual((pull_request.url, pull_request.state), (PULL_REQUEST_URL, "open"))
