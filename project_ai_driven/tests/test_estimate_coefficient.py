from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests.common import tagged

from .common import ProjectAiDrivenCommon


@tagged("post_install", "-at_install")
class TestEstimateCoefficient(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.stage_progress = cls._create_stage("In Progress", 1)
        cls.stage_done = cls._create_stage("Done", 2, fold=True)
        cls.tag_report, cls.tag_stock = cls.env["project.tags"].create(
            [{"name": "Coefficient Report"}, {"name": "Coefficient Stock"}]
        )
        cls.tags = cls.tag_report | cls.tag_stock

    def _create_closed_tasks(self, actual_hours_list, name="Closed", project=None, days_ago=1, **values):
        """One closed feature task of the developer per value, estimated at one
        hour, each closed a minute after the one before, days_ago days back.
        """
        project = project or self.project
        values = {
            "project_id": project.id,
            "stage_id": self.stage_done.id,
            "user_ids": [Command.set(self.user_dev.ids)],
            "task_type": "feature",
            "planned_hours": 1,
            **values,
        }
        # no mail: dozens of tasks, assigned
        tasks = (
            project.env["project.task"]
            .with_context(tracking_disable=True)
            .create(
                [
                    {"name": "%s %d" % (name, index), "actual_hours": actual_hours, **values}
                    for index, actual_hours in enumerate(actual_hours_list)
                ]
            )
        )
        first_closing = fields.Datetime.now() - timedelta(days=days_ago)
        for minutes, task in enumerate(tasks):
            task.date_last_stage_update = first_closing + timedelta(minutes=minutes)
        return tasks

    def _get_coefficient(self, tag_ids, task_type, assignees=None, caller=None):
        task_model = self.env["project.task"].with_user(caller) if caller else self.env["project.task"]
        return task_model.get_estimate_coefficient(tag_ids, task_type, (assignees or self.user_dev).ids)

    def _newest_first(self, tasks):
        return tasks.sorted(lambda task: (task.date_last_stage_update, task.id), reverse=True)

    def _create_tasks_stored_by_sql(self):
        """Closed tasks, a day back, each with a NaN or an infinity that SQL wrote
        past the constraints into its actual hours, estimate or coefficient.
        """
        stored_values = [
            (column, value)
            for column in ("actual_hours", "planned_hours", "estimate_coefficient")
            for value in (float("nan"), float("inf"))
        ]
        stored_tasks = self._create_closed_tasks([1] * len(stored_values), name="Stored by SQL")
        self.env.flush_all()
        for task, (column, value) in zip(stored_tasks, stored_values):
            self.env.cr.execute("UPDATE project_task SET %s = %%s WHERE id = %%s" % column, (value, task.id))
        self.env.invalidate_all()

    def test_tasks_with_every_tag_and_the_type_come_first(self):
        keyed_env = self.env(context=dict(self.env.context, test_project_key=True))
        keyed_project = keyed_env["project.project"].create(
            {"name": "Keyed", "key": "ZQCOEF", "is_ai_driven": True, "type_ids": [Command.set(self.stage_done.ids)]}
        )
        tasks = self._create_closed_tasks(
            [1, 2, 2, 2, 3, 3], project=keyed_project, tag_ids=[Command.set(self.tags.ids)]
        )
        # one tag only, at another coefficient: the median would move if they counted
        self._create_closed_tasks([9] * 5, tag_ids=[Command.set(self.tag_report.ids)])

        self.assertEqual(
            self._get_coefficient(self.tags.ids, "feature"),
            {"coefficient": 2.0, "count": 6, "level": 1, "task_keys": tasks[::-1].mapped("key")},
        )
        self.assertTrue(all(key.startswith("ZQCOEF-") for key in tasks.mapped("key")))

    def test_tasks_with_a_tag_and_the_type_come_next(self):
        both_tag_tasks = self._create_closed_tasks([2, 2], name="Both tags", tag_ids=[Command.set(self.tags.ids)])
        one_tag_tasks = self._create_closed_tasks([2, 2, 2], name="One tag", tag_ids=[Command.set(self.tag_stock.ids)])
        self._create_closed_tasks([9] * 5, name="No tag")
        self._create_closed_tasks([9] * 5, name="Bug", task_type="bug", tag_ids=[Command.set(self.tags.ids)])

        self.assertEqual(
            self._get_coefficient(self.tags.ids, "feature"),
            {
                "coefficient": 2.0,
                "count": 5,
                "level": 2,
                "task_keys": self._newest_first(both_tag_tasks | one_tag_tasks).mapped("display_name"),
            },
        )

    def test_tasks_of_the_type_come_next(self):
        self._create_closed_tasks([3] * 4, name="Tagged", tag_ids=[Command.set(self.tags.ids)])
        self._create_closed_tasks([3], name="Not tagged")
        self._create_closed_tasks([9] * 5, name="Bug", task_type="bug", tag_ids=[Command.set(self.tags.ids)])

        result = self._get_coefficient(self.tags.ids, "feature")

        self.assertEqual((result["coefficient"], result["count"], result["level"]), (3.0, 5, 3))

    def test_any_task_comes_last(self):
        self._create_closed_tasks([4] * 3, name="Feature", tag_ids=[Command.set(self.tags.ids)])
        self._create_closed_tasks([4] * 2, name="Bug", task_type="bug")

        result = self._get_coefficient(self.tags.ids, "feature")

        self.assertEqual((result["coefficient"], result["count"], result["level"]), (4.0, 5, 4))

    def test_levels_without_tags_or_type_are_skipped(self):
        self._create_closed_tasks([2] * 5, tag_ids=[Command.set(self.tags.ids)])
        # a level kept without its type or tags would match these alone
        self._create_closed_tasks([6] * 5, name="Untyped", task_type=False, tag_ids=[Command.set(self.tags.ids)])
        for tag_ids, task_type, expected in (
            ([], "feature", (3, 2.0, 5)),
            (self.tags.ids, False, (4, 4.0, 10)),
            ([], False, (4, 4.0, 10)),
        ):
            with self.subTest(tag_ids=tag_ids, task_type=task_type):
                result = self._get_coefficient(tag_ids, task_type)
                self.assertEqual((result["level"], result["coefficient"], result["count"]), expected)

    def test_fewer_than_five_tasks_give_no_coefficient(self):
        tasks = self._create_closed_tasks([2] * 3, tag_ids=[Command.set(self.tags.ids)])
        tasks |= self._create_closed_tasks([2], name="Bug", task_type="bug")

        self.assertEqual(
            self._get_coefficient(self.tags.ids, "feature"),
            {
                "coefficient": 1.0,
                "count": 4,
                "level": False,
                "task_keys": self._newest_first(tasks).mapped("display_name"),
            },
        )

    def test_an_override_without_levels_gives_no_coefficient(self):
        self._create_closed_tasks([2] * 5)
        with patch.object(self.env.registry["project.task"], "_get_estimate_coefficient_levels", return_value=[]):
            result = self._get_coefficient(self.tags.ids, "feature")
        self.assertEqual(result, {"coefficient": 1.0, "count": 0, "level": False, "task_keys": []})

    def test_only_the_newest_thirty_closed_tasks_of_the_users_with_both_hours_count(self):
        newest_tasks = self._create_closed_tasks([1] * 15, name="Coefficient 1") | self._create_closed_tasks(
            [2] * 15, name="Coefficient 2"
        )
        # created last, closed first: the order is the closing date's
        self._create_closed_tasks([10] * 5, name="Old outlier", days_ago=30)
        self._create_closed_tasks([10] * 5, name="Open", days_ago=0, stage_id=self.stage_progress.id)
        self._create_closed_tasks([0], name="No actual hours", days_ago=0)
        self._create_closed_tasks([5], name="No estimate", days_ago=0, planned_hours=0)
        self._create_closed_tasks([10] * 5, name="Of the lead", days_ago=0, user_ids=[Command.set(self.user_lead.ids)])

        self.assertEqual(
            self._get_coefficient([], "feature"),
            {
                "coefficient": 1.5,
                "count": 30,
                "level": 3,
                "task_keys": self._newest_first(newest_tasks).mapped("display_name"),
            },
        )

    def test_the_coefficient_each_estimate_applied_is_taken_out(self):
        self._create_closed_tasks([2] * 5, planned_hours=2, estimate_coefficient=2.0)
        self.assertEqual(self._get_coefficient([], "feature")["coefficient"], 2.0)

    def test_tasks_with_a_nan_or_an_infinity_stored_by_sql_do_not_count(self):
        """PostgreSQL holds NaN and infinity above zero: the search finds them."""
        counted_tasks = self._create_closed_tasks([2] * 5, days_ago=2)
        self._create_tasks_stored_by_sql()

        self.assertEqual(
            self._get_coefficient([], "feature"),
            {
                "coefficient": 2.0,
                "count": 5,
                "level": 3,
                "task_keys": self._newest_first(counted_tasks).mapped("display_name"),
            },
        )

    def test_tasks_with_a_nan_or_an_infinity_stored_by_sql_take_no_slot_of_the_newest_thirty(self):
        self._create_closed_tasks([2] * 30, days_ago=2)
        self._create_tasks_stored_by_sql()

        result = self._get_coefficient([], "feature")

        self.assertEqual((result["coefficient"], result["count"], result["level"]), (2.0, 30, 3))

    def test_the_next_occurrence_and_its_sub_task_take_the_estimate_and_the_coefficient_it_applied(self):
        self.project.allow_recurring_tasks = True
        task_values = {"project_id": self.project.id, "stage_id": self.stage_done.id}
        recurring_task = self.env["project.task"].create(
            {
                "name": "Recurring Task",
                "planned_hours": 3,
                "estimate_coefficient": 1.5,
                "recurring_task": True,
                "repeat_unit": "week",
                "mon": True,
                **task_values,
            }
        )
        self.env["project.task"].create(
            {
                "name": "Sub-task",
                "parent_id": recurring_task.id,
                "planned_hours": 2,
                "estimate_coefficient": 2.5,
                **task_values,
            }
        )

        recurring_task.recurrence_id._create_next_task()

        next_task = recurring_task.recurrence_id.task_ids - recurring_task
        self.assertEqual(next_task.child_ids.name, "Sub-task")
        self.assertRecordValues(
            next_task | next_task.child_ids,
            [
                {"stage_id": self.stage_progress.id, "planned_hours": 3, "estimate_coefficient": 1.5},
                {"stage_id": self.stage_progress.id, "planned_hours": 2, "estimate_coefficient": 2.5},
            ],
        )

    def test_tasks_of_several_users_count_together(self):
        self._create_closed_tasks([2] * 3)
        self._create_closed_tasks([2] * 2, name="Of the lead", user_ids=[Command.set(self.user_lead.ids)])
        result = self._get_coefficient([], "feature", assignees=self.user_dev | self.user_lead)
        self.assertEqual((result["coefficient"], result["count"]), (2.0, 5))

    def test_a_caller_who_cannot_read_the_project_counts_no_task(self):
        self.project.privacy_visibility = "followers"
        self._create_closed_tasks([2] * 5, user_ids=[Command.set(self.user_lead.ids)])
        self.assertEqual(self._get_coefficient([], "feature", assignees=self.user_lead)["count"], 5)

        result = self._get_coefficient([], "feature", assignees=self.user_lead, caller=self.user_dev)

        self.assertEqual(result, {"coefficient": 1.0, "count": 0, "level": False, "task_keys": []})
