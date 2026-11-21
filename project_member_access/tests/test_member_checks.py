from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import ROLE_LOGINS, MemberAccessCommon

PULL_REQUEST_URL = "https://example.com/org/repo/pull/1"
OTHER_PULL_REQUEST_URL = "https://example.com/org/repo/pull/2"
DONE = "1_done"
CANCELLED = "1_canceled"
APPROVED = "03_approved"
CHANGES_REQUESTED = "02_changes_requested"
IN_PROGRESS = "01_in_progress"


def grant_pull_request_unlink(env):
    # no group deletes a pull request here; another module may grant it
    env["ir.access"].create(
        {
            "name": "Pull request deletion",
            "model_id": env["ir.model"]._get_id("project.pull.request"),
            "group_id": env.ref("project.group_project_user").id,
            "operation": "d",
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

    def test_an_editor_neither_removes_nor_clears_assignees(self):
        self._lead(self.private_project, self.editor)
        for user_ids in ([Command.unlink(self.reader.id)], [Command.clear()], [Command.set([])], False):
            with self.subTest(user_ids=user_ids), self.assertRefused(AccessError, "Only an Admin of Private"):
                self.private_reader_task.with_user(self.editor).write({"user_ids": user_ids})

    def test_an_assignee_does_not_change_the_assignees_of_their_task(self):
        self._lead(self.private_project, self.reader)
        for user_ids in ([Command.link(self.contributor.id)], [Command.unlink(self.reader.id)]):
            with self.subTest(user_ids=user_ids), self.assertRefused(AccessError, "Only an Admin of Private"):
                self.private_reader_task.with_user(self.reader).write({"user_ids": user_ids})

    def test_writing_the_same_assignees_is_no_change(self):
        self._lead(self.private_project, self.editor)
        self.private_reader_task.with_user(self.editor).write({"user_ids": [Command.set(self.reader.ids)]})
        self.assertEqual(self.private_reader_task.user_ids, self.reader)

    def test_only_an_admin_creates_a_task_with_assignees(self):
        with self.assertRefused(AccessError, "Only an Admin of Private"):
            self._create_task(self.private_project, self.contributor, user_ids=[Command.set(self.contributor.ids)])
        task = self._create_task(self.private_project, self.admin, user_ids=[Command.set(self.contributor.ids)])
        self.assertEqual(task.user_ids, self.contributor)

    def test_assignees_a_screen_fills_in_take_an_admin(self):
        """My Tasks assigns the creator by a context default."""
        Task = self.env["project.task"].with_context(
            default_project_id=self.private_project.id, default_user_ids=[Command.link(self.contributor.id)]
        )
        with self.assertRefused(AccessError, "Only an Admin of Private creates a task with assignees"):
            Task.with_user(self.contributor).create({"name": "Mine"})
        task = Task.with_user(self.admin).create({"name": "Mine"})
        self.assertEqual(task.user_ids, self.contributor)

    def test_a_contributor_does_not_delete_a_task_they_created(self):
        task = self._create_task(self.private_project, self.contributor)
        with self.assertRefused(AccessError, in_place=True):
            task.with_user(self.contributor).unlink()
        self.assertTrue(task.exists())

    def test_a_reader_posts_in_the_chatter_of_a_task(self):
        message = self.private_task.with_user(self.reader).message_post(
            body="Seen", message_type="comment", subtype_xmlid="mail.mt_comment"
        )
        self.assertEqual(message.author_id, self.reader.partner_id)
        self.assertIn(message, self.private_task.message_ids)

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

    def test_an_assignee_closes_and_reopens_their_task(self):
        task = self.private_reader_task.with_user(self.reader)
        for state in (DONE, IN_PROGRESS, CANCELLED, APPROVED):
            task.state = state
            self.assertEqual(self.private_reader_task.state, state)

    def test_an_own_task_is_closed_and_reopened_by_its_assignees_and_editors_only(self):
        """A UAT user and the creator edit the task and mark a review, as the stage
        is moved: by an assignee, an Editor or an Admin.
        """
        task = self._create_task(self.public_project, self.contributor)
        task.with_user(self.editor).uat_user_ids = self.reader
        for user in self.reader | self.contributor:
            with self.subTest(user=user.login):
                for state in (APPROVED, CHANGES_REQUESTED):
                    task.with_user(user).state = state
                    self.assertEqual(task.state, state)
                for state in (DONE, CANCELLED):
                    with self.assertRefused(AccessError, "closes it or reopens it"):
                        task.with_user(user).state = state
        task.with_user(self.editor).state = DONE
        for user in self.reader | self.contributor:
            with self.subTest(user=user.login), self.assertRefused(AccessError, "closes it or reopens it"):
                task.with_user(user).state = IN_PROGRESS
        task.with_user(self.editor).state = IN_PROGRESS
        self.assertEqual(task.state, IN_PROGRESS)

    def test_a_task_is_created_closed_by_editors_only(self):
        for project in self.public_project | self.private_project:
            for state in (DONE, CANCELLED):
                with self.subTest(project=project.name, state=state):
                    with self.assertRefused(AccessError, "closes it or reopens it"):
                        self._create_task(project, self.contributor, state=state)
                    self.assertEqual(self._create_task(project, self.editor, state=state).state, state)
            self.assertEqual(self._create_task(project, self.contributor, state=APPROVED).state, APPROVED)

    def test_closing_a_standard_task_keeps_no_role_check(self):
        task = self._create_task(self.standard_project, self.outsider)
        task.with_user(self.outsider).state = DONE
        self.assertEqual(task.state, DONE)

    def test_moving_a_task_in_takes_a_contributor(self):
        for user in self.reader | self.outsider:
            task = self._create_task(self.standard_project, user)
            with self.assertRefused(AccessError, "Only a Contributor, Editor or Admin of Public"):
                task.with_user(user).write({"project_id": self.public_project.id})
        task = self._create_task(self.standard_project, self.contributor)
        task.with_user(self.contributor).write({"project_id": self.public_project.id})
        self.assertEqual(task.project_id, self.public_project)

    def test_a_task_moved_in_under_review_is_back_in_progress(self):
        """Core sets the state of a task that changes stage as the user, which is no reopening."""
        standard_stage = self.env["project.task.type"].create(
            {"name": "Standard only", "project_ids": [Command.link(self.standard_project.id)]}
        )
        task = self._create_task(self.standard_project, self.contributor, stage_id=standard_stage.id, state=APPROVED)
        task.with_user(self.contributor).write({"project_id": self.public_project.id})
        self.assertEqual(
            (task.project_id, task.stage_id, task.state), (self.public_project, self.stage_todo, IN_PROGRESS)
        )

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
            {"user_ids": [Command.set(self.reader.ids)], "stage_id": self.stage_done.id, "state": DONE}
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

    def test_an_admin_without_project_administrator_rights_changes_no_stage(self):
        """A role opens nothing the groups do not: core keeps project stages to Project Administrators."""
        with self.assertRaises(AccessError):
            self.stage_todo.with_user(self.admin).write({"fold": True})

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
        # their own sub-task: a module writing the sub-tasks of a moved task as the user would meet the rows first
        sub_task.user_ids = self.contributor
        with self.assertRefused(AccessError, "Only an Admin of Private moves a task with assignees into it"):
            parent.with_user(self.contributor).write({"project_id": self.private_project.id})
        parent.with_user(self.admin).write({"project_id": self.private_project.id})
        self.assertEqual((parent | sub_task).project_id, self.private_project)

    def _create_sub_task(self, user):
        """A sub-task of a Standard task, shown under its parent: it follows its parent's project."""
        parent = self._create_task(self.standard_project, user)
        sub_task = self._create_task(self.standard_project, user, parent_id=parent.id)
        self.assertFalse(sub_task.display_in_project)
        return sub_task

    def test_a_new_parent_moves_a_sub_task_in_only_as_the_role_allows(self):
        sub_task = self._create_sub_task(self.reader)
        with self.assertRefused(AccessError, "Only a Contributor, Editor or Admin of Private"):
            sub_task.with_user(self.reader).write({"parent_id": self.private_reader_task.id})
        sub_task = self._create_sub_task(self.contributor)
        sub_task.with_user(self.contributor).write({"parent_id": self.private_task.id})
        self.assertEqual(sub_task.project_id, self.private_project)

    def _create_carried_sub_task(self, parent):
        """A sub-task of parent assigned to somebody else, shown under it: it follows its parent's project."""
        sub_task = self._create_task(
            parent.project_id, name="Carried", parent_id=parent.id, user_ids=[Command.set(self.contributor.ids)]
        )
        self.assertFalse(sub_task.display_in_project)
        return sub_task

    def test_a_parent_moved_out_does_not_carry_sub_tasks_one_may_not_edit(self):
        """The assignee of the parent edits it, not the sub-task: once out, the sub-task
        would be read and edited as its new project allows.
        """
        parent = self.private_reader_task
        sub_task = self._create_carried_sub_task(parent)
        self.assertFalse(sub_task.with_user(self.reader).has_access("write"))
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

    def test_a_parent_moved_out_carries_the_sub_tasks_one_edits(self):
        for user in self.editor | self.admin:
            with self.subTest(user=user.login):
                parent = self._create_task(self.private_project, user)
                sub_task = self._create_carried_sub_task(parent)
                parent.with_user(user).write({"project_id": self.standard_project.id})
                self.assertEqual((parent | sub_task).project_id, self.standard_project)

    def test_a_project_the_user_does_not_read_is_not_named(self):
        # core reads the new parent or project as the user, so its refusal comes first and names neither
        for field_name, value in (("parent_id", self.private_task.id), ("project_id", self.private_project.id)):
            sub_task = self._create_sub_task(self.outsider)
            with self.subTest(field=field_name):
                with self.assertRaises(AccessError) as refusal:
                    sub_task.with_user(self.outsider).write({field_name: value})
                self.assertIn("doesn't have 'read' access", str(refusal.exception))
                self.assertNotIn("Private", str(refusal.exception))

    def test_a_batch_mixing_standard_and_restricted_tasks_is_refused_whole(self):
        for project in self.standard_project | self.public_project:
            self._lead(project, self.editor)
        tasks = self.standard_task | self.public_task
        with self.assertRefused(AccessError, "Only an Admin of Public", in_place=True):
            tasks.with_user(self.editor).write({"user_ids": [Command.set(self.editor.ids)]})
        self.assertFalse(tasks.user_ids, "the Standard task is not written either")
        tasks.with_user(self.editor).write({"stage_id": self.stage_done.id})
        self.assertEqual(tasks.stage_id, self.stage_done)

    def test_a_settings_administrator_creates_a_task_with_assignees(self):
        task = self._create_task(self.private_project, self.sysadmin, user_ids=[Command.set(self.reader.ids)])
        self.assertEqual(task.user_ids, self.reader)

    def test_uat_users_are_changed_by_an_assignee_an_editor_or_an_admin(self):
        task = self._create_task(self.public_project, self.contributor).with_env(self.env)
        task.write({"user_ids": [Command.set(self.reader.ids)], "uat_user_ids": [Command.set(self.outsider.ids)]})
        changes = (
            [Command.link(self.editor.id)],
            [Command.unlink(self.outsider.id)],
            [Command.clear()],
            [Command.set([])],
            False,
            [self.editor.id],
            (self.editor.id,),
        )
        for user in self.contributor | self.outsider:
            for uat_user_ids in changes:
                with (
                    self.subTest(user=user.login, uat_user_ids=uat_user_ids),
                    self.assertRefused(AccessError, "or an Editor or Admin of Public, sets its UAT users"),
                ):
                    task.with_user(user).write({"uat_user_ids": uat_user_ids})
            # the same users, or no command, is no change
            for uat_user_ids in ([Command.set(self.outsider.ids)], []):
                task.with_user(user).write({"uat_user_ids": uat_user_ids, "name": "Same UAT users"})
                self.assertEqual(task.uat_user_ids, self.outsider)
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

    def test_a_refused_create_names_the_task_without_the_key_it_drew(self):
        keyed_env = self.env(context=dict(self.env.context, test_project_key=True))
        members = [Command.create({"user_id": getattr(self, login).id, "role": login}) for login in ROLE_LOGINS]
        keyed_project = keyed_env["project.project"].create(
            {
                "name": "Keyed",
                "key": "ZQMEMBER",
                "access_mode": "public",
                "privacy_visibility": "followers",
                "member_ids": members,
            }
        )
        Task = keyed_env["project.task"].with_context(default_project_id=keyed_project.id)
        refused_creates = {
            "sets its UAT users": {"name": "Tested by a reader", "uat_user_ids": [Command.set(self.reader.ids)]},
            "closes it or reopens it": {"name": "Closed at once", "state": DONE},
        }
        for refusal_end, vals in refused_creates.items():
            with (
                self.subTest(refusal=refusal_end),
                self.assertRefused(AccessError, "Only an assignee of %s, or" % vals["name"], refusal_end),
            ):
                Task.with_user(self.contributor).create(vals)

        task = Task.with_user(self.contributor).create({"name": "Saved"})
        self.assertTrue(task.key.startswith("ZQMEMBER-"), "keys are on, so the refusals had a key to leave out")
        with self.assertRefused(AccessError, "Only an assignee of %s, or" % task.display_name):
            task.with_user(self.contributor).write({"uat_user_ids": [Command.set(self.reader.ids)]})

    def test_moving_a_task_with_uat_users_in_takes_an_editor(self):
        task = self._create_task(self.standard_project, uat_user_ids=[Command.set(self.reader.ids)])
        with self.assertRefused(AccessError, "Only an Editor or Admin of Public moves a task with UAT users"):
            task.with_user(self.contributor).write({"project_id": self.public_project.id})
        task.with_user(self.editor).write({"project_id": self.public_project.id})
        self.assertEqual(task.project_id, self.public_project)

    def test_a_sub_task_with_uat_users_follows_its_parent_in_only_as_the_role_allows(self):
        parent = self._create_task(self.standard_project, self.contributor)
        sub_task = self._create_task(
            self.standard_project, name="Sub-task", parent_id=parent.id, uat_user_ids=[Command.set(self.reader.ids)]
        )
        with self.assertRefused(AccessError, "Only an Editor or Admin of Public moves a task with UAT users"):
            parent.with_user(self.contributor).write({"project_id": self.public_project.id})
        parent.with_user(self.editor).write({"project_id": self.public_project.id})
        self.assertEqual((parent | sub_task).project_id, self.public_project)
        self.assertEqual(sub_task.uat_user_ids, self.reader)

    def test_uat_users_of_a_standard_task_keep_no_role_check(self):
        task = self._create_task(self.standard_project, self.outsider, uat_user_ids=[Command.set(self.reader.ids)])
        task.with_user(self.outsider).write({"uat_user_ids": [Command.set(self.outsider.ids)]})
        self.assertEqual(task.uat_user_ids, self.outsider)

    def _create_stage_of(self, project):
        return self.env["project.task.type"].create(
            {"name": "%s only" % project.name, "project_ids": [Command.link(project.id)]}
        )

    def test_a_task_takes_a_stage_its_project_does_not_list_from_an_admin_only(self):
        standard_stage = self._create_stage_of(self.standard_project)
        for project in self.public_project | self.private_project:
            with (
                self.subTest(project=project.name),
                self.assertRefused(AccessError, "Only an Admin of %s puts a task on a stage" % project.name),
            ):
                self._create_task(project, self.contributor, stage_id=standard_stage.id)
        self._lead(self.private_project, self.reader)
        with self.assertRefused(AccessError, "Only an Admin of Private puts a task on a stage"):
            self.private_reader_task.with_user(self.reader).write({"stage_id": standard_stage.id})
        self._lead(self.private_project, self.editor)
        with self.assertRefused(AccessError, "Only an Admin of Private puts a task on a stage"):
            self.private_task.with_user(self.editor).write({"stage_id": standard_stage.id})

        self._lead(self.private_project, self.admin)
        self.private_task.with_user(self.admin).write({"stage_id": standard_stage.id})
        self.assertEqual(self.private_task.stage_id, standard_stage)
        self.assertEqual(standard_stage.project_ids, self.standard_project, "core adds no stage on a write")
        admin_task = self._create_task(self.public_project, self.admin, stage_id=standard_stage.id)
        self.assertEqual(admin_task.stage_id, standard_stage)
        self.assertIn(self.public_project, standard_stage.project_ids, "core adds the stage of a new task")
        sysadmin_task = self._create_task(self.private_project, self.sysadmin, stage_id=standard_stage.id)
        self.assertEqual(sysadmin_task.stage_id, standard_stage)

    def test_a_stage_a_personal_default_gives_is_judged_where_the_task_lands(self):
        """Core fills a stage the values do not give from the defaults, a user's own included,
        and adds it to the project of the new task.
        """
        standard_stage = self._create_stage_of(self.standard_project)
        for user in self.contributor | self.admin:
            self.env["ir.default"].with_user(user).set("project.task", "stage_id", standard_stage.id, user_id=True)
        for project in self.public_project | self.private_project:
            with (
                self.subTest(project=project.name),
                self.assertRefused(AccessError, "Only an Admin of %s puts a task on a stage" % project.name),
            ):
                self._create_task(project, self.contributor)
        self.assertEqual(self._create_task(self.standard_project, self.contributor).stage_id, standard_stage)
        admin_task = self._create_task(self.public_project, self.admin)
        self.assertEqual(admin_task.stage_id, standard_stage)
        self.assertIn(self.public_project, standard_stage.project_ids)
        self.env["ir.default"].with_user(self.contributor).set(
            "project.task", "stage_id", self.stage_done.id, user_id=True
        )
        self.assertEqual(self._create_task(self.private_project, self.contributor).stage_id, self.stage_done)

    def test_an_archived_stage_the_project_lists_stays_listed(self):
        archived_stage = self._create_stage_of(self.public_project)
        archived_stage.active = False
        task = self._create_task(self.public_project, self.contributor, stage_id=archived_stage.id)
        self.assertEqual(task.stage_id, archived_stage)
        self._lead(self.public_project, self.editor)
        self.public_task.with_user(self.editor).write({"stage_id": archived_stage.id})
        self.assertEqual(self.public_task.stage_id, archived_stage)

    def test_a_listed_stage_and_a_standard_project_keep_no_stage_check(self):
        task = self._create_task(self.public_project, self.contributor, stage_id=self.stage_done.id)
        self.assertEqual(task.stage_id, self.stage_done)
        quick_created_task = (
            self.env["project.task"]
            .with_context(default_project_id=self.public_project.id, default_stage_id=self.stage_done.id)
            .with_user(self.contributor)
            .create({"name": "In the Done column"})
        )
        self.assertEqual(quick_created_task.stage_id, self.stage_done)
        public_stage = self._create_stage_of(self.public_project)
        standard_task = self._create_task(self.standard_project, self.outsider, stage_id=public_stage.id)
        self.assertEqual(standard_task.stage_id, public_stage)
        unlisted_stage = self.env["project.task.type"].create({"name": "Of no project"})
        self._lead(self.standard_project, self.outsider)
        standard_task.with_user(self.outsider).write({"stage_id": unlisted_stage.id})
        self.assertEqual(standard_task.stage_id, unlisted_stage)

    def test_a_task_moved_in_gets_a_stage_of_its_new_project_unless_it_brings_one(self):
        """Core gives a task moved to another project a stage of that project, unless the write names a stage."""
        standard_stage = self._create_stage_of(self.standard_project)
        task = self._create_task(self.standard_project, self.contributor, stage_id=standard_stage.id)
        task.with_user(self.contributor).write({"project_id": self.public_project.id})
        self.assertEqual((task.project_id, task.stage_id), (self.public_project, self.stage_todo))

        task = self._create_task(self.standard_project, self.contributor, stage_id=standard_stage.id)
        with self.assertRefused(AccessError, "Only an Admin of Public puts a task on a stage"):
            task.with_user(self.contributor).write(
                {"project_id": self.public_project.id, "stage_id": standard_stage.id}
            )
        task.with_user(self.admin).write({"project_id": self.public_project.id, "stage_id": standard_stage.id})
        self.assertEqual((task.project_id, task.stage_id), (self.public_project, standard_stage))

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

    def test_a_pull_request_of_a_private_task_is_not_changed_by_its_readers(self):
        grant_pull_request_unlink(self.env)
        pull_request = self.env["project.pull.request"].create(
            {"url": PULL_REQUEST_URL, "task_ids": [Command.set(self.private_task.ids)]}
        )
        as_reader = pull_request.with_user(self.reader)
        self.assertTrue(as_reader.has_access("read"))
        with self.assertRefused(AccessError, "a task you cannot edit", in_place=True):
            as_reader.write({"state": "merged"})
        with self.assertRefused(AccessError, "a task you cannot edit", in_place=True):
            as_reader.unlink()
        self.assertEqual(pull_request.exists().state, "open")

    def test_a_pull_request_of_standard_tasks_is_changed_by_any_project_user(self):
        grant_pull_request_unlink(self.env)
        self.standard_project.privacy_visibility = "followers"
        self.assertFalse(self.standard_task.with_user(self.outsider).has_access("write"), "a task the user cannot edit")
        pull_request = self.env["project.pull.request"].create(
            {"url": PULL_REQUEST_URL, "task_ids": [Command.set(self.standard_task.ids)]}
        )
        pull_request.with_user(self.outsider).write({"state": "merged", "url": OTHER_PULL_REQUEST_URL})
        self.assertEqual((pull_request.state, pull_request.url), ("merged", OTHER_PULL_REQUEST_URL))
        pull_request.with_user(self.outsider).unlink()
        self.assertFalse(pull_request.exists())

    def test_a_task_not_edited_is_not_linked_from_the_pull_request_side(self):
        PullRequest = self.env["project.pull.request"].with_user(self.outsider)
        pull_request = PullRequest.create({"url": PULL_REQUEST_URL})
        # a task the user does not read is refused by core before this module's check
        refusals = {self.public_task: "a task you cannot edit", self.private_task: "doesn't have 'read' access"}
        for task, refusal in refusals.items():
            links = {
                "write": lambda task=task: pull_request.write({"task_ids": [Command.link(task.id)]}),
                "create": lambda task=task: PullRequest.create(
                    {"url": OTHER_PULL_REQUEST_URL, "task_ids": [Command.link(task.id)]}
                ),
            }
            for link_name, link in links.items():
                with self.subTest(project=task.project_id.name, link=link_name):
                    with self.assertRefused(AccessError, refusal):
                        link()

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
                self.assertEqual(set(own_task.sudo().pull_request_ids.mapped("url")), {PULL_REQUEST_URL, new_url})
                self.assertIn(own_task, private_pull_request.task_ids)


@tagged("post_install", "-at_install")
class TestRefusalsNameNothingUnread(MemberAccessCommon):
    """A user refused by the access rows learns nothing of the records refused."""

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

    def test_a_stage_or_state_written_on_a_task_not_written(self):
        for user in self.portal | self.outsider | self.employee | self.manager:
            for vals in ({"stage_id": self.stage_done.id}, {"state": DONE}):
                with self.subTest(user=user.login, field=next(iter(vals))):
                    self.assertNamesNothing(
                        AccessError, lambda user=user, vals=vals: self.secret_task.with_user(user).write(vals)
                    )

    def test_a_member_row_not_written(self):
        admin_row = self.secret_project.member_ids
        self.assertNamesNothing(AccessError, lambda: admin_row.with_user(self.outsider).write({"role": "editor"}))
        self.assertNamesNothing(AccessError, lambda: admin_row.with_user(self.outsider).unlink())

    def test_a_member_row_not_created(self):
        followers_project = self.env["project.project"].create(
            {"name": "Secret Followers", "privacy_visibility": "followers"}
        )
        self.assertFalse(followers_project.with_user(self.outsider).has_access("read"))
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
        # by its parent, written by id
        parent = self._create_task(self.standard_project)
        sub_task = self.env["project.task"].create(
            {"name": "Planted", "parent_id": parent.id, "user_ids": [Command.set(self.outsider.ids)]}
        )
        self.assertNamesNothing(
            UserError, lambda: sub_task.with_user(self.outsider).write({"parent_id": self.secret_task.id})
        )

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
