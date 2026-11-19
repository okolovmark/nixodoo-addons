from odoo.tests import tagged

from .common import ProjectAiDrivenCommon

KEY_PREFIX = "ZR"


@tagged("post_install", "-at_install")
class TestProjectKeyRename(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, test_project_key=True))
        cls.Project = cls.env["project.project"]
        cls.Task = cls.env["project.task"]

    def setUp(self):
        super().setUp()
        self.assertFalse(
            self.Project.with_context(active_test=False).search_count([("key", "=like", KEY_PREFIX + "%")]),
            "the fixture keys starting with %s must be free on this database" % KEY_PREFIX,
        )
        # Per test: a sequence draws its numbers outside the transaction, a test rolled back spends them.
        self.key_project = self.Project.create({"name": "Key Rename", "key": "ZRK"})
        self.other_project = self.Project.create({"name": "Key Rename Other", "key": "ZRO"})
        self.parent_task = self.Task.create({"name": "Parent", "project_id": self.key_project.id})
        self.sub_task = self.Task.create(
            {"name": "Sub-task", "parent_id": self.parent_task.id, "project_id": self.key_project.id}
        )
        self.env.flush_all()

    def test_a_new_project_key_renames_the_tasks_of_the_project_whatever_their_prefix(self):
        foreign_task = self.Task.create({"name": "Foreign", "project_id": self.other_project.id})
        stray_task = self.Task.create({"name": "Stray", "project_id": self.key_project.id})
        foreign_task.key = "ZRK-7"
        stray_task.key = "ZRSTRAY-9"
        self.env.flush_all()

        self.key_project.key = "ZRNEW"

        self.assertEqual(
            (self.parent_task | self.sub_task | stray_task).mapped("key"),
            ["ZRNEW-1", "ZRNEW-2", "ZRNEW-9"],
            "every task of the project takes the new key, the one with another prefix included",
        )
        self.assertEqual(foreign_task.key, "ZRK-7", "a task of another project keeps the former prefix")

    def test_a_new_project_key_renames_a_task_key_written_just_before(self):
        self.parent_task.key = "ZRK-41"

        self.key_project.key = "ZRNEW"

        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZRNEW-41", "ZRNEW-2"])

    def test_keyless_tasks_are_numbered_with_a_key_cleared_just_before(self):
        self.sub_task.key = False

        self.key_project._number_keyless_tasks()

        self.assertEqual(self.sub_task.key, "ZRK-3")
        self.assertEqual(self.Task.create({"name": "Next", "project_id": self.key_project.id}).key, "ZRK-4")

    def test_sub_tasks_archived_or_given_their_parent_just_before_it_moves_go_along_with_keys(self):
        late_sub_task = self.Task.create({"name": "Late sub-task", "project_id": self.key_project.id})
        late_sub_task.parent_id = self.parent_task
        self.sub_task.active = False

        self.parent_task.project_id = self.other_project

        moved_tasks = self.parent_task | self.sub_task | late_sub_task
        self.assertEqual(moved_tasks.project_id, self.other_project)
        self.assertEqual(moved_tasks.mapped("key"), ["ZRO-1", "ZRO-2", "ZRO-3"])
