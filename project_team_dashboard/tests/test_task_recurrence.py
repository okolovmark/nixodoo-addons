from datetime import datetime

from odoo import Command
from odoo.tests import tagged

from ..models.project_task import DEADLINE_PLAN_FIELDS
from .common import TeamDashboardCommon

START = datetime(2026, 3, 2, 9)
END = datetime(2026, 3, 6, 12)
DEADLINE_BY_HAND = datetime(2026, 3, 10, 17)
# START, END and DEADLINE_BY_HAND one interval later
NEXT_DATES = {
    "week": (datetime(2026, 3, 9, 9), datetime(2026, 3, 13, 12), datetime(2026, 3, 17, 17)),
    "month": (datetime(2026, 4, 2, 9), datetime(2026, 4, 6, 12), datetime(2026, 4, 10, 17)),
}
PLANNED_DATE_SHAPES = {"both dates": (True, True), "end only": (False, True), "start only": (True, False)}
# unit, interval, planned dates, planned dates of the next occurrence
INTERVAL_CASES = {
    "every 3 days": ("day", 3, (START, END), (datetime(2026, 3, 5, 9), datetime(2026, 3, 9, 12))),
    "every 2 weeks": ("week", 2, (START, END), (datetime(2026, 3, 16, 9), datetime(2026, 3, 20, 12))),
    "every year from 29 February": (
        "year",
        1,
        (datetime(2028, 2, 29, 9), datetime(2028, 2, 29, 17)),
        (datetime(2029, 2, 28, 9), datetime(2029, 2, 28, 17)),
    ),
}


