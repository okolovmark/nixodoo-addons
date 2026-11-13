from datetime import datetime
from itertools import pairwise
from unittest.mock import patch

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import tagged

from .common import ProjectAiDrivenCommon

QUIET_CONTEXTS = (
    {"tracking_disable": True},
    {"mail_notrack": True},
    {"mail_create_nolog": True},
    {"tracking_disable": True, "mail_notrack": True, "mail_create_nolog": True, "mail_create_nosubscribe": True},
)


@tagged("post_install", "-at_install")
class TestStageLog(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.Log = cls.env["project.task.stage.log"]
        cls.no_stage = cls.env["project.task.type"]
        cls.stage_new = cls._create_stage("New", 1)
        cls.stage_progress = cls._create_stage("In Progress", 2)
        cls.stage_done = cls._create_stage("Done", 3, fold=True)
        cls.other_project = cls.env["project.project"].create({"name": "Other Project", "type_ids": [Command.clear()]})
        cls.other_stage = cls._create_stage("Inbox", 1, project=cls.other_project)
        cls.other_stage_late = cls._create_stage("Shipped", 2, project=cls.other_project)

    def _create_task(self, **values):
        return self.Task.create({"name": "Task", "project_id": self.project.id, **values})

    def _rows(self, tasks):
        # a row is written with the column, when the change is flushed
        self.env.flush_all()
        # by id, the order they were written in: their dates are the clock's, which a test may freeze
        return self.Log.search([("task_id", "in", tasks.ids)], order="id")

    def _write_pending_stage(self, task, stage):
        """Leave stage on task as a recomputation does: in cache, not yet in
        the database.
        """
        self.Task._fields["stage_id"].write(task, stage)

    def _flush_in_one_update(self, tasks):
        """Flush, and check what the test stands on: the pending changes of
        tasks reach the database together, in one call of _write_multi.
        """
        model_cls = self.registry["project.task"]
        write_multi = model_cls._write_multi
        updated_ids = []

        def spy_write_multi(records, vals_list):
            updated_ids.append(set(records.ids))
            return write_multi(records, vals_list)

        with patch.object(model_cls, "_write_multi", spy_write_multi):
            self.env.flush_all()
        self.assertTrue(any(set(tasks.ids) <= ids for ids in updated_ids), "the tasks were not updated together")

    def assertMoves(self, task, *moves):
        """The rows of task are these (previous stage, stage) pairs, in order."""
        self.assertEqual([(row.previous_stage_id, row.stage_id) for row in self._rows(task)], list(moves))

    def assertChain(self, task, first_stage, last_stage):
        """The rows of task lead from first_stage to last_stage: each one is a
        change and starts where the one before ended.
        """
        rows = self._rows(task)
        self.assertEqual((rows[0].previous_stage_id, rows[-1].stage_id), (first_stage, last_stage))
        for row in rows:
            self.assertNotEqual(row.previous_stage_id, row.stage_id, "a row that changes nothing")
        for row, next_row in pairwise(rows):
            self.assertEqual(next_row.previous_stage_id, row.stage_id, "two rows for one change")

    def test_create_with_the_default_stage_of_the_project(self):
        with freeze_time("2030-01-05 10:00:00"):
            task = self._create_task()
            rows = self._rows(task)
        self.assertRecordValues(
            rows,
            [
                {
                    "task_id": task.id,
                    "previous_stage_id": False,
                    "stage_id": self.stage_new.id,
                    "stage_name": "New",
                    "date": datetime(2030, 1, 5, 10),
                    "user_id": self.env.uid,
                    "source": "live",
                }
            ],
        )

    def test_create_with_an_explicit_stage(self):
        task = self._create_task(stage_id=self.stage_progress.id)
        self.assertMoves(task, (self.no_stage, self.stage_progress))

    def test_create_without_a_stage_logs_nothing(self):
        private_task = self.Task.create({"name": "Private Task", "user_ids": [Command.link(self.user_dev.id)]})
        stageless_task = self._create_task(stage_id=False)
        self.assertFalse(private_task.stage_id | stageless_task.stage_id)
        self.assertFalse(self._rows(private_task | stageless_task | self.task))

    def test_first_stage_of_a_task_created_without_one(self):
        task = self._create_task(stage_id=False)
        task.stage_id = self.stage_progress
        self.assertMoves(task, (self.no_stage, self.stage_progress))
        self.assertEqual(self._rows(task).stage_name, "In Progress")

    def test_first_move_of_a_task_older_than_the_log(self):
        task = self._create_task()
        self.env.flush_all()
        # as on a database that got the log after the task: the past is not filled
        self.env.cr.execute("DELETE FROM project_task_stage_log WHERE task_id = %s", [task.id])

        task.stage_id = self.stage_progress

        self.assertMoves(task, (self.stage_new, self.stage_progress))

    def test_create_without_a_project_then_a_project(self):
        task = self.Task.create({"name": "Private Task", "user_ids": [Command.link(self.user_dev.id)]})
        task.project_id = self.project
        self.assertMoves(task, (self.no_stage, self.stage_new))

    def test_every_change_of_stage_is_one_row(self):
        task = self._create_task()
        task.stage_id = self.stage_progress
        self.env.flush_all()
        task.write({"stage_id": self.stage_done.id, "name": "Renamed"})
        self.assertMoves(
            task,
            (self.no_stage, self.stage_new),
            (self.stage_new, self.stage_progress),
            (self.stage_progress, self.stage_done),
        )
        self.assertEqual(self._rows(task).mapped("stage_name"), ["New", "In Progress", "Done"])

    def test_create_and_write_in_one_transaction(self):
        task = self._create_task()
        task.stage_id = self.stage_progress
        self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.stage_progress))

    def test_write_that_keeps_the_stage_logs_nothing(self):
        task = self._create_task()
        task.write({"stage_id": self.stage_new.id})
        self.env.flush_all()
        task.write({"stage_id": self.stage_new.id, "name": "Renamed"})
        task.name = "Renamed again"
        task.state = "1_done"
        self.assertMoves(task, (self.no_stage, self.stage_new))

    def test_update_with_the_stored_stage_logs_nothing(self):
        """The column can be written with the stage it holds, by a write on a
        task whose stage is not in cache or after a move there and back. The
        update is called as a flush calls it.
        """
        kept_task = self._create_task()
        moved_task = self._create_task(stage_id=self.stage_progress.id)
        self.env.flush_all()

        (kept_task | moved_task)._write_multi([{"stage_id": self.stage_new.id}, {"stage_id": self.stage_new.id}])
        (kept_task | moved_task).invalidate_recordset(["stage_id"])

        self.assertMoves(kept_task, (self.no_stage, self.stage_new))
        self.assertMoves(moved_task, (self.no_stage, self.stage_progress), (self.stage_progress, self.stage_new))

    def test_task_that_loses_its_stage(self):
        task = self._create_task()
        task.write({"project_id": False, "user_ids": [Command.link(self.user_dev.id)]})
        self.assertFalse(task.stage_id)
        self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.no_stage))
        self.assertFalse(self._rows(task)[-1].stage_name)

    def test_stage_written_empty(self):
        task = self._create_task()
        task.write({"stage_id": False})
        self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.no_stage))
        self.assertFalse(self._rows(task)[-1].stage_name)

    def test_changes_within_one_flush_are_one_row(self):
        task = self._create_task()
        self.env.flush_all()
        self._write_pending_stage(task, self.stage_progress)
        self._write_pending_stage(task, self.stage_done)
        self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.stage_done))

    def test_change_undone_within_one_flush_leaves_no_row(self):
        task = self._create_task()
        self.env.flush_all()
        self._write_pending_stage(task, self.stage_progress)
        self._write_pending_stage(task, self.stage_new)
        self.assertMoves(task, (self.no_stage, self.stage_new))

    def test_one_flush_that_moves_tasks_to_different_stages(self):
        to_progress = self._create_task(name="To In Progress")
        to_done = self._create_task(name="To Done", stage_id=self.stage_progress.id)
        also_to_done = self._create_task(name="Also To Done")
        to_no_stage = self._create_task(name="To No Stage", stage_id=self.stage_done.id)
        self.env.flush_all()

        self._write_pending_stage(to_progress, self.stage_progress)
        self._write_pending_stage(to_done, self.stage_done)
        self._write_pending_stage(also_to_done, self.stage_done)
        self._write_pending_stage(to_no_stage, self.no_stage)
        self._flush_in_one_update(to_progress | to_done | also_to_done | to_no_stage)

        self.assertMoves(to_progress, (self.no_stage, self.stage_new), (self.stage_new, self.stage_progress))
        self.assertMoves(to_done, (self.no_stage, self.stage_progress), (self.stage_progress, self.stage_done))
        self.assertMoves(also_to_done, (self.no_stage, self.stage_new), (self.stage_new, self.stage_done))
        self.assertMoves(to_no_stage, (self.no_stage, self.stage_done), (self.stage_done, self.no_stage))

    def test_one_flush_where_only_some_tasks_change_stage(self):
        moved_task = self._create_task(name="Moved")
        renamed_task = self._create_task(name="Renamed")
        self.env.flush_all()

        self._write_pending_stage(moved_task, self.stage_progress)
        self.Task._fields["name"].write(renamed_task, "Renamed Again")
        self._flush_in_one_update(moved_task | renamed_task)

        self.assertMoves(moved_task, (self.no_stage, self.stage_new), (self.stage_new, self.stage_progress))
        self.assertMoves(renamed_task, (self.no_stage, self.stage_new))

    def test_one_flush_that_carries_different_users(self):
        by_lead = self._create_task(name="By Lead")
        by_dev = self._create_task(name="By Dev")
        recomputed = self._create_task(name="Recomputed")
        self.env.flush_all()

        by_lead.with_user(self.user_lead).stage_id = self.stage_progress
        by_dev.with_user(self.user_dev).stage_id = self.stage_progress
        self._write_pending_stage(recomputed, self.stage_progress)
        self._flush_in_one_update(by_lead | by_dev | recomputed)

        self.assertEqual(
            [self._rows(task)[-1].user_id for task in (by_lead, by_dev, recomputed)],
            [self.user_lead, self.user_dev, self.env.user],
        )

    def test_more_tasks_than_one_statement_takes(self):
        with patch.object(type(self.env.cr), "IN_MAX", 2):
            tasks = self.Task.create([{"name": f"Task {number}", "project_id": self.project.id} for number in range(5)])
            self.env.flush_all()
            tasks.stage_id = self.stage_progress
            self._flush_in_one_update(tasks)

        for task in tasks:
            self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.stage_progress))

    def test_search_sees_a_stage_change_that_is_pending(self):
        task = self._create_task()
        self.env.flush_all()
        domain = [("task_id", "=", task.id)]

        task.stage_id = self.stage_progress
        self.assertEqual(self.Log.search_count(domain), 2)

        self._write_pending_stage(task, self.stage_done)
        self.assertEqual(
            [row.stage_id for row in self.Log.search(domain, order="id")],
            [self.stage_new, self.stage_progress, self.stage_done],
        )

        # the stage waits for its recomputation: nothing has read it since the project changed
        self.Task._fields["project_id"].write(task, self.other_project)
        self.env.add_to_compute(self.Task._fields["stage_id"], task)
        self.assertEqual(self.Log.search(domain, order="id")[-1].stage_id, self.other_stage)

    def test_no_context_and_no_sudo_goes_around_the_log(self):
        as_lead = self.Task.with_user(self.user_lead)
        for context in QUIET_CONTEXTS:
            for Task in (self.Task, as_lead, as_lead.sudo()):
                task = Task.with_context(**context).create({"name": "Quiet Task", "project_id": self.project.id})
                task.stage_id = self.stage_progress
                # in the environment that wrote, as a request flushes
                task.flush_recordset()
                self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.stage_progress))

    def test_move_without_tracking_is_logged(self):
        task = self._create_task()
        self._flush_tracking()

        task.with_context(tracking_disable=True).stage_id = self.stage_progress
        self.env.flush_all()
        task.with_context(mail_notrack=True).stage_id = self.stage_done
        self.assertFalse(self._tracking_texts(task))
        task.stage_id = self.stage_new

        self.assertEqual(len(self._tracking_texts(task)), 1)
        self.assertMoves(
            task,
            (self.no_stage, self.stage_new),
            (self.stage_new, self.stage_progress),
            (self.stage_progress, self.stage_done),
            (self.stage_done, self.stage_new),
        )

    def test_user_is_who_acts_also_under_sudo(self):
        as_lead = self.env(user=self.user_lead)
        task = as_lead["project.task"].create({"name": "Task of the Lead", "project_id": self.project.id})
        task.stage_id = self.stage_progress
        # a request flushes in its own environment
        as_lead.flush_all()
        task.sudo().with_context(tracking_disable=True).stage_id = self.stage_done
        as_lead.flush_all()
        self.assertEqual([row.user_id for row in self._rows(task)], [self.user_lead] * 3)

    def test_user_is_who_wrote_whoever_flushes(self):
        task = self._create_task()
        task.with_user(self.user_lead).stage_id = self.stage_progress
        self.assertEqual([row.user_id for row in self._rows(task)], [self.env.user, self.user_lead])

    def test_user_of_a_change_nobody_wrote_is_who_flushes(self):
        task = self._create_task()
        self.env.flush_all()

        self._write_pending_stage(task, self.stage_progress)
        self.env(user=self.user_lead).flush_all()

        task.invalidate_recordset(["write_uid"])
        self.assertEqual((self._rows(task)[-1].user_id, task.write_uid), (self.user_lead, self.user_lead))

    def test_project_change_recomputes_the_stage(self):
        task = self._create_task()
        task.project_id = self.other_project
        self.assertEqual(task.stage_id, self.other_stage)
        self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.other_stage))

    def test_project_change_that_keeps_the_stage_logs_nothing(self):
        self.stage_new.project_ids = [Command.link(self.other_project.id)]
        task = self._create_task()
        task.project_id = self.other_project
        self.assertMoves(task, (self.no_stage, self.stage_new))

    def test_subtask_moved_with_its_parent(self):
        parent = self._create_task(name="Parent")
        subtask = self._create_task(name="Subtask", parent_id=parent.id)
        self.env.flush_all()

        parent.project_id = self.other_project

        self.assertEqual(subtask.stage_id, self.other_stage)
        for task in parent | subtask:
            self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.other_stage))

    def test_project_written_on_its_own_before_the_stage(self):
        """A module may write the new project of a task first and the other
        values of the same write after it: one stage change or two, never a
        row twice.
        """
        task = self._create_task()
        self.env.flush_all()
        task.write({"project_id": self.other_project.id})
        task.write({"project_id": self.other_project.id, "stage_id": self.other_stage_late.id})
        self.assertChain(task, self.no_stage, self.other_stage_late)
        self.assertIn(len(self._rows(task)), (2, 3))

        flushed_task = self._create_task()
        flushed_task.write({"project_id": self.other_project.id})
        self.env.flush_all()
        flushed_task.write({"project_id": self.other_project.id, "stage_id": self.other_stage_late.id})
        self.assertMoves(
            flushed_task,
            (self.no_stage, self.stage_new),
            (self.stage_new, self.other_stage),
            (self.other_stage, self.other_stage_late),
        )

    def test_project_and_stage_in_one_write(self):
        task = self._create_task()
        task.write({"project_id": self.other_project.id, "stage_id": self.other_stage_late.id})
        self.assertChain(task, self.no_stage, self.other_stage_late)
        self.assertIn(len(self._rows(task)), (2, 3))

    def test_copy(self):
        task = self._create_task(stage_id=self.stage_done.id)
        plain_copy = task.copy()
        other_stage_copy = task.copy({"stage_id": self.stage_progress.id})
        self.assertMoves(plain_copy, (self.no_stage, self.stage_done))
        self.assertMoves(other_stage_copy, (self.no_stage, self.stage_progress))
        self.assertMoves(task, (self.no_stage, self.stage_done))

    def test_copy_of_several_tasks(self):
        tasks = self._create_task(stage_id=self.stage_done.id) | self._create_task() | self._create_task(stage_id=False)
        done_copy, new_copy, stageless_copy = tasks.copy()
        self.assertMoves(done_copy, (self.no_stage, self.stage_done))
        self.assertMoves(new_copy, (self.no_stage, self.stage_new))
        self.assertFalse(self._rows(stageless_copy))

    def test_duplicated_project(self):
        parent = self._create_task(name="Parent", stage_id=self.stage_progress.id)
        self._create_task(name="Subtask", parent_id=parent.id, stage_id=self.stage_done.id)
        self._create_task(name="Archived", active=False)
        self.assertFalse(self.task.stage_id)

        duplicated_project = self.project.copy()

        duplicated_tasks = self.Task.with_context(active_test=False).search(
            [("project_id", "=", duplicated_project.id)]
        )
        self.assertEqual(
            sorted(duplicated_tasks.mapped(lambda task: (task.name, task.stage_id.name))),
            [("AI-driven Task", False), ("Archived", "New"), ("Parent", "In Progress"), ("Subtask", "Done")],
        )
        staged_tasks = duplicated_tasks.filtered("stage_id")
        for task in staged_tasks:
            self.assertMoves(task, (self.no_stage, task.stage_id))
        self.assertFalse(self._rows(duplicated_tasks - staged_tasks))

    def test_batch_create(self):
        tasks = self.Task.create(
            [
                {"name": "Default Stage", "project_id": self.project.id},
                {"name": "Explicit Stage", "project_id": self.project.id, "stage_id": self.stage_done.id},
                {"name": "No Stage", "project_id": self.project.id, "stage_id": False},
                {"name": "Other Project", "project_id": self.other_project.id},
            ]
        )
        self.assertRecordValues(
            self._rows(tasks),
            [
                {"task_id": tasks[0].id, "previous_stage_id": False, "stage_id": self.stage_new.id},
                {"task_id": tasks[1].id, "previous_stage_id": False, "stage_id": self.stage_done.id},
                {"task_id": tasks[3].id, "previous_stage_id": False, "stage_id": self.other_stage.id},
            ],
        )

    def test_batch_write_that_moves_only_some(self):
        moved_tasks = self._create_task(name="Moved") | self._create_task(name="Moved Too")
        kept_task = self._create_task(name="Kept", stage_id=self.stage_progress.id)
        stageless_task = self._create_task(name="Stageless", stage_id=False)
        self.env.flush_all()

        (moved_tasks | kept_task | stageless_task).write({"stage_id": self.stage_progress.id})

        for task in moved_tasks:
            self.assertMoves(task, (self.no_stage, self.stage_new), (self.stage_new, self.stage_progress))
        self.assertMoves(kept_task, (self.no_stage, self.stage_progress))
        self.assertMoves(stageless_task, (self.no_stage, self.stage_progress))

    def test_task_moved_while_it_is_created_is_not_logged_twice(self):
        task = self._create_task()
        self.env.flush_all()
        self.Log._log_created_tasks(task)
        self.assertMoves(task, (self.no_stage, self.stage_new))

    def test_deleted_stage_keeps_its_name(self):
        retired_stage = self._create_stage("Retired", 4)
        task = self._create_task(stage_id=retired_stage.id)
        task.stage_id = self.stage_new
        self.env.flush_all()

        retired_stage.unlink()

        self.assertRecordValues(
            self._rows(task),
            [
                {"previous_stage_id": False, "stage_id": False, "stage_name": "Retired"},
                {"previous_stage_id": False, "stage_id": self.stage_new.id, "stage_name": "New"},
            ],
        )

    def test_deleted_task_takes_its_rows(self):
        task = self._create_task()
        rows = self._rows(task)
        task.unlink()
        self.assertFalse(rows.exists())

    def test_only_a_system_administrator_reads_the_log(self):
        task = self._create_task()
        rows = self._rows(task)
        administrator = self._create_user(
            "Administrator", "project_ai_driven_admin", "admin@example.com", extra_group="base.group_system"
        )
        manager = self._create_user(
            "Project Manager", "project_ai_driven_manager", "manager@example.com", "project.group_project_manager"
        )

        self.assertTrue(rows.with_user(administrator).has_access("read"))
        self.assertEqual(rows.with_user(administrator).stage_id, self.stage_new)
        other_users = (manager, self.user_lead, self.user_dev)
        for user in other_users:
            self.assertFalse(rows.with_user(user).has_access("read"))
            with self.assertRaises(AccessError):
                rows.with_user(user).read(["stage_id"])
            with self.assertRaises(AccessError):
                self.Log.with_user(user).search([("task_id", "=", task.id)])
        for user in (administrator, *other_users):
            as_user = rows.with_user(user)
            for operation in ("create", "write", "unlink"):
                self.assertFalse(as_user.has_access(operation), operation)
            with self.assertRaises(AccessError):
                as_user.create({"task_id": task.id, "date": datetime(2030, 1, 5), "source": "live"})
            with self.assertRaises(AccessError):
                as_user.write({"stage_id": self.stage_done.id})
            with self.assertRaises(AccessError):
                as_user.unlink()
