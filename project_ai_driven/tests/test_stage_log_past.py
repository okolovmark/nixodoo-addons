import importlib.util
import logging
from datetime import datetime, timedelta
from pathlib import Path

from odoo import Command
from odoo.tests.common import tagged

from ..hooks import PAST_CASES, fill_stage_log, post_init_hook
from .common import ProjectAiDrivenCommon

MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.2.0" / "post-migration.py"
CREATED = datetime(2030, 1, 5, 10)
LATER = CREATED + timedelta(days=3)
TASK_STATE_QUERIES = (
    "SELECT id, stage_id, create_date, write_date, date_last_stage_update FROM project_task ORDER BY id",
    "SELECT count(*) FROM mail_message",
)
LOG_STATE_QUERY = """
    SELECT id, task_id, previous_stage_id, stage_id, stage_name, date, user_id, source
      FROM project_task_stage_log
  ORDER BY id
"""


@tagged("post_install", "-at_install")
class TestStageLogPast(ProjectAiDrivenCommon):
    """A task is put back by SQL in the state it had before the log existed:
    its dates as given, no row in the log, and the chatter the test gives it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Log = cls.env["project.task.stage.log"]
        cls.no_stage = cls.env["project.task.type"]
        cls.stage_new = cls._create_stage("New", 1)
        cls.stage_progress = cls._create_stage("In Progress", 2)
        cls.stage_done = cls._create_stage("Done", 3, fold=True)
        cls.stage_field = cls.env["ir.model.fields"]._get("project.task", "stage_id")
        cls.dependency_subtype = cls.env.ref("project.mt_task_dependency_change")
        # the tasks of the database have their rows: the counts of a test are those of its own tasks
        fill_stage_log(cls.env)

    def _create_past_task(self, stage, stage_updated=CREATED, created=CREATED, **values):
        task = self.env["project.task"].create(
            {"name": "Past Task", "project_id": self.project.id, "stage_id": stage.id, **values},
        )
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET create_date = %s, create_uid = %s, date_last_stage_update = %s WHERE id = %s",
            (created, self.user_lead.id, stage_updated, task.id),
        )
        self.env.cr.execute("DELETE FROM project_task_stage_log WHERE task_id = %s", (task.id,))
        task.invalidate_recordset()
        return task

    def _track(self, task, old_stage, new_stage, date, author=None, subtype=None):
        return self.env["mail.message"].create(
            {
                "model": "project.task",
                "res_id": task.id,
                "message_type": "notification",
                "subtype_id": (subtype or self.env.ref("project.mt_task_stage")).id,
                "author_id": (author or self.user_dev.partner_id).id,
                "date": date,
                "tracking_value_ids": [
                    Command.create(
                        {
                            "field": self.stage_field.id,
                            "field_desc": "Stage",
                            "field_type": "many2one",
                            "old_value_integer": old_stage.id,
                            "old_value_char": old_stage.name or "",
                            "new_value_integer": new_stage.id,
                            "new_value_char": new_stage.name or "",
                        }
                    )
                ],
            }
        )

    def _fill(self, **expected_counts):
        counts = fill_stage_log(self.env)
        self.assertEqual(counts, dict(dict.fromkeys(PAST_CASES, 0), **expected_counts))

    def _log_state(self):
        self.env.cr.execute(LOG_STATE_QUERY)
        return self.env.cr.fetchall()

    def assertRows(self, task, *expected_rows):
        """The rows of task, in order, as (date, user, previous stage, stage,
        stage name, source).
        """
        rows = self.Log.search([("task_id", "=", task.id)])
        self.assertRecordValues(
            rows,
            [
                {
                    "date": date,
                    "user_id": user.id,
                    "previous_stage_id": previous_stage.id,
                    "stage_id": stage.id,
                    "stage_name": stage_name,
                    "source": source,
                }
                for date, user, previous_stage, stage, stage_name, source in expected_rows
            ],
        )

    def test_tracked_task_gets_its_first_stage_and_its_changes(self):
        task = self._create_past_task(self.stage_done, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        self._track(task, self.stage_progress, self.stage_done, LATER, author=self.user_lead.partner_id)
        self._track(task, self.stage_new, self.stage_done, LATER, subtype=self.dependency_subtype)

        self._fill(tracked_task=1, tracked_start=1, tracked_move=2)

        no_stage, user_dev, user_lead = self.no_stage, self.user_dev, self.user_lead
        self.assertRows(
            task,
            (CREATED, user_lead, no_stage, self.stage_new, "New", "tracking"),
            (CREATED + timedelta(days=1), user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
            (LATER, user_lead, self.stage_progress, self.stage_done, "Done", "tracking"),
        )

    def test_tracked_task_created_without_a_stage(self):
        task = self._create_past_task(self.stage_new, stage_updated=LATER)
        self._track(task, self.no_stage, self.stage_new, LATER)

        self._fill(tracked_task=1, tracked_move=1)

        self.assertRows(task, (LATER, self.user_dev, self.no_stage, self.stage_new, "New", "tracking"))

    def test_tracked_task_that_left_its_stage(self):
        task = self._create_past_task(self.no_stage, stage_updated=LATER)
        self._track(task, self.stage_new, self.no_stage, LATER)

        self._fill(tracked_task=1, tracked_start=1, tracked_move=1)

        self.assertRows(
            task,
            (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "tracking"),
            (LATER, self.user_dev, self.stage_new, self.no_stage, False, "tracking"),
        )

    def test_deleted_stage_keeps_its_tracked_name(self):
        retired_stage = self._create_stage("Retired", 4)
        dropped_stage = self._create_stage("Dropped", 5)
        task = self._create_past_task(self.stage_new, stage_updated=LATER + timedelta(days=1))
        self._track(task, retired_stage, self.stage_new, CREATED + timedelta(days=1))
        self._track(task, self.stage_new, dropped_stage, LATER)
        (retired_stage | dropped_stage).unlink()

        self._fill(tracked_task=1, tracked_start=1, tracked_move=2, untracked_end=1)

        no_stage, user_dev = self.no_stage, self.user_dev
        self.assertRows(
            task,
            (CREATED, self.user_lead, no_stage, no_stage, "Retired", "tracking"),
            (CREATED + timedelta(days=1), user_dev, no_stage, self.stage_new, "New", "tracking"),
            (LATER, user_dev, self.stage_new, no_stage, "Dropped", "tracking"),
            (LATER + timedelta(days=1), self.env["res.users"], no_stage, self.stage_new, "New", "stage_date"),
        )

    def test_author_who_is_no_user(self):
        task = self._create_past_task(self.stage_done, stage_updated=LATER)
        contact = self.env["res.partner"].create({"name": "Contact"})
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1), author=contact)
        self._track(task, self.stage_progress, self.stage_done, LATER)
        self.env.cr.execute(
            "UPDATE mail_message SET author_id = NULL WHERE model = 'project.task' AND res_id = %s AND date = %s",
            (task.id, LATER),
        )

        self._fill(tracked_task=1, tracked_start=1, tracked_move=2)

        self.assertFalse(self.Log.search([("task_id", "=", task.id), ("previous_stage_id", "!=", False)]).user_id)

    def test_author_with_several_users_is_the_one_with_the_lowest_id(self):
        task = self._create_past_task(self.stage_progress, stage_updated=LATER)
        partner = self.user_dev.partner_id
        later_user = self._create_user("Second Login", "project_ai_driven_dev_2", "dev2@example.com")
        self.env.cr.execute("UPDATE res_users SET partner_id = %s WHERE id = %s", (partner.id, later_user.id))
        later_user.invalidate_recordset(["partner_id"])
        self._track(task, self.stage_new, self.stage_progress, LATER, author=partner)

        self._fill(tracked_task=1, tracked_start=1, tracked_move=1)

        self.assertRows(
            task,
            (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "tracking"),
            (LATER, self.user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
        )

    def test_stage_change_of_a_predecessor_is_not_one_of_the_task(self):
        task = self._create_past_task(self.stage_new)
        self._track(task, self.stage_new, self.stage_done, LATER, subtype=self.dependency_subtype)

        self._fill(creation=1)

        self.assertRows(task, (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "creation"))

    def test_message_without_a_subtype_is_a_change_of_the_task(self):
        task = self._create_past_task(self.stage_progress, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, LATER)
        self.env.cr.execute(
            "UPDATE mail_message SET subtype_id = NULL WHERE model = 'project.task' AND res_id = %s", (task.id,)
        )

        self._fill(tracked_task=1, tracked_start=1, tracked_move=1)

    def test_database_without_the_dependency_subtype(self):
        """Its external id is looked up, and none is found: every tracked stage
        change is then one of the task, a message without a subtype included.
        """
        external_id = self.env["ir.model.data"].search(
            [("module", "=", "project"), ("name", "=", "mt_task_dependency_change")]
        )
        external_id.unlink()
        task = self._create_past_task(self.stage_done, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        bare_message = self._track(task, self.stage_progress, self.stage_done, LATER)
        self.env.flush_all()
        self.env.cr.execute("UPDATE mail_message SET subtype_id = NULL WHERE id = %s", (bare_message.id,))

        self._fill(tracked_task=1, tracked_start=1, tracked_move=2)

    def test_changes_after_the_last_tracked_one(self):
        task = self._create_past_task(self.stage_done, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        stageless_task = self._create_past_task(self.no_stage, stage_updated=LATER)
        self._track(stageless_task, self.stage_new, self.stage_progress, LATER)

        self._fill(tracked_task=2, tracked_start=2, tracked_move=2, untracked_end=2)

        no_stage, no_user = self.no_stage, self.env["res.users"]
        self.assertRows(
            task,
            (CREATED, self.user_lead, no_stage, self.stage_new, "New", "tracking"),
            (
                CREATED + timedelta(days=1),
                self.user_dev,
                self.stage_new,
                self.stage_progress,
                "In Progress",
                "tracking",
            ),
            (LATER, no_user, no_stage, self.stage_done, "Done", "stage_date"),
        )
        self.assertRows(
            stageless_task,
            (CREATED, self.user_lead, no_stage, self.stage_new, "New", "tracking"),
            (LATER, self.user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
            (LATER, no_user, no_stage, no_stage, False, "stage_date"),
        )

    def test_current_stage_not_dated_after_the_last_tracked_change(self):
        early_task = self._create_past_task(self.stage_done, stage_updated=LATER - timedelta(seconds=1))
        undated_task = self._create_past_task(self.stage_done, stage_updated=None)
        for task in early_task | undated_task:
            self._track(task, self.stage_new, self.stage_progress, LATER)

        self._fill(tracked_task=2, tracked_start=2, tracked_move=2, undated_end=2)

        for task in early_task | undated_task:
            self.assertRows(
                task,
                (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "tracking"),
                (LATER, self.user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
            )

    def test_later_stage_date_adds_nothing_to_a_history_that_ends_at_the_current_stage(self):
        task = self._create_past_task(self.stage_progress, stage_updated=LATER + timedelta(days=30))
        self._track(task, self.stage_new, self.stage_progress, LATER)

        self._fill(tracked_task=1, tracked_start=1, tracked_move=1)

        self.assertEqual(self.Log.search_count([("task_id", "=", task.id)]), 2)

    def test_break_in_a_tracked_chain_stays_as_tracked(self):
        task = self._create_past_task(self.stage_new, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        self._track(task, self.stage_done, self.stage_new, LATER)

        self._fill(tracked_task=1, tracked_start=1, tracked_move=2)

        self.assertRows(
            task,
            (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "tracking"),
            (
                CREATED + timedelta(days=1),
                self.user_dev,
                self.stage_new,
                self.stage_progress,
                "In Progress",
                "tracking",
            ),
            (LATER, self.user_dev, self.stage_done, self.stage_new, "New", "tracking"),
        )

    def test_changes_of_the_same_second_keep_their_order(self):
        task = self._create_past_task(self.stage_done, stage_updated=CREATED)
        first_message = self._track(task, self.stage_new, self.stage_progress, CREATED)
        self._track(task, self.stage_progress, self.stage_done, CREATED)
        # only the id of its message says which change is the first: its rows are written again, and lie
        # behind those of the second change, where a scan of either table finds them last
        first_tracking = first_message.tracking_value_ids
        first_tracking.copy()
        first_tracking.unlink()
        self.env.flush_all()
        for res_id in (0, task.id):
            self.env.cr.execute("UPDATE mail_message SET res_id = %s WHERE id = %s", (res_id, first_message.id))

        self._fill(tracked_task=1, tracked_start=1, tracked_move=2)

        rows = self.Log.search([("task_id", "=", task.id)])
        self.assertEqual(rows.mapped("stage_name"), ["New", "In Progress", "Done"])

    def test_change_in_the_second_of_the_creation_comes_after_it(self):
        """A message is dated to the second and a task to the microsecond: the
        first change may be dated before the creation.
        """
        created = CREATED + timedelta(microseconds=600000)
        task = self._create_past_task(self.stage_progress, stage_updated=CREATED, created=created)
        self._track(task, self.stage_new, self.stage_progress, CREATED)
        later_task = self._create_past_task(self.stage_progress, stage_updated=LATER, created=created)
        self._track(later_task, self.stage_new, self.stage_progress, LATER)

        self._fill(tracked_task=2, tracked_start=2, tracked_move=2)

        self.assertRows(
            task,
            (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "tracking"),
            (CREATED, self.user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
        )
        self.assertRows(
            later_task,
            (created, self.user_lead, self.no_stage, self.stage_new, "New", "tracking"),
            (LATER, self.user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
        )

    def test_rows_without_a_date_are_left_out_and_counted(self):
        untracked_task = self._create_past_task(self.stage_new, created=None)
        tracked_task = self._create_past_task(self.stage_done, stage_updated=LATER, created=None)
        self._track(tracked_task, self.stage_new, self.stage_progress, CREATED)
        undated_message = self._track(tracked_task, self.stage_progress, self.stage_done, LATER)
        self.env.flush_all()
        self.env.cr.execute("UPDATE mail_message SET date = NULL WHERE id = %s", (undated_message.id,))

        self._fill(tracked_task=1, tracked_move=1, undated=3)

        self.assertFalse(self.Log.search([("task_id", "=", untracked_task.id)]))
        self.assertRows(
            tracked_task,
            (CREATED, self.user_dev, self.stage_new, self.stage_progress, "In Progress", "tracking"),
        )
        self._fill(undated=1)

    def test_untracked_task_whose_stage_was_written_after_its_creation(self):
        boundary_task = self._create_past_task(self.stage_progress, stage_updated=CREATED + timedelta(seconds=2))
        later_task = self._create_past_task(self.stage_done, stage_updated=LATER)

        self._fill(stage_date=2)

        no_stage, no_user = self.no_stage, self.env["res.users"]
        self.assertRows(
            boundary_task,
            (CREATED + timedelta(seconds=2), no_user, no_stage, self.stage_progress, "In Progress", "stage_date"),
        )
        self.assertRows(later_task, (LATER, no_user, no_stage, self.stage_done, "Done", "stage_date"))

    def test_untracked_task_in_the_stage_it_was_created_in(self):
        stage_updates = (CREATED, CREATED + timedelta(seconds=1), CREATED - timedelta(seconds=5), None)
        tasks = self.env["project.task"].concat(
            *(self._create_past_task(self.stage_new, stage_updated=stage_updated) for stage_updated in stage_updates)
        )

        self._fill(creation=len(tasks))

        for task in tasks:
            self.assertRows(task, (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "creation"))

    def test_task_without_stage_and_tracked_change_gets_nothing(self):
        task = self._create_past_task(self.no_stage, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_done, LATER, subtype=self.dependency_subtype)

        self._fill()

        self.assertFalse(self.Log.search([("task_id", "=", task.id)]))

    def test_archived_task(self):
        tracked_task = self._create_past_task(self.stage_progress, stage_updated=LATER, active=False)
        self._track(tracked_task, self.stage_new, self.stage_progress, LATER)
        untracked_task = self._create_past_task(self.stage_new, active=False)

        self._fill(tracked_task=1, tracked_start=1, tracked_move=1, creation=1)

        self.assertEqual(self.Log.search_count([("task_id", "=", tracked_task.id)]), 2)
        self.assertEqual(self.Log.search_count([("task_id", "=", untracked_task.id)]), 1)

    def test_task_that_has_a_row_is_left_alone(self):
        task = self.env["project.task"].create(
            {"name": "Logged Task", "project_id": self.project.id, "stage_id": self.stage_progress.id},
        )
        self._track(task, self.stage_new, self.stage_progress, LATER)
        self.env.flush_all()
        state = self._log_state()

        self._fill()

        self.assertEqual(self._log_state(), state)

    def test_second_run_inserts_nothing(self):
        tracked_task = self._create_past_task(self.stage_done, stage_updated=LATER)
        self._track(tracked_task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        self._create_past_task(self.stage_progress, stage_updated=LATER)
        self._create_past_task(self.stage_new)
        self._create_past_task(self.no_stage)
        fill_stage_log(self.env)
        state = self._log_state()

        self._fill()

        self.assertEqual(self._log_state(), state)

    def test_nothing_is_written_on_the_tasks(self):
        task = self._create_past_task(self.stage_done, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        self._create_past_task(self.stage_new)
        self.env.flush_all()

        def task_state():
            state = []
            for query in TASK_STATE_QUERIES:
                self.env.cr.execute(query)
                state.append(self.env.cr.fetchall())
            return state

        state = task_state()

        fill_stage_log(self.env)
        self.env.flush_all()

        self.assertEqual(task_state(), state)

    def test_one_log_line_with_the_counts(self):
        task = self._create_past_task(self.stage_done, stage_updated=LATER)
        self._track(task, self.stage_new, self.stage_progress, CREATED + timedelta(days=1))
        self._create_past_task(self.stage_new)

        # the level by number: Odoo gives the name INFO to a level above it
        with self.assertLogs("odoo.addons.project_ai_driven.hooks", level=logging.INFO) as log:
            fill_stage_log(self.env)

        self.assertEqual(len(log.output), 1)
        self.assertIn(
            "1 tasks with stage changes in the chatter, 1 stages they were created in, 1 stage changes,"
            " 1 current stages the chatter does not end at, 0 such stages left out for want of a later stage date,"
            " 0 stages of the other tasks from their last stage update, 1 stages of the other tasks from their creation,"
            " 0 rows left out for want of a date",
            log.output[0],
        )

    def test_post_init_hook_fills(self):
        task = self._create_past_task(self.stage_new)
        post_init_hook(self.env.cr, self.env.registry)
        self.assertRows(task, (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "creation"))

    def test_migration_script_fills_on_update_only(self):
        spec = importlib.util.spec_from_file_location("post_migration", MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        task = self._create_past_task(self.stage_new)

        script.migrate(self.env.cr, None)
        self.assertFalse(self.Log.search([("task_id", "=", task.id)]))

        script.migrate(self.env.cr, "16.0.2.1.0")
        self.assertRows(task, (CREATED, self.user_lead, self.no_stage, self.stage_new, "New", "creation"))
