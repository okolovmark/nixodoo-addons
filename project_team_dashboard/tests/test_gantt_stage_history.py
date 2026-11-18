from datetime import datetime, timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestGanttStageHistory(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.Project = cls.env["project.project"]
        cls.Log = cls.env["project.task.stage.log"]
        cls.no_stage = cls.env["project.task.type"]
        cls.now = fields.Datetime.now() + timedelta(days=30)

    def _create_task(self, name, stage, project=None, **vals):
        return self.Task.create(
            {
                "name": name,
                "project_id": (project or self.project).id,
                "stage_id": stage.id,
                "planned_date_start": datetime(2026, 10, 1, 9),
                "planned_date_end": datetime(2026, 10, 20, 17),
                **vals,
            },
        )

    def _stage_logs(self, task):
        self.env.flush_all()
        return self.Log.search([("task_id", "=", task.id)])

    def _set_stage_log(self, task, *rows):
        """Replace the rows task got when it was created by (stage, date) rows."""
        self._stage_logs(task).unlink()
        return self.Log.create(
            [
                {"task_id": task.id, "stage_id": stage.id, "stage_name": stage.name, "date": date, "source": "live"}
                for stage, date in rows
            ]
        )

    def _flush_tracking(self):
        self.env.flush_all()
        self.cr.precommit.run()

    def _gantt_task(self, result, task):
        return next(task_data for task_data in result["tasks"] if task_data["id"] == task.id)

    def _names(self, *stages):
        return [stage.name for stage in stages]

    def test_visits_follow_a_broken_chain_and_repeat_a_stage(self):
        task = self._create_task("Chain", self.stage_in_progress)
        created = task.create_date
        hours = [created + timedelta(hours=offset) for offset in range(1, 6)]
        stage_logs = self._set_stage_log(
            task,
            (self.stage_backlog, created),
            (self.stage_in_progress, hours[0]),
            (self.stage_backlog, hours[1]),
            (self.stage_other, hours[2]),
            (self.stage_backlog, hours[3]),
            (self.stage_in_progress, hours[4]),
        )
        # the row before it ended in Backlog
        stage_logs[3].previous_stage_id = self.stage_in_progress
        visits = task._gantt_stage_visits(stage_logs, self.now)
        self.assertEqual(
            [(visit["stage"], visit["start"], visit["end"]) for visit in visits],
            [
                (self.stage_backlog, created, hours[0]),
                (self.stage_in_progress, hours[0], hours[1]),
                (self.stage_backlog, hours[1], hours[2]),
                (self.stage_other, hours[2], hours[3]),
                (self.stage_backlog, hours[3], hours[4]),
                (self.stage_in_progress, hours[4], self.now),
            ],
        )

    def test_visits_of_a_task_never_moved_hold_its_stage_until_now(self):
        task = self._create_task("Never Moved", self.stage_other)
        stage_logs = self._stage_logs(task)
        self.assertEqual(stage_logs.mapped("source"), ["live"], "The row written when the task was created")
        visits = task._gantt_stage_visits(stage_logs, self.now)
        self.assertEqual(
            visits,
            [
                {
                    "stage": self.stage_other,
                    "stage_name": self.stage_other.name,
                    "start": stage_logs.date,
                    "end": self.now,
                }
            ],
        )

    def test_no_row_is_no_visit(self):
        task = self._create_task("No Row", self.stage_backlog)
        self._stage_logs(task).unlink()
        self.assertEqual(task._gantt_stage_visits(self.Log, self.now), [])
        self.assertEqual(task._gantt_stage_history(), {task.id: []})

    def test_visits_of_a_closed_task_end_where_the_last_one_starts(self):
        task = self._create_task("Closed", self.stage_done, state="1_done")
        closed_at = task.create_date + timedelta(hours=2)
        stage_logs = self._set_stage_log(task, (self.stage_in_progress, task.create_date), (self.stage_done, closed_at))
        visits = task._gantt_stage_visits(stage_logs, self.now)
        self.assertEqual(
            visits[-1],
            {"stage": self.stage_done, "stage_name": self.stage_done.name, "start": closed_at, "end": closed_at},
        )
        self.assertEqual(visits[0]["end"], closed_at)

    def test_the_last_visit_of_a_task_closed_in_its_stage_ends_when_it_was_closed(self):
        task = self._create_task("Closed In Place", self.stage_in_progress)
        entered_at = task.create_date
        closed_at = entered_at + timedelta(hours=5)
        stage_logs = self._set_stage_log(task, (self.stage_in_progress, entered_at))
        task.state = "1_done"
        task.date_last_stage_update = closed_at

        visits = task._gantt_stage_visits(stage_logs, self.now)

        self.assertEqual([(visit["start"], visit["end"]) for visit in visits], [(entered_at, closed_at)])

    def test_the_last_visit_of_an_open_task_in_a_folded_stage_runs_until_now(self):
        task = self._create_task("Folded Open", self.stage_done)
        stage_logs = self._set_stage_log(task, (self.stage_done, task.create_date))

        visits = task._gantt_stage_visits(stage_logs, self.now)

        self.assertFalse(task.is_closed)
        self.assertEqual(visits[-1]["end"], self.now)

    def test_visits_skip_a_row_without_stage_and_never_run_backwards(self):
        task = self._create_task("Stageless", self.stage_backlog)
        after_now = self.now + timedelta(days=1)
        stage_logs = self._set_stage_log(task, (self.no_stage, task.create_date), (self.stage_backlog, after_now))
        visits = task._gantt_stage_visits(stage_logs, self.now)
        self.assertEqual(
            visits,
            [
                {
                    "stage": self.stage_backlog,
                    "stage_name": self.stage_backlog.name,
                    "start": after_now,
                    "end": after_now,
                }
            ],
        )

    def test_visits_stop_while_the_task_has_no_stage(self):
        task = self._create_task("Left Its Stage", self.stage_in_progress)
        created = task.create_date
        left_at, back_at = created + timedelta(hours=1), created + timedelta(hours=2)
        stage_logs = self._set_stage_log(
            task, (self.stage_backlog, created), (self.no_stage, left_at), (self.stage_in_progress, back_at)
        )
        visits = task._gantt_stage_visits(stage_logs, self.now)
        self.assertEqual(
            [(visit["stage"], visit["start"], visit["end"]) for visit in visits],
            [(self.stage_backlog, created, left_at), (self.stage_in_progress, back_at, self.now)],
        )
        visits = task._gantt_stage_visits(stage_logs[:2], self.now)
        self.assertEqual(
            [(visit["stage"], visit["start"], visit["end"]) for visit in visits],
            [(self.stage_backlog, created, left_at)],
            "A task that left its stage has no open visit",
        )

    def test_payload_carries_stage_visits_when_asked(self):
        task = self._create_task("Payload", self.stage_in_progress)
        moved_at = (task.create_date + timedelta(hours=3)).replace(microsecond=0)
        self._set_stage_log(task, (self.stage_backlog, task.create_date), (self.stage_in_progress, moved_at))
        result = self.Project.get_gantt_chart_data(project_ids=[self.project.id], include_stage_history=True)
        history = self._gantt_task(result, task)["stage_history"]
        self.assertEqual(len(history), 2)
        self.assertEqual(
            history[0],
            {
                "stage_id": self.stage_backlog.id,
                "stage_name": "Backlog",
                "stage_sequence": 1,
                "is_closed": False,
                "start": fields.Datetime.to_string(task.create_date),
                "end": fields.Datetime.to_string(moved_at),
            },
        )
        self.assertEqual(history[1]["stage_name"], "In Progress")
        self.assertEqual(history[1]["start"], fields.Datetime.to_string(moved_at))
        self.assertGreaterEqual(history[1]["end"], history[1]["start"])

    def test_payload_orders_visits_by_date_whatever_order_the_rows_were_written_in(self):
        task = self._create_task("Out Of Order", self.stage_in_progress)
        created = task.create_date
        self._set_stage_log(
            task,
            (self.stage_in_progress, created + timedelta(hours=2)),
            (self.stage_backlog, created),
            (self.stage_other, created + timedelta(hours=1)),
        )
        result = self.Project.get_gantt_chart_data(project_ids=[self.project.id], include_stage_history=True)
        history = self._gantt_task(result, task)["stage_history"]
        self.assertEqual(
            [visit["stage_name"] for visit in history],
            self._names(self.stage_backlog, self.stage_other, self.stage_in_progress),
        )
        for visit in history:
            self.assertLessEqual(visit["start"], visit["end"], visit["stage_name"])

    def test_payload_marks_a_closed_visit(self):
        task = self._create_task("Done Payload", self.stage_done, state="1_done")
        closed_at = task.create_date + timedelta(hours=1)
        self._set_stage_log(task, (self.stage_in_progress, task.create_date), (self.stage_done, closed_at))
        result = self.Project.get_gantt_chart_data(
            project_ids=[self.project.id], show_completed=True, include_stage_history=True
        )
        first_visit, last_visit = self._gantt_task(result, task)["stage_history"]
        self.assertTrue(last_visit["is_closed"])
        self.assertEqual(last_visit["start"], last_visit["end"])
        self.assertFalse(first_visit["is_closed"], "Only the visit the task was closed in is closed")

    def test_payload_marks_no_visit_of_an_open_task_in_a_folded_stage_closed(self):
        task = self._create_task("Folded Open Payload", self.stage_done)
        self._set_stage_log(task, (self.stage_in_progress, task.create_date), (self.stage_done, task.create_date))

        result = self.Project.get_gantt_chart_data(project_ids=[self.project.id], include_stage_history=True)

        self.assertEqual(
            [visit["is_closed"] for visit in self._gantt_task(result, task)["stage_history"]], [False, False]
        )

    def test_payload_names_a_deleted_stage_from_the_log(self):
        retired_stage = self.env["project.task.type"].create({"name": "Retired", "sequence": 9})
        task = self._create_task("Retired Stage", self.stage_backlog)
        self._set_stage_log(
            task, (retired_stage, task.create_date), (self.stage_backlog, task.create_date + timedelta(hours=1))
        )
        retired_stage.unlink()
        history = task._gantt_stage_history()[task.id]
        self.assertEqual(len(history), 2)
        self.assertEqual((history[0]["stage_id"], history[0]["stage_name"]), (False, "Retired"))
        self.assertFalse(history[0]["is_closed"])

    def test_move_without_tracking_is_a_visit(self):
        task = self._create_task("Quiet Move", self.stage_backlog)
        self._flush_tracking()
        messages_before_the_move = task.message_ids
        task.with_context(tracking_disable=True, mail_notrack=True).stage_id = self.stage_in_progress
        self._flush_tracking()
        task.invalidate_recordset(["message_ids"])
        self.assertEqual(task.message_ids, messages_before_the_move, "The chatter holds no record of the move")
        history = task._gantt_stage_history()[task.id]
        self.assertEqual(
            [visit["stage_name"] for visit in history], self._names(self.stage_backlog, self.stage_in_progress)
        )
        self.assertEqual(history[0]["end"], history[1]["start"])

    def test_stage_change_not_yet_flushed_is_a_visit(self):
        task = self._create_task("Pending Move", self.stage_backlog)
        self.env.flush_all()
        # as a recomputation leaves a stage: in cache, not yet in the database
        self.Task._fields["stage_id"].write(task, self.stage_in_progress)
        history = task._gantt_stage_history()[task.id]
        self.assertEqual(
            [visit["stage_name"] for visit in history], self._names(self.stage_backlog, self.stage_in_progress)
        )

    def test_stage_change_of_a_predecessor_adds_no_visit(self):
        self.project.allow_task_dependencies = True
        self.env.user.group_ids += self.env.ref("project.group_project_task_dependencies")
        predecessor = self._create_task("Predecessor", self.stage_backlog)
        successor = self._create_task("Successor", self.stage_backlog)
        successor.depend_on_ids = predecessor
        self._flush_tracking()
        self.assertEqual(successor.state, "04_waiting_normal")
        predecessor.write({"stage_id": self.stage_in_progress.id, "state": "1_done"})
        self._flush_tracking()
        self.assertEqual(successor.state, "01_in_progress", "The predecessor's change reaches the successor")
        history_by_task = (predecessor | successor)._gantt_stage_history()
        self.assertEqual(
            [visit["stage_name"] for visit in history_by_task[successor.id]], self._names(self.stage_backlog)
        )
        self.assertEqual(
            [visit["stage_name"] for visit in history_by_task[predecessor.id]],
            self._names(self.stage_backlog, self.stage_in_progress),
        )

    def test_stage_history_is_not_computed_unless_asked(self):
        task = self._create_task("Not Asked", self.stage_in_progress)
        task_model = self.registry["project.task"]
        with patch.object(task_model, "_gantt_stage_history", autospec=True, return_value={}) as history_mock:
            result = self.Project.get_gantt_chart_data(project_ids=[self.project.id])
            history_mock.assert_not_called()
            self.Project.get_gantt_chart_data(project_ids=[self.project.id], include_stage_history=True)
            history_mock.assert_called_once()
        self.assertEqual(self._gantt_task(result, task)["stage_history"], [])

    def test_stage_history_never_leaks_a_hidden_task(self):
        hidden_project = self.Project.create(
            {
                "name": "Hidden History",
                "privacy_visibility": "followers",
                "type_ids": [Command.set([self.stage_backlog.id, self.stage_in_progress.id])],
            },
        )
        hidden_project.message_unsubscribe(partner_ids=[self.user_dev1.partner_id.id])
        visible_task = self._create_task("Visible", self.stage_backlog)
        hidden_task = self._create_task("Hidden", self.stage_backlog, project=hidden_project)
        (visible_task | hidden_task).stage_id = self.stage_in_progress
        result = self.Project.with_user(self.user_dev1).get_gantt_chart_data(
            project_ids=[self.project.id, hidden_project.id], include_stage_history=True
        )
        task_ids = {task_data["id"] for task_data in result["tasks"]}
        self.assertIn(visible_task.id, task_ids)
        self.assertNotIn(hidden_task.id, task_ids)
        self.assertEqual(len(self._gantt_task(result, visible_task)["stage_history"]), 2)
        history_by_task = visible_task.with_user(self.user_dev1)._gantt_stage_history()
        self.assertEqual(set(history_by_task), {visible_task.id})

    def test_stage_history_is_one_batch_for_any_number_of_tasks(self):
        tasks = self.Task
        for index in range(3):
            tasks |= self._create_task("Batch %s" % index, self.stage_backlog)
        tasks.stage_id = self.stage_in_progress
        tasks._gantt_stage_history()

        def count_queries(task_records):
            self.env.invalidate_all()
            queries_before = self.cr.sql_log_count
            history_by_task = task_records._gantt_stage_history()
            self.assertEqual(set(history_by_task), set(task_records.ids))
            self.assertEqual({len(history) for history in history_by_task.values()}, {2})
            return self.cr.sql_log_count - queries_before

        self.assertEqual(count_queries(tasks), count_queries(tasks[:1]))
        self.assertEqual(self.Task._gantt_stage_history(), {})
