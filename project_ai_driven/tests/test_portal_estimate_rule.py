from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

from .common import NOT_AI_DRIVEN, SHARING_FORM_VIEW, ProjectAiDrivenCommon


@tagged("post_install", "-at_install")
class TestPortalTaskMove(ProjectAiDrivenCommon):
    """A sub-task that does not show in a project of its own follows the
    project of its parent, which a portal user may change: cls.project and
    project_c are AI-driven, project_b and project_d are not.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project.privacy_visibility = "portal"
        cls.project_c, cls.project_b, cls.project_d = cls.env["project.project"].create(
            [
                {"name": "Shared, AI-driven", "is_ai_driven": True},
                {"name": "Shared, not AI-driven"},
                {"name": "Shared, not AI-driven either"},
            ]
        )
        (cls.project_c | cls.project_b | cls.project_d).privacy_visibility = "portal"
        cls.user_portal = cls._create_collaborator(
            "project_ai_driven_move_edit", "edit", cls.project | cls.project_b | cls.project_c | cls.project_d
        )
        cls.task_a2, cls.task_b, cls.task_b2, cls.task_c, cls.task_d = cls.env["project.task"].create(
            [
                {"name": "Second AI-driven Task", "project_id": cls.project.id},
                {"name": "Plain Task", "project_id": cls.project_b.id},
                {"name": "Second Plain Task", "project_id": cls.project_b.id},
                {"name": "Other AI-driven Task", "project_id": cls.project_c.id},
                {"name": "Other Plain Task", "project_id": cls.project_d.id},
            ]
        )

    def _create_subtask(self, parent, name="Sub-task", **values):
        subtask = self.env["project.task"].create({"name": name, "parent_id": parent.id, **values})
        self.assertRecordValues(subtask, [{"project_id": parent.project_id.id, "display_in_project": False}])
        return subtask

    def _move(self, task, parent):
        task.with_user(self.user_portal).write({"parent_id": parent.id})

    def assertRefusedMove(self, refusal, tasks):
        self.assertEqual(
            refusal.exception.args[0], "%s.\nTasks: %s" % (NOT_AI_DRIVEN, ", ".join(tasks.mapped("display_name")))
        )

    def test_a_sub_task_with_a_type_or_an_estimate_does_not_leave_an_ai_driven_project_by_its_parent(self):
        for vals in ({"task_type": "bug"}, {"allocated_hours": 2}):
            with self.subTest(vals=vals):
                subtask = self._create_subtask(self.task)
                subtask.with_user(self.user_portal).write(vals)
                with self.assertRaises(AccessError) as refusal:
                    self._move(subtask, self.task_b)
                self.assertRefusedMove(refusal, subtask)

    def test_a_sub_task_with_an_estimate_is_not_linked_to_a_task_outside_an_ai_driven_project(self):
        task_model = self.env["project.task"].with_user(self.user_portal)
        linking_calls = {
            "write of a task": lambda link: self.task_b.with_user(self.user_portal).write({"child_ids": link}),
            "create of a task": lambda link: task_model.with_context(default_project_id=self.project_b.id).create(
                {"name": "New Parent", "child_ids": link}
            ),
        }
        for call_name, linking_call in linking_calls.items():
            with self.subTest(call=call_name):
                subtask = self._create_subtask(self.task, allocated_hours=2)
                with self.assertRaises(AccessError) as refusal:
                    linking_call([Command.link(subtask.id)])
                self.assertRefusedMove(refusal, subtask)

    def test_a_sub_task_with_an_estimate_set_as_the_sub_tasks_of_a_task_outside_an_ai_driven_project_is_refused(self):
        subtask = self._create_subtask(self.task, allocated_hours=2)
        with self.assertRaises(AccessError) as refusal:
            self.task_b.with_user(self.user_portal).write({"child_ids": [Command.set([subtask.id])]})
        self.assertRefusedMove(refusal, subtask)

    def test_a_sub_task_with_an_estimate_given_a_new_parent_through_its_old_one_is_refused(self):
        subtask = self._create_subtask(self.task, allocated_hours=2)
        with self.assertRaises(AccessError) as refusal:
            self.task.with_user(self.user_portal).write(
                {"child_ids": [Command.update(subtask.id, {"parent_id": self.task_b.id})]}
            )
        self.assertRefusedMove(refusal, subtask)

    def test_a_move_of_several_sub_tasks_names_those_with_an_estimate(self):
        subtask_with_estimate = self._create_subtask(self.task, name="Sub-task with an Estimate", allocated_hours=2)
        subtask = self._create_subtask(self.task)
        with self.assertRaises(AccessError) as refusal:
            (subtask_with_estimate | subtask).with_user(self.user_portal).write({"parent_id": self.task_b.id})
        self.assertRefusedMove(refusal, subtask_with_estimate)

    def test_a_sub_task_moved_along_with_its_parent_is_refused_with_an_estimate(self):
        subtask = self._create_subtask(self.task)
        nested_subtask = self._create_subtask(subtask, name="Nested Sub-task", allocated_hours=2)
        with self.assertRaises(AccessError) as refusal:
            self._move(subtask, self.task_b)
        self.assertRefusedMove(refusal, nested_subtask)

    def test_an_archived_sub_task_with_an_estimate_refuses_the_move_without_its_name(self):
        subtask = self._create_subtask(self.task)
        archived_subtask = self._create_subtask(subtask, name="Archived Secret Nested", allocated_hours=2, active=False)
        self.env.invalidate_all()
        with self.assertRaises(AccessError) as refusal:
            self._move(subtask, self.task_b)
        self.assertEqual(
            refusal.exception.args[0],
            "%s.\nSub-tasks under %s that you cannot see: 1" % (NOT_AI_DRIVEN, subtask.display_name),
        )
        with self.assertRaises(AccessError):
            archived_subtask.with_user(self.user_portal).read(["name"])

    def test_a_move_refused_by_a_readable_and_a_hidden_sub_task_names_the_readable_one(self):
        subtask = self._create_subtask(self.task)
        nested_subtask = self._create_subtask(subtask, name="Nested Sub-task", allocated_hours=2)
        self._create_subtask(subtask, name="Archived Secret Nested", task_type="bug", active=False)
        self.env.invalidate_all()
        with self.assertRaises(AccessError) as refusal:
            self._move(subtask, self.task_b)
        self.assertEqual(
            refusal.exception.args[0],
            "%s.\nTasks: %s\nSub-tasks under %s that you cannot see: 1"
            % (NOT_AI_DRIVEN, nested_subtask.display_name, subtask.display_name),
        )

    def test_a_move_out_of_the_user_s_reach_names_the_sub_tasks_they_read_before_it(self):
        project_followed = self.env["project.project"].create(
            {"name": "Followed, not shared", "privacy_visibility": "portal"}
        )
        followed_task = self.env["project.task"].create({"name": "Followed Task", "project_id": project_followed.id})
        followed_task.message_subscribe(partner_ids=self.user_portal.partner_id.ids)
        subtask = self._create_subtask(self.task, allocated_hours=2)
        nested_subtask = self._create_subtask(subtask, name="Nested Sub-task", task_type="bug")
        with self.assertRaises(AccessError) as refusal:
            self._move(subtask, followed_task)
        self.assertRefusedMove(refusal, (subtask | nested_subtask).sorted())

    def test_a_sub_task_hidden_before_the_move_stays_unnamed_though_the_move_shows_it(self):
        subtask = self._create_subtask(self.task)
        self._create_subtask(subtask, name="Secret Nested", allocated_hours=2)
        self._restrict_task_access(
            "Hide the secret task in its AI-driven project",
            "['|', ('project_id', '!=', %s), ('name', '!=', 'Secret Nested')]" % self.project.id,
            operation="r",
        )
        with self.assertRaises(AccessError) as refusal:
            self._move(subtask, self.task_b)
        self.assertEqual(
            refusal.exception.args[0],
            "%s.\nSub-tasks under %s that you cannot see: 1" % (NOT_AI_DRIVEN, subtask.display_name),
        )

    def test_a_move_of_several_sub_tasks_counts_the_hidden_ones_under_the_task_that_holds_them(self):
        subtask = self._create_subtask(self.task, name="Sub-task with a Hidden One")
        self._create_subtask(subtask, name="Archived Secret Nested", allocated_hours=2, active=False)
        other_subtask = self._create_subtask(self.task, name="Other Sub-task")
        with self.assertRaises(AccessError) as refusal:
            (subtask | other_subtask).with_user(self.user_portal).write({"parent_id": self.task_b.id})
        self.assertEqual(
            refusal.exception.args[0],
            "%s.\nSub-tasks under %s that you cannot see: 1" % (NOT_AI_DRIVEN, subtask.display_name),
        )

    def test_a_sub_task_with_an_estimate_freed_of_its_parent_stays_in_its_ai_driven_project(self):
        subtask = self._create_subtask(self.task, allocated_hours=2)
        subtask.with_user(self.user_portal).write({"parent_id": False})
        self.assertRecordValues(
            subtask, [{"parent_id": False, "project_id": self.project.id, "display_in_project": True}]
        )

    def test_a_sub_task_with_an_estimate_moves_inside_a_project_not_ai_driven(self):
        subtask = self._create_subtask(self.task_b, task_type="bug", allocated_hours=2)
        self._move(subtask, self.task_b2)
        self.assertRecordValues(
            subtask,
            [{"parent_id": self.task_b2.id, "project_id": self.project_b.id, "task_type": "bug", "allocated_hours": 2}],
        )

    def test_a_sub_task_with_an_estimate_moves_between_projects_not_ai_driven(self):
        subtask = self._create_subtask(self.task_b, task_type="bug", allocated_hours=2)
        self._move(subtask, self.task_d)
        self.assertRecordValues(
            subtask,
            [{"parent_id": self.task_d.id, "project_id": self.project_d.id, "task_type": "bug", "allocated_hours": 2}],
        )

    def test_a_sub_task_without_a_type_or_an_estimate_leaves_an_ai_driven_project(self):
        subtask = self._create_subtask(self.task)
        self._move(subtask, self.task_b)
        self.assertRecordValues(subtask, [{"parent_id": self.task_b.id, "project_id": self.project_b.id}])

    def test_a_sub_task_with_an_estimate_moves_inside_and_between_ai_driven_projects(self):
        subtask = self._create_subtask(self.task, allocated_hours=2)
        for parent in (self.task_a2, self.task_c):
            self._move(subtask, parent)
            self.assertRecordValues(
                subtask, [{"parent_id": parent.id, "project_id": parent.project_id.id, "allocated_hours": 2}]
            )

    def test_a_task_shown_in_its_ai_driven_project_stays_there_under_a_parent_of_another(self):
        task = self.env["project.task"].create(
            {"name": "Shown Task", "project_id": self.project.id, "task_type": "bug", "allocated_hours": 2}
        )
        # Computed before the move, as a save would: core reads a flag not yet computed from the database.
        task.flush_recordset(["display_in_project"])
        self._move(task, self.task_b)
        self.assertRecordValues(
            task,
            [{"parent_id": self.task_b.id, "project_id": self.project.id, "display_in_project": True}],
        )

    def test_a_new_parent_written_with_an_estimate_is_refused_by_the_check_of_the_fields(self):
        """A pin of the combined write, not of the move rule: the check of the
        fields refuses an Estimate written on a task whose project, once saved,
        is not AI-driven, before the moves are checked.
        """
        subtask = self._create_subtask(self.task)
        with self.assertRaises(AccessError) as refusal:
            subtask.with_user(self.user_portal).write({"parent_id": self.task_b.id, "allocated_hours": 3})
        self.assertRefusedMove(refusal, subtask)

    def test_an_internal_user_moves_a_sub_task_with_an_estimate_out_of_an_ai_driven_project(self):
        subtask = self._create_subtask(self.task, allocated_hours=2)
        subtask.with_user(self.user_dev).write({"parent_id": self.task_b.id})
        self.assertRecordValues(subtask, [{"project_id": self.project_b.id, "allocated_hours": 2}])


@tagged("post_install", "-at_install")
class TestPortalEstimateFieldViews(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_portal = new_test_user(
            cls.env,
            "project_ai_driven_views_portal",
            groups="base.group_portal",
            context={"no_reset_password": True},
        )
        cls.user_internal = new_test_user(
            cls.env,
            "project_ai_driven_views_internal",
            groups="base.group_user,project.group_project_user",
            context={"no_reset_password": True},
        )

    def _render_in_both_orders(self, render):
        """Return what render(user) gives in the order portal, internal, then
        internal, portal, the view cache cleared before each order.
        """
        renders = []
        for users in ((self.user_portal, self.user_internal), (self.user_internal, self.user_portal)):
            self.env.transaction.invalidate_ormcache("templates")
            renders += [(user, render(user)) for user in users]
        return renders

    def test_a_view_of_another_model_embeds_one_task_list_for_every_user(self):
        """Core embeds the list of an x2many that has none through the task's
        _get_view, and caches it under the key of the other model, which tells
        no portal user apart.
        """
        project_view = self.env["ir.ui.view"].create(
            {
                "name": "project.project.view.form.tasks",
                "model": "project.project",
                "type": "form",
                "arch": """<form><field name="task_ids" context="{'list_view_ref': 'project.view_task_tree2'}"/></form>""",
            }
        )

        def render_task_list(user):
            arch = self.env["project.project"].with_user(user)._get_view_cache(project_view.id, "form")["arch"]
            return etree.tostring(etree.fromstring(arch).find("field[@name='task_ids']/list"), encoding="unicode")

        task_lists = [task_list for _user, task_list in self._render_in_both_orders(render_task_list)]
        self.assertIn('name="task_type"', task_lists[0])
        self.assertEqual(task_lists, task_lists[:1] * 4)

    def test_the_task_s_own_views_keep_the_estimate_read_only_for_the_portal_alone(self):
        """In the form of project sharing and in its list of sub-tasks: core
        caches the views of the task apart for the portal.
        """
        sharing_form = self.env.ref(SHARING_FORM_VIEW)
        self.env["ir.ui.view"].create(
            {
                "name": "project.task.view.form.project_sharing.estimate",
                "model": "project.task",
                "inherit_id": sharing_form.id,
                "arch": """
                    <data>
                        <field name="child_ids" position="before"><field name="task_type"/></field>
                        <xpath expr="//field[@name='child_ids']/list/field[@name='name']" position="after">
                            <field name="allocated_hours"/>
                        </xpath>
                    </data>
                """,
            }
        )

        def render_readonly(user):
            arch = etree.fromstring(self.env["project.task"].with_user(user).get_view(sharing_form.id, "form")["arch"])
            return [
                arch.find(".//field[@name='task_type']").get("readonly"),
                arch.find(".//field[@name='child_ids']/list/field[@name='allocated_hours']").get("readonly"),
            ]

        for user, readonly in self._render_in_both_orders(render_readonly):
            with self.subTest(user=user.login):
                self.assertEqual(readonly, ["not is_ai_driven"] * 2 if user == self.user_portal else [None] * 2)
