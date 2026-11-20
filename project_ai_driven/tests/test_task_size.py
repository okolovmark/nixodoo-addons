import importlib.util
import logging
import unittest
from datetime import datetime
from pathlib import Path

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import Form, new_test_user, tagged

from ..hooks import MODULE, clear_estimates_that_are_not_numbers, post_init_hook, recompute_task_sizes
from ..models.project_task import SIZE_BANDS, get_size
from .common import ProjectAiDrivenCommon

MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.4.0" / "post-migration.py"
FINITE_ESTIMATE_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.4.1" / "post-migration.py"
NOT_NUMBERS = (float("nan"), float("inf"), float("-inf"))
HOOKS_LOGGER = "odoo.addons.%s.hooks" % MODULE
SIZES_OF_HOURS = (
    (0.5, "xs"),
    (2, "xs"),
    (2.5, "s"),
    (6, "s"),
    (6.5, "m"),
    (12, "m"),
    (12.5, "l"),
    (32, "l"),
    (33, "xl"),
    (60, "xl"),
    (61, "xxl"),
)
HOURS_WITHOUT_SIZE = (0, -1, None)
TASKS_BESIDE_THEIR_SIZE = "SELECT id, task_type, planned_hours, write_date, write_uid FROM project_task ORDER BY id"
EARLIER_WRITE_DATE = datetime(2026, 3, 2, 9, 0)


@tagged("post_install", "-at_install")
class TestTaskSize(ProjectAiDrivenCommon):
    def _create_task(self, **values):
        return self.env["project.task"].create({"name": "Sized Task", "project_id": self.project.id, **values})

    def test_size_follows_the_hours(self):
        for hours, size in SIZES_OF_HOURS:
            self.task.planned_hours = hours
            self.assertEqual(self.task.tshirt_estimate, size, hours)

    def test_no_size_without_hours(self):
        for hours in HOURS_WITHOUT_SIZE:
            self.task.planned_hours = 5
            self.task.planned_hours = hours
            self.assertFalse(self.task.tshirt_estimate, hours)

    def test_size_written_is_dropped(self):
        self.task.planned_hours = 5

        self.assertTrue(self.task.write({"tshirt_estimate": "xxl"}))
        self.assertEqual(self.task.tshirt_estimate, "s")
        self.task.tshirt_estimate = False
        self.assertEqual(self.task.tshirt_estimate, "s")

        self.task.write({"tshirt_estimate": "xxl", "planned_hours": 40})
        self.assertRecordValues(self.task, [{"planned_hours": 40, "tshirt_estimate": "xl"}])

    def test_size_sent_at_create_is_dropped(self):
        tasks = self._create_task(planned_hours=5, tshirt_estimate="xxl") | self._create_task(tshirt_estimate="m")
        self.assertRecordValues(
            tasks,
            [{"planned_hours": 5, "tshirt_estimate": "s"}, {"planned_hours": 0, "tshirt_estimate": False}],
        )

    def test_default_size_of_the_context_gives_way_to_the_hours(self):
        Task = self.env["project.task"].with_context(default_tshirt_estimate="xxl")
        task = Task.create({"name": "Sized Task", "project_id": self.project.id, "planned_hours": 5})
        self.assertEqual(task.tshirt_estimate, "s")

    def test_sizes_sent_in_one_create_follow_the_hours_of_each_task(self):
        vals_list = [
            {"name": "Small Task", "project_id": self.project.id, "planned_hours": 5, "tshirt_estimate": "xxl"},
            {"name": "Large Task", "project_id": self.project.id, "planned_hours": 40, "tshirt_estimate": "xs"},
            {"name": "Task Without Estimate", "project_id": self.project.id, "tshirt_estimate": "m"},
        ]

        tasks = self.env["project.task"].create(vals_list)

        self.assertEqual(tasks.mapped("tshirt_estimate"), ["s", "xl", False])
        self.assertEqual([vals["tshirt_estimate"] for vals in vals_list], ["xxl", "xs", "m"])

    def test_write_leaves_the_values_of_the_caller_as_sent(self):
        vals = {"planned_hours": 40, "tshirt_estimate": "xs"}
        self.task.write(vals)
        self.assertEqual(vals, {"planned_hours": 40, "tshirt_estimate": "xs"})

    def test_copy_takes_the_size_of_its_own_hours(self):
        self.task.planned_hours = 5
        copied_tasks = self.env["project.task"].concat(
            self.task.copy(),
            self.task.copy({"tshirt_estimate": "xxl"}),
            self.task.copy({"planned_hours": 40}),
        )
        self.assertEqual(copied_tasks.mapped("tshirt_estimate"), ["s", "s", "xl"])

    def test_help_states_the_bands(self):
        size_help = self.env["project.task"]._fields["tshirt_estimate"].help
        for max_hours, size in SIZE_BANDS[:-1]:
            self.assertIn("%s up to %d" % (size.upper(), max_hours), size_help)
        self.assertIn("%s above" % SIZE_BANDS[-1][1].upper(), size_help)

    def test_task_form_takes_the_estimate_and_shows_the_size(self):
        with Form(self.task) as task_form:
            task_form.planned_hours = 5
            self.assertEqual(task_form.tshirt_estimate, "s")
            with self.assertRaises(AssertionError):
                task_form.tshirt_estimate = "xl"
        self.assertRecordValues(self.task, [{"planned_hours": 5, "tshirt_estimate": "s"}])