@tagged("post_install", "-at_install")
class TestTaskRecurrence(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        (cls.project | cls.project_plain).allow_recurring_tasks = True

    def _create_recurring_task(self, repeat_unit, repeat_interval=1, project=None, **vals):
        task = self.Task.create({"name": "Recurring", "project_id": (project or self.project).id, **vals})
        task.write(
            {
                "recurring_task": True,
                "repeat_interval": repeat_interval,
                "repeat_unit": repeat_unit,
                "repeat_type": "forever",
            }
        )
        return task

    def _close(self, tasks, user=None):
        """Close the tasks in one write and return the occurrence each made, in their order."""
        known_tasks = tasks.recurrence_id.task_ids
        tasks.with_user(user or self.env.user).state = "1_done"
        occurrences = [task.recurrence_id.task_ids - known_tasks for task in tasks]
        self.assertEqual([len(occurrence) for occurrence in occurrences], [1] * len(tasks))
        return self.Task.concat(occurrences)

    def _get_dates(self, task):
        return task.planned_date_start, task.planned_date_end, task.date_deadline

    def _get_followed_plan(self, task):
        return tuple(task[field_name] for field_name in DEADLINE_PLAN_FIELDS)

    def test_in_an_ai_driven_project_the_next_occurrence_moves_its_planned_dates_and_its_deadline_follows(self):
        for repeat_unit, (next_start, next_end, _next_deadline) in NEXT_DATES.items():
            for shape, (has_start, has_end) in PLANNED_DATE_SHAPES.items():
                with self.subTest(repeat_unit=repeat_unit, shape=shape):
                    task = self._create_recurring_task(
                        repeat_unit, planned_date_start=has_start and START, planned_date_end=has_end and END
                    )

                    occurrence = self._close(task)

                    self.assertEqual(
                        self._get_dates(occurrence),
                        (has_start and next_start, has_end and next_end, has_end and next_end),
                    )
                    self.assertEqual(self._get_followed_plan(occurrence), (has_end and next_end, self.project))
                    self.assertEqual(self._get_dates(task), (has_start and START, has_end and END, has_end and END))

    def test_outside_ai_driven_projects_the_next_occurrence_moves_its_planned_dates_and_its_deadline_alike(self):
        for repeat_unit, (next_start, next_end, next_deadline) in NEXT_DATES.items():
            for shape, (has_start, has_end) in PLANNED_DATE_SHAPES.items():
                with self.subTest(repeat_unit=repeat_unit, shape=shape):
                    task = self._create_recurring_task(
                        repeat_unit,
                        project=self.project_plain,
                        planned_date_start=has_start and START,
                        planned_date_end=has_end and END,
                        date_deadline=DEADLINE_BY_HAND,
                    )

                    occurrence = self._close(task)

                    self.assertEqual(
                        self._get_dates(occurrence), (has_start and next_start, has_end and next_end, next_deadline)
                    )

    def test_the_next_occurrence_moves_its_planned_dates_by_the_interval_in_the_unit_of_its_recurrence(self):
        for case, (repeat_unit, repeat_interval, (start, end), (next_start, next_end)) in INTERVAL_CASES.items():
            with self.subTest(case=case):
                task = self._create_recurring_task(
                    repeat_unit, repeat_interval, planned_date_start=start, planned_date_end=end
                )

                occurrence = self._close(task)

                self.assertEqual(self._get_dates(occurrence), (next_start, next_end, next_end))

    def test_tasks_of_different_recurrences_closed_together_each_move_by_their_own_recurrence(self):
        weekly = self._create_recurring_task("week", planned_date_start=START, planned_date_end=END)
        monthly = self._create_recurring_task(
            "month", planned_date_start=datetime(2026, 3, 16, 9), planned_date_end=datetime(2026, 3, 20, 12)
        )

        occurrences = self._close(weekly | monthly)

        next_start, next_end, _next_deadline = NEXT_DATES["week"]
        self.assertEqual(
            [self._get_dates(occurrence) for occurrence in occurrences],
            [
                (next_start, next_end, next_end),
                (datetime(2026, 4, 16, 9), datetime(2026, 4, 20, 12), datetime(2026, 4, 20, 12)),
            ],
        )

    def test_a_postponed_deadline_set_by_hand_stays_until_the_planned_end_of_the_occurrence_changes(self):
        task = self._create_recurring_task(
            "week", planned_date_start=START, planned_date_end=END, date_deadline=DEADLINE_BY_HAND
        )
        next_start, next_end, next_deadline = NEXT_DATES["week"]

        occurrence = self._close(task)

        self.assertEqual(self._get_dates(occurrence), (next_start, next_end, next_deadline))
        self.assertEqual(self._get_followed_plan(occurrence), (next_end, self.project))

        # as the Gantt chart writes a dragged start: both planned dates
        occurrence.write({"planned_date_start": datetime(2026, 3, 10, 9), "planned_date_end": next_end})

        self.assertEqual(occurrence.date_deadline, next_deadline)

        occurrence.planned_date_end = datetime(2026, 3, 16, 12)

        self.assertEqual(occurrence.date_deadline, datetime(2026, 3, 16, 12))

    def test_a_monthly_occurrence_moved_onto_the_end_of_a_shorter_month_keeps_its_length_and_its_end(self):
        task = self._create_recurring_task(
            "month", planned_date_start=datetime(2026, 1, 30, 14), planned_date_end=datetime(2026, 1, 31, 10)
        )

        february = self._close(task)
        march = self._close(february)

        self.assertEqual(
            [self._get_dates(occurrence) for occurrence in february | march],
            [
                (datetime(2026, 2, 27, 14), datetime(2026, 2, 28, 10), datetime(2026, 2, 28, 10)),
                (datetime(2026, 3, 27, 14), datetime(2026, 3, 28, 10), datetime(2026, 3, 28, 10)),
            ],
        )

    def test_a_monthly_occurrence_whose_dates_both_land_on_the_end_of_a_shorter_month_keeps_its_length(self):
        task = self._create_recurring_task(
            "month", planned_date_start=datetime(2026, 1, 30, 10), planned_date_end=datetime(2026, 1, 31, 10)
        )

        february = self._close(task)

        self.assertEqual(
            self._get_dates(february),
            (datetime(2026, 2, 27, 10), datetime(2026, 2, 28, 10), datetime(2026, 2, 28, 10)),
        )

    def test_the_subtasks_of_the_next_occurrence_move_their_planned_dates_and_its_parent_rolls_them_up(self):
        subtask_dates = {
            "First step": (datetime(2026, 3, 2, 9), datetime(2026, 3, 4, 17)),
            "Last step": (datetime(2026, 3, 4, 9), END),
        }
        parent = self._create_recurring_task(
            "week",
            child_ids=[
                Command.create(
                    {"name": name, "project_id": self.project.id, "planned_date_start": start, "planned_date_end": end}
                )
                for name, (start, end) in subtask_dates.items()
            ],
        )
        self.assertEqual(self._get_dates(parent), (START, END, END))

        occurrence = self._close(parent)

        self.assertEqual(
            {subtask.name: self._get_dates(subtask) for subtask in occurrence.child_ids},
            {
                "First step": (datetime(2026, 3, 9, 9), datetime(2026, 3, 11, 17), datetime(2026, 3, 11, 17)),
                "Last step": (datetime(2026, 3, 11, 9), datetime(2026, 3, 13, 12), datetime(2026, 3, 13, 12)),
            },
        )
        next_start, next_end, _next_deadline = NEXT_DATES["week"]
        self.assertTrue(occurrence.is_planned_from_subtasks)
        self.assertEqual(self._get_dates(occurrence), (next_start, next_end, next_end))
        self.assertEqual(self._get_followed_plan(occurrence), (next_end, self.project))

        moved_end = datetime(2026, 3, 16, 12)

        occurrence.child_ids.filtered(lambda subtask: subtask.name == "Last step").planned_date_end = moved_end

        self.assertEqual(self._get_dates(occurrence), (next_start, moved_end, moved_end))

    def test_a_portal_collaborator_closing_the_task_moves_the_planned_dates_of_the_next_occurrence(self):
        portal_user = self.env["res.users"].create(self._user_vals("Portal Closer", ["base.group_portal"]))
        self.project.write(
            {
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": portal_user.partner_id.id, "access_mode": "edit"})],
            }
        )
        task = self._create_recurring_task("week", planned_date_start=START, planned_date_end=END)
        next_start, next_end, _next_deadline = NEXT_DATES["week"]

        occurrence = self._close(task, user=portal_user)

        self.assertEqual(self._get_dates(occurrence), (next_start, next_end, next_end))

    def test_a_task_made_from_a_recurring_template_comes_unplanned_and_once_planned_its_occurrence_moves(self):
        template = self._create_recurring_task("week", planned_date_start=START, planned_date_end=END, is_template=True)
        task = self.Task.browse(template.action_create_from_template())
        self.assertEqual(self._get_dates(task), (False,) * 3)
        next_start, next_end, _next_deadline = NEXT_DATES["week"]

        task.write({"planned_date_start": next_start, "planned_date_end": next_end})
        occurrence = self._close(task)

        self.assertEqual(
            self._get_dates(occurrence),
            (datetime(2026, 3, 16, 9), datetime(2026, 3, 20, 12), datetime(2026, 3, 20, 12)),
        )

    def test_a_recurrence_stored_without_a_unit_still_repeats_a_task_without_planned_dates(self):
        task = self._create_recurring_task("week")
        # what an import or an RPC write can leave; Odoo moves no date then, so it never needs the unit
        task.recurrence_id.repeat_unit = False

        occurrence = self._close(task)

        self.assertEqual(self._get_dates(occurrence), (False,) * 3)

    def test_a_recurrence_stored_without_a_unit_repeats_a_planned_task_without_a_deadline(self):
        task = self._create_recurring_task("week", project=self.project_plain, planned_date_end=END)
        task.recurrence_id.repeat_unit = False

        occurrence = self._close(task)

        self.assertEqual(self._get_dates(occurrence), (False, END, False))
