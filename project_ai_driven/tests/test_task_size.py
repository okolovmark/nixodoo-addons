from odoo.tests import Form

from ..models.project_task import SIZE_BANDS, get_size
from .common import ProjectAiDrivenCommon

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


class TestTaskSize(ProjectAiDrivenCommon):
    def _create_task(self, **values):
        return self.env["project.task"].create({"name": "Sized Task", "project_id": self.project.id, **values})

    def test_size_follows_the_hours(self):
        for hours, size in SIZES_OF_HOURS:
            self.task.allocated_hours = hours
            self.assertEqual(self.task.tshirt_estimate, size, hours)

    def test_hours_under_no_bound_take_the_largest_size(self):
        for hours in (float("nan"), float("inf")):
            self.assertEqual(get_size(hours), "xxl", hours)

    def test_no_size_without_hours(self):
        for hours in HOURS_WITHOUT_SIZE:
            self.task.allocated_hours = 5
            self.task.allocated_hours = hours
            self.assertFalse(self.task.tshirt_estimate, hours)

    def test_size_written_is_dropped(self):
        self.task.allocated_hours = 5

        self.assertTrue(self.task.write({"tshirt_estimate": "xxl"}))
        self.assertEqual(self.task.tshirt_estimate, "s")
        self.task.tshirt_estimate = False
        self.assertEqual(self.task.tshirt_estimate, "s")

        self.task.write({"tshirt_estimate": "xxl", "allocated_hours": 40})
        self.assertRecordValues(self.task, [{"allocated_hours": 40, "tshirt_estimate": "xl"}])

    def test_size_sent_at_create_is_dropped(self):
        tasks = self._create_task(allocated_hours=5, tshirt_estimate="xxl") | self._create_task(tshirt_estimate="m")
        self.assertRecordValues(
            tasks,
            [{"allocated_hours": 5, "tshirt_estimate": "s"}, {"allocated_hours": 0, "tshirt_estimate": False}],
        )

    def test_default_size_of_the_context_gives_way_to_the_hours(self):
        Task = self.env["project.task"].with_context(default_tshirt_estimate="xxl")
        task = Task.create({"name": "Sized Task", "project_id": self.project.id, "allocated_hours": 5})
        self.assertEqual(task.tshirt_estimate, "s")

    def test_sizes_sent_in_one_create_follow_the_hours_of_each_task(self):
        vals_list = [
            {"name": "Small Task", "project_id": self.project.id, "allocated_hours": 5, "tshirt_estimate": "xxl"},
            {"name": "Large Task", "project_id": self.project.id, "allocated_hours": 40, "tshirt_estimate": "xs"},
            {"name": "Task Without Estimate", "project_id": self.project.id, "tshirt_estimate": "m"},
        ]

        tasks = self.env["project.task"].create(vals_list)

        self.assertEqual(tasks.mapped("tshirt_estimate"), ["s", "xl", False])
        self.assertEqual([vals["tshirt_estimate"] for vals in vals_list], ["xxl", "xs", "m"])

    def test_write_leaves_the_values_of_the_caller_as_sent(self):
        vals = {"allocated_hours": 40, "tshirt_estimate": "xs"}
        self.task.write(vals)
        self.assertEqual(vals, {"allocated_hours": 40, "tshirt_estimate": "xs"})

    def test_copy_takes_the_size_of_its_own_hours(self):
        self.task.allocated_hours = 5
        copied_tasks = self.env["project.task"].concat(
            [
                self.task.copy(),
                self.task.copy({"tshirt_estimate": "xxl"}),
                self.task.copy({"allocated_hours": 40}),
            ],
        )
        self.assertEqual(copied_tasks.mapped("tshirt_estimate"), ["s", "s", "xl"])

    def test_help_states_the_bands(self):
        size_help = self.env["project.task"]._fields["tshirt_estimate"].help
        for max_hours, size in SIZE_BANDS[:-1]:
            self.assertIn("%s up to %d" % (size.upper(), max_hours), size_help)
        self.assertIn("%s above" % SIZE_BANDS[-1][1].upper(), size_help)

    def test_task_form_takes_the_estimate_and_shows_the_size(self):
        with Form(self.task) as task_form:
            task_form.allocated_hours = 5
            self.assertEqual(task_form.tshirt_estimate, "s")
            with self.assertRaises(AssertionError):
                task_form.tshirt_estimate = "xl"
        self.assertRecordValues(self.task, [{"allocated_hours": 5, "tshirt_estimate": "s"}])