@tagged("post_install", "-at_install")
class TestTaskSizeProjectSharing(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_portal = new_test_user(
            cls.env,
            "project_ai_driven_portal",
            groups="base.group_portal",
            context={"no_reset_password": True},
            name="Portal Collaborator",
            email="portal@example.com",
        )
        cls.project.write(
            {
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": cls.user_portal.partner_id.id})],
            }
        )
        cls.project.message_subscribe(partner_ids=cls.user_portal.partner_id.ids)
        cls.task.planned_hours = 5

    def test_a_portal_collaborator_writing_the_size_is_refused(self):
        task = self.task.with_user(self.user_portal)
        task.write({"name": "Renamed by the collaborator"})

        for vals in ({"tshirt_estimate": "xxl"}, {"name": "Sized by the collaborator", "tshirt_estimate": False}):
            # unittest's: Odoo's rolls back what ran before the refusal
            with self.subTest(vals=vals), unittest.TestCase.assertRaises(self, AccessError) as refusal:
                task.write(vals)
            self.assertIn("tshirt_estimate", str(refusal.exception))

        self.assertRecordValues(self.task, [{"name": "Renamed by the collaborator", "tshirt_estimate": "s"}])

    def test_a_size_written_in_sudo_for_a_portal_collaborator_is_dropped(self):
        self.assertTrue(self.task.with_user(self.user_portal).sudo().write({"tshirt_estimate": "xxl"}))
        self.assertEqual(self.task.tshirt_estimate, "s")


@tagged("post_install", "-at_install")
class TestRecomputeTaskSizes(ProjectAiDrivenCommon):
    """Sizes that are not the size of the hours are stored by SQL, as the
    column held them before the size followed the hours.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # what the database holds besides the tasks of a test is not this test's to count
        recompute_task_sizes(cls.env.cr)

    def _recompute(self):
        """Return the count the recompute returns and the line it logs."""
        # the level by number: Odoo gives the name INFO to a level above it
        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            changed_count = recompute_task_sizes(self.env.cr)
        return changed_count, logs.records[0].getMessage()

    def _create_task(self, hours, **values):
        """Hours stored by SQL leave the task the size the ORM gave it: none.
        The last write is another user's, before this transaction: a write
        of this one would leave both as they are.
        """
        task = self.env["project.task"].create({"name": "Old Task", "project_id": self.project.id, **values})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET planned_hours = %s, write_date = %s, write_uid = %s WHERE id = %s",
            (hours, EARLIER_WRITE_DATE, self.user_dev.id, task.id),
        )
        task.invalidate_recordset(["planned_hours", "write_date", "write_uid"])
        return task

    def _store_size(self, tasks, size):
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET tshirt_estimate = %s WHERE id IN %s", (size, tuple(tasks.ids)))
        tasks.invalidate_recordset(["tshirt_estimate"])

    def _stored_rows(self, query):
        self.env.flush_all()
        self.env.cr.execute(query)
        return self.env.cr.fetchall()

    def _tasks_off_their_size(self):
        rows = self._stored_rows("SELECT id, planned_hours, tshirt_estimate FROM project_task")
        return [task_id for task_id, hours, size in rows if (size or False) != get_size(hours)]

    def test_every_size_becomes_the_size_of_its_hours(self):
        sized_task = self._create_task(8, task_type="bug")
        self._store_size(sized_task, "m")
        stale_tasks = self.env["project.task"].concat(
            self._create_task(0.5, task_type="feature"),
            self._create_task(40),
            self._create_task(0),
            self._create_task(None),
            self._create_task(100, active=False),
        )
        self._store_size(stale_tasks, "m")
        unsized_task = self._create_task(3)
        self._store_size(unsized_task, None)
        self.assertEqual(sorted(self._tasks_off_their_size()), sorted((stale_tasks | unsized_task).ids))
        rows_beside_the_size = self._stored_rows(TASKS_BESIDE_THEIR_SIZE)

        changed_count, logged_line = self._recompute()

        self.assertEqual(changed_count, 6)
        self.assertEqual(logged_line, "Task sizes recomputed from the hours: 6 tasks changed")
        self.assertEqual(self._tasks_off_their_size(), [])
        self.assertEqual(
            (sized_task | stale_tasks | unsized_task).mapped("tshirt_estimate"),
            ["m", "xs", "xl", False, False, "xxl", "s"],
        )
        self.assertEqual(self._stored_rows(TASKS_BESIDE_THEIR_SIZE), rows_beside_the_size)

    def test_second_run_changes_nothing(self):
        self._store_size(self._create_task(40), "m")
        recompute_task_sizes(self.env.cr)
        sizes = self._stored_rows("SELECT id, tshirt_estimate FROM project_task ORDER BY id")

        changed_count, logged_line = self._recompute()

        self.assertEqual(changed_count, 0)
        self.assertEqual(logged_line, "Task sizes recomputed from the hours: 0 tasks changed")
        self.assertEqual(self._stored_rows("SELECT id, tshirt_estimate FROM project_task ORDER BY id"), sizes)

    def test_sql_bands_are_the_python_bands(self):
        bounds = [max_hours for max_hours, _size in SIZE_BANDS[:-1]]
        hours_at_the_bounds = [bound + step for bound in [0, *bounds] for step in (-0.01, 0, 0.01)]
        hours_beside_the_bands = [float(value) for value in ("nan", "inf", "-inf")]
        all_hours = [None, -5, *hours_at_the_bounds, 10 * bounds[-1], *hours_beside_the_bands]
        self.assertEqual({get_size(hours) for hours in all_hours}, {False, *(size for _max_hours, size in SIZE_BANDS)})
        tasks = self.env["project.task"].concat(*map(self._create_task, all_hours))
        for task, hours in zip(tasks, all_hours):
            self._store_size(task, "xs" if get_size(hours) == "xxl" else "xxl")

        changed_count = recompute_task_sizes(self.env.cr)

        self.assertEqual(changed_count, len(all_hours))
        for task, hours in zip(tasks, all_hours):
            self.assertEqual(task.tshirt_estimate, get_size(hours), hours)

    def _load_migration(self, path):
        spec = importlib.util.spec_from_file_location("post_migration", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        return script

    def test_estimates_that_are_not_numbers_are_set_to_zero(self):
        tasks = self.env["project.task"].concat(*map(self._create_task, (*NOT_NUMBERS, 5)))
        recompute_task_sizes(self.env.cr)
        self.assertEqual(tasks.mapped("tshirt_estimate"), ["xxl", "xxl", False, "s"])
        if "remaining_hours" in tasks._fields:
            # hr_timesheet stores the hours less the time spent: what a write of the hours had left
            self.env.cr.execute(
                "UPDATE project_task SET remaining_hours = planned_hours WHERE id IN %s", [tuple(tasks.ids)]
            )

        self.assertEqual(clear_estimates_that_are_not_numbers(self.env.cr), 3)
        # the fields computed from the hours follow without a size recompute
        self.assertRecordValues(
            tasks,
            [{"planned_hours": hours, "tshirt_estimate": size} for hours, size in ((0, False),) * 3 + ((5, "s"),)],
        )
        if "remaining_hours" in tasks._fields:
            self.env.cr.execute("SELECT remaining_hours FROM project_task WHERE id IN %s", [tuple(tasks[:3].ids)])
            self.assertEqual([row[0] for row in self.env.cr.fetchall()], [0, 0, 0])
        self.assertEqual(clear_estimates_that_are_not_numbers(self.env.cr), 0)

    def test_post_init_hook_sets_estimates_that_are_not_numbers_to_zero(self):
        task = self._create_task(float("nan"))
        post_init_hook(self.env.cr, self.env.registry)
        self.assertRecordValues(task, [{"planned_hours": 0, "tshirt_estimate": False}])

    def test_finite_estimate_migration_on_update_only(self):
        script = self._load_migration(FINITE_ESTIMATE_SCRIPT)
        tasks = self.env["project.task"].concat(*map(self._create_task, (*NOT_NUMBERS, 5)))
        recompute_task_sizes(self.env.cr)

        script.migrate(self.env.cr, None)
        self.assertEqual(tasks[1].planned_hours, float("inf"))

        script.migrate(self.env.cr, "16.0.2.4.0")
        self.assertRecordValues(
            tasks,
            [
                {"planned_hours": 0, "tshirt_estimate": False},
                {"planned_hours": 0, "tshirt_estimate": False},
                {"planned_hours": 0, "tshirt_estimate": False},
                {"planned_hours": 5, "tshirt_estimate": "s"},
            ],
        )

    def test_migration_script_recomputes_on_update_only(self):
        script = self._load_migration(MIGRATION_SCRIPT)
        task = self._create_task(40)
        self._store_size(task, "m")

        script.migrate(self.env.cr, None)
        self.assertEqual(task.tshirt_estimate, "m")

        script.migrate(self.env.cr, "16.0.2.3.0")
        self.assertEqual(task.tshirt_estimate, "xl")
