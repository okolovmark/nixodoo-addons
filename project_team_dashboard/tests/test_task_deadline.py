import importlib.util
import os
from collections import deque
from datetime import date, datetime
from unittest.mock import patch

from psycopg2 import sql

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import Form, tagged

from ..hooks import post_init_hook
from ..models.project_task import DEADLINE_PLAN_FIELDS
from .common import TeamDashboardCommon

# 01:30 on 3 March for a user eight hours ahead of UTC
EVENING_UTC = datetime(2026, 3, 2, 17, 30)
# 2 March from eleven hours behind UTC to eleven hours ahead
MIDDAY_UTC = datetime(2026, 3, 2, 12)
GANTT_START = datetime(2026, 2, 27, 9)
MIGRATION = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "16.0.1.1.0", "post-migration.py")


def _run_update_migration(cr):
    spec = importlib.util.spec_from_file_location("project_team_dashboard_post_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.migrate(cr, "16.0.1.0.0")


@tagged("post_install", "-at_install")
class TestTaskDeadline(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        (cls.project | cls.project_plain).allow_subtasks = True
        planner_groups = ["base.group_user", "project.group_project_user"]
        cls.planner_utc_plus_8, cls.planner_utc, cls.planner_without_tz = cls.env["res.users"].create(
            [
                dict(cls._user_vals("Planner Plus Eight", planner_groups), tz="Australia/Perth"),
                dict(cls._user_vals("Planner UTC", planner_groups), tz="UTC"),
                dict(cls._user_vals("Planner Without Zone", planner_groups), tz=False),
            ]
        )
        # The installed task forms may hide the Deadline, and Form edits no row of their sub-task
        # list: it takes the list's many2many widget for the field type. The plan fields are there
        # as on the module's form.
        cls.deadline_form_view = cls.env["ir.ui.view"].create(
            {
                "name": "project.task.form.deadline",
                "model": "project.task",
                "arch": """
                    <form>
                        <field name="company_id" invisible="1"/>
                        <field name="project_id"/>
                        <field name="date_deadline"/>
                        <field name="date_deadline_planned_end" invisible="1"/>
                        <field name="date_deadline_project_id" invisible="1"/>
                        <field name="child_ids">
                            <tree editable="bottom">
                                <field name="name"/>
                                <field name="date_deadline"/>
                            </tree>
                        </field>
                    </form>
                """,
            }
        )
        # The installed task form, its sub-task rows editable by Form and showing their planned end.
        cls.subtask_planned_end_form_view = cls.env["ir.ui.view"].create(
            {
                "name": "project.task.form.subtask.planned.end",
                "model": "project.task",
                "inherit_id": cls.env.ref("project.view_task_form2").id,
                "mode": "primary",
                "arch": """
                    <data>
                        <xpath expr="//field[@name='child_ids']" position="attributes">
                            <attribute name="widget"/>
                        </xpath>
                        <xpath expr="//field[@name='child_ids']/tree/field[@name='date_deadline']" position="before">
                            <field name="planned_date_end"/>
                        </xpath>
                    </data>
                """,
            }
        )

    def _create_task(self, name, project=None, **vals):
        task_vals = {
            "name": name,
            "project_id": (project or self.project).id,
            "stage_id": self.stage_in_progress.id,
        }
        task_vals.update(vals)
        return self.Task.create(task_vals)

    def _move_the_start_alone(self, tasks):
        """Write what the Gantt chart writes when a bar's start is dragged: both planned dates."""
        for task in tasks:
            task.write({"planned_date_start": GANTT_START, "planned_date_end": task.planned_date_end})

    def test_deadline_is_the_date_of_the_planned_end_for_the_user_who_saves(self):
        for planner, deadline in (
            (self.planner_utc_plus_8, date(2026, 3, 3)),
            (self.planner_utc, date(2026, 3, 2)),
            (self.planner_without_tz, date(2026, 3, 2)),
        ):
            with self.subTest(planner=planner.name):
                task = self._create_task("Planned by %s" % planner.name).with_user(planner)

                task.planned_date_end = EVENING_UTC

                self.assertEqual(task.date_deadline, deadline)

    def test_the_deadline_is_the_day_of_the_planned_end_in_the_time_zone_of_the_save_over_the_user_s(self):
        for planner, save_tz, deadline in (
            (self.planner_utc, "Australia/Perth", date(2026, 3, 3)),
            (self.planner_utc_plus_8, "UTC", date(2026, 3, 2)),
            (self.planner_without_tz, "Australia/Perth", date(2026, 3, 3)),
        ):
            with self.subTest(planner=planner.name, save_tz=save_tz):
                task = self._create_task("Planned by %s in %s" % (planner.name, save_tz))
                task = task.with_user(planner).with_context(tz=save_tz)

                task.planned_date_end = EVENING_UTC

                self.assertEqual(task.date_deadline, deadline)

    def test_create_with_a_planned_end_sets_the_deadline(self):
        task = self.Task.with_user(self.planner_utc_plus_8).create(
            {"name": "Planned", "project_id": self.project.id, "planned_date_end": EVENING_UTC}
        )
        self.assertEqual(task.date_deadline, date(2026, 3, 3))

    def test_a_deadline_saved_with_the_planned_end_stays_until_the_planned_end_changes(self):
        created_with_both = self._create_task("Planned", planned_date_end=MIDDAY_UTC, date_deadline=date(2026, 4, 1))
        created_by_default = self.Task.with_context(default_date_deadline=date(2026, 4, 1)).create(
            {"name": "From the calendar", "project_id": self.project.id, "planned_date_end": MIDDAY_UTC}
        )
        written_with_both = self._create_task("Replanned", planned_date_end=MIDDAY_UTC)
        written_with_both.write({"planned_date_end": datetime(2026, 3, 1, 12), "date_deadline": date(2026, 4, 1)})
        self.env["ir.default"].set("project.task", "date_deadline", "2026-04-01")
        created_from_a_default = self._create_task("From a default", planned_date_end=MIDDAY_UTC)
        tasks = created_with_both | created_by_default | written_with_both | created_from_a_default
        self.assertEqual(tasks.mapped("date_deadline"), [date(2026, 4, 1)] * 4)

        self._move_the_start_alone(tasks)

        self.assertEqual(tasks.mapped("date_deadline"), [date(2026, 4, 1)] * 4)

        tasks.planned_date_end = datetime(2026, 3, 9, 12)

        self.assertEqual(tasks.mapped("date_deadline"), [date(2026, 3, 9)] * 4)

    def test_a_deadline_set_by_hand_stays_until_the_planned_end_changes(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)
        task.date_deadline = date(2026, 4, 1)

        self._move_the_start_alone(task)

        self.assertEqual(task.date_deadline, date(2026, 4, 1))

        task.planned_date_end = datetime(2026, 3, 9, 12)

        self.assertEqual(task.date_deadline, date(2026, 3, 9))

    def test_on_the_form_the_deadline_follows_the_planned_end_and_stays_once_saved(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)

        with Form(task.with_user(self.planner_utc_plus_8)) as task_form:
            task_form.planned_date_end = EVENING_UTC
            self.assertEqual(task_form.date_deadline, date(2026, 3, 3))

        self.assertEqual(task.date_deadline, date(2026, 3, 3))
        self._move_the_start_alone(task)
        self.assertEqual(task.date_deadline, date(2026, 3, 3))

    def test_a_subtask_edited_on_the_parent_form_keeps_the_deadlines_set_by_hand(self):
        parent = self._create_task("Parent")
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 12),
        )
        (parent | subtask).date_deadline = date(2026, 4, 1)

        with Form(parent, view=self.deadline_form_view) as parent_form:
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.name = "Sub-task renamed"

        self.assertEqual(subtask.name, "Sub-task renamed")
        self.assertEqual((parent | subtask).mapped("date_deadline"), [date(2026, 4, 1)] * 2)

    def test_a_project_changed_and_changed_back_on_the_form_keeps_a_typed_deadline(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)

        with Form(task, view=self.deadline_form_view) as task_form:
            task_form.date_deadline = date(2026, 4, 1)
            task_form.project_id = self.project_plain
            task_form.project_id = self.project

        self.assertEqual(task.date_deadline, date(2026, 4, 1))

    def test_a_planned_end_changed_and_changed_back_on_the_task_form_brings_the_deadline_back(self):
        planned_end = datetime(2026, 3, 6, 12)
        task = self._create_task("Planned", planned_date_end=planned_end)

        with Form(task, view="project.view_task_form2") as task_form:
            task_form.planned_date_end = datetime(2026, 3, 16, 12)
            self.assertEqual(task_form.date_deadline, date(2026, 3, 16))
            task_form.planned_date_end = planned_end
            self.assertEqual(task_form.date_deadline, date(2026, 3, 6))

        self.assertEqual((task.planned_date_end, task.date_deadline), (planned_end, date(2026, 3, 6)))
        self._move_the_start_alone(task)
        self.assertEqual(task.date_deadline, date(2026, 3, 6))

    def test_a_subtask_planned_end_changed_and_changed_back_on_the_parent_form_brings_its_deadline_back(self):
        planned_end = datetime(2026, 3, 6, 12)
        parent = self._create_task("Parent")
        subtask = self._create_task(
            "Sub-task", parent_id=parent.id, planned_date_start=datetime(2026, 3, 2, 9), planned_date_end=planned_end
        )

        with Form(parent, view=self.subtask_planned_end_form_view) as parent_form:
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.planned_date_end = datetime(2026, 3, 16, 12)
                self.assertEqual(subtask_line.date_deadline, date(2026, 3, 16))
                subtask_line.planned_date_end = planned_end
                self.assertEqual(subtask_line.date_deadline, date(2026, 3, 6))

        self.assertEqual((subtask.planned_date_end, subtask.date_deadline), (planned_end, date(2026, 3, 6)))
        self._move_the_start_alone(subtask)
        self.assertEqual(subtask.date_deadline, date(2026, 3, 6))

    def test_an_occurrence_given_a_deadline_with_its_plan_keeps_it_until_its_plan_changes(self):
        for recurrence_update in ("subsequent", "all"):
            with self.subTest(recurrence_update=recurrence_update):
                recurrence = self.env["project.task.recurrence"].create({"repeat_unit": "week", "mon": True})
                occurrences = self._create_task("First", planned_date_end=MIDDAY_UTC, recurrence_id=recurrence.id)
                occurrences |= self._create_task("Second", planned_date_end=MIDDAY_UTC, recurrence_id=recurrence.id)

                occurrences[0].write(
                    {
                        "planned_date_end": datetime(2026, 3, 9, 12),
                        "date_deadline": date(2026, 4, 1),
                        "recurrence_update": recurrence_update,
                    }
                )
                self._move_the_start_alone(occurrences)

                self.assertEqual(occurrences.mapped("date_deadline"), [date(2026, 4, 1)] * 2)

                occurrences.planned_date_end = datetime(2026, 3, 16, 12)

                self.assertEqual(occurrences.mapped("date_deadline"), [date(2026, 3, 16)] * 2)

    def test_the_task_lines_of_the_task_form_carry_the_plan_their_deadline_follows(self):
        # read from the arch: Form cannot edit the Blocked By lines, a many2many
        task_form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        for lines_field_name in ("child_ids", "depend_on_ids"):
            with self.subTest(lines=lines_field_name):
                hidden_columns = task_form_arch.xpath(
                    "//field[@name='%s']/tree/field[@invisible='1']/@name" % lines_field_name
                )
                self.assertLessEqual(set(DEADLINE_PLAN_FIELDS), set(hidden_columns))

    def test_a_portal_collaborator_cannot_read_the_planned_end_of_the_deadline(self):
        collaborator = self.env["res.users"].create(self._user_vals("Sharing Collaborator", ["base.group_portal"]))
        shared_project = self.env["project.project"].create({"name": "Shared", "privacy_visibility": "portal"})
        self.env["project.collaborator"].create(
            {"project_id": shared_project.id, "partner_id": collaborator.partner_id.id}
        )
        shared_project.message_subscribe(partner_ids=collaborator.partner_id.ids)
        task = self._create_task("Shared", project=shared_project, planned_date_end=MIDDAY_UTC)
        shared_task = task.with_user(collaborator)

        self.assertEqual(shared_task.read(["name"])[0]["name"], "Shared")
        with self.assertRaises(AccessError):
            shared_task.read(["date_deadline_planned_end"])

    def test_a_deadline_typed_after_a_move_on_the_form_stays_while_a_subtask_is_edited(self):
        other_project = self.env["project.project"].create(
            {"name": "Other AI-driven Project", "is_ai_driven": True, "allow_subtasks": True}
        )
        parent = self._create_task("Parent")
        self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 12),
        )
        parent.date_deadline = date(2026, 4, 1)

        with Form(parent, view=self.deadline_form_view) as parent_form:
            parent_form.project_id = other_project
            self.assertEqual(parent_form.date_deadline, date(2026, 3, 6))
            parent_form.date_deadline = date(2026, 5, 1)
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.name = "Sub-task renamed"
            self.assertEqual(parent_form.date_deadline, date(2026, 5, 1))

        self.assertEqual((parent.project_id, parent.date_deadline), (other_project, date(2026, 5, 1)))

    def test_the_deadline_alone_does_not_move_the_plan(self):
        task = self._create_task("Planned", planned_date_start=datetime(2026, 3, 1, 9), planned_date_end=EVENING_UTC)

        task.date_deadline = date(2026, 4, 1)

        self.assertEqual(task.date_deadline, date(2026, 4, 1))
        self.assertEqual((task.planned_date_start, task.planned_date_end), (datetime(2026, 3, 1, 9), EVENING_UTC))

    def test_a_recompute_by_a_user_without_a_time_zone_keeps_an_aligned_deadline(self):
        task = self._create_task("Planned").with_user(self.planner_utc_plus_8)
        task.planned_date_end = EVENING_UTC
        self.assertEqual(task.date_deadline, date(2026, 3, 3))

        task_of_a_user_without_tz = task.with_user(self.planner_without_tz)

        self._move_the_start_alone(task_of_a_user_without_tz)

        self.assertEqual(task_of_a_user_without_tz.date_deadline, date(2026, 3, 3))

    def test_the_deadline_outside_ai_driven_projects_does_not_follow(self):
        undated = self._create_task("Undated", project=self.project_plain, planned_date_end=EVENING_UTC)
        dated = self._create_task(
            "Dated", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=date(2026, 4, 1)
        )

        dated.planned_date_end = datetime(2026, 3, 9, 9)

        self.assertFalse(undated.date_deadline)
        self.assertEqual(dated.date_deadline, date(2026, 4, 1))

    def test_the_next_occurrence_of_a_recurring_task_starts_unplanned(self):
        """Odoo 16 gives the next occurrence neither the Deadline nor the planned dates of the task
        it repeats: no stale plan to move; in an AI-driven project its Deadline follows its first plan."""
        weekly = {"recurring_task": True, "repeat_unit": "week", "repeat_type": "forever", "mon": True}
        for project in self.project | self.project_plain:
            project.allow_recurring_tasks = True
            for planned_dates in (
                {"planned_date_start": GANTT_START, "planned_date_end": MIDDAY_UTC},
                {"planned_date_end": MIDDAY_UTC},
            ):
                with self.subTest(project=project.name, planned_dates=list(planned_dates)):
                    task = self._create_task(
                        "Weekly", project=project, date_deadline=date(2026, 3, 6), **planned_dates, **weekly
                    )

                    task.recurrence_id._create_next_task()

                    occurrence = task.recurrence_id.task_ids - task
                    occurrence_plan = (occurrence.planned_date_start, occurrence.planned_date_end)
                    self.assertEqual(occurrence_plan, (False, False))
                    self.assertFalse(occurrence.date_deadline)

                    occurrence.planned_date_end = datetime(2026, 3, 9, 12)

                    expected_deadline = date(2026, 3, 9) if project.is_ai_driven else False
                    self.assertEqual(occurrence.date_deadline, expected_deadline)

    def test_a_rolled_up_parent_follows_its_planned_end_at_every_level(self):
        grandparent = self._create_task("Grandparent", date_deadline=date(2026, 6, 30))
        parent = self._create_task("Parent", parent_id=grandparent.id, date_deadline=date(2026, 6, 30))
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 9),
        )
        self.assertEqual((parent | grandparent).mapped("date_deadline"), [date(2026, 3, 6)] * 2)

        subtask.with_user(self.planner_utc_plus_8).planned_date_end = datetime(2026, 3, 9, 17, 30)

        self.assertEqual(grandparent.planned_date_end, subtask.planned_date_end)
        self.assertEqual(
            (parent | grandparent).with_user(self.planner_utc_plus_8).mapped("date_deadline"),
            [date(2026, 3, 10)] * 2,
        )

    def test_a_parent_keeps_a_deadline_set_by_hand_while_its_rolled_up_end_stays(self):
        parent = self._create_task("Parent")
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 12),
        )
        parent.date_deadline = date(2026, 4, 1)

        subtask.planned_date_start = datetime(2026, 3, 3, 9)

        self.assertEqual(parent.planned_date_start, datetime(2026, 3, 3, 9))
        self.assertEqual(parent.date_deadline, date(2026, 4, 1))

        subtask.planned_date_end = datetime(2026, 3, 9, 12)

        self.assertEqual(parent.date_deadline, date(2026, 3, 9))

    def test_a_parent_created_with_its_planned_dates_and_subtasks_follows_its_rolled_up_end(self):
        task_model = type(self.Task)
        populate_personal_stages = task_model._populate_missing_personal_stages

        def read_the_deadline_then_populate(tasks):
            tasks.mapped("date_deadline")
            return populate_personal_stages(tasks)

        def create_parent(name, **vals):
            subtask_vals = {
                "name": "Sub-task of %s" % name,
                "project_id": self.project.id,
                "planned_date_start": datetime(2026, 3, 2, 9),
                "planned_date_end": datetime(2026, 3, 9, 12),
            }
            return self._create_task(
                name,
                planned_date_start=datetime(2026, 3, 1, 9),
                planned_date_end=MIDDAY_UTC,
                child_ids=[Command.create(subtask_vals)],
                **vals,
            )

        # a create override of another module reads the Deadline before the roll-up has run
        with patch.object(task_model, "_populate_missing_personal_stages", read_the_deadline_then_populate):
            parent = create_parent("Parent")
            parent_set_by_hand = create_parent("Parent set by hand", date_deadline=date(2026, 4, 1))
        parents = parent | parent_set_by_hand

        self.assertEqual(parents.mapped("planned_date_end"), [datetime(2026, 3, 9, 12)] * 2)
        self.assertEqual(parents.mapped("date_deadline"), [date(2026, 3, 9), date(2026, 4, 1)])

        parents.child_ids.planned_date_start = datetime(2026, 3, 3, 9)

        self.assertEqual(parents.mapped("date_deadline"), [date(2026, 3, 9), date(2026, 4, 1)])

    def test_clearing_the_planned_end_keeps_the_deadline(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)
        parent = self._create_task("Parent")
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 12),
        )
        self.assertEqual((task | parent).mapped("date_deadline"), [date(2026, 3, 2), date(2026, 3, 6)])

        task.planned_date_end = False
        subtask.write({"planned_date_start": False, "planned_date_end": False})

        self.assertFalse(parent.planned_date_end)
        self.assertEqual((task | parent).mapped("date_deadline"), [date(2026, 3, 2), date(2026, 3, 6)])

    def test_a_task_moved_into_an_ai_driven_project_aligns_its_deadline(self):
        task = self._create_task(
            "Moving", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=date(2026, 4, 1)
        )

        task.with_user(self.planner_utc_plus_8).project_id = self.project

        self.assertEqual(task.with_user(self.planner_utc_plus_8).date_deadline, date(2026, 3, 3))

        task.date_deadline = date(2026, 4, 1)
        task.project_id = self.project_plain

        self.assertEqual(task.date_deadline, date(2026, 4, 1))

        task.with_user(self.planner_utc_plus_8).project_id = self.project

        self.assertEqual(task.with_user(self.planner_utc_plus_8).date_deadline, date(2026, 3, 3))

    def test_a_deadline_saved_with_a_move_into_another_ai_driven_project_stays(self):
        # Keyed projects: the move gives the task a new key, a write whose tracking reads the Deadline.
        first_project, second_project = (
            self.env["project.project"]
            .with_context(test_project_key=True)
            .create([{"name": "Keyed One", "is_ai_driven": True}, {"name": "Keyed Two", "is_ai_driven": True}])
        )
        task = self.Task.with_context(test_project_key=True).create(
            {"name": "Moving", "project_id": first_project.id, "planned_date_end": MIDDAY_UTC}
        )
        # saved in an earlier request: Odoo tracks no record created in the current one
        self.env.cr.flush()

        task.write({"project_id": second_project.id, "date_deadline": date(2026, 4, 1)})

        self.assertEqual((task.key, task.date_deadline), ("%s-1" % second_project.key, date(2026, 4, 1)))
        self._move_the_start_alone(task)
        self.assertEqual(task.date_deadline, date(2026, 4, 1))
        task.planned_date_end = datetime(2026, 3, 9, 12)
        self.assertEqual(task.date_deadline, date(2026, 3, 9))

    def test_flagging_a_project_leaves_the_deadlines_of_its_tasks_until_their_planned_end_changes(self):
        undated = self._create_task("Undated", project=self.project_plain, planned_date_end=MIDDAY_UTC)
        dated = self._create_task(
            "Dated", project=self.project_plain, planned_date_end=MIDDAY_UTC, date_deadline=date(2026, 4, 1)
        )
        tasks = undated | dated
        # saved before the project is ticked, as in two requests
        self.env.flush_all()

        self.project_plain.is_ai_driven = True
        self._move_the_start_alone(tasks)

        self.assertEqual(tasks.mapped("date_deadline"), [False, date(2026, 4, 1)])

        tasks.planned_date_end = datetime(2026, 3, 9, 12)

        self.assertEqual(tasks.mapped("date_deadline"), [date(2026, 3, 9)] * 2)

    def _write_in_sql(self, tasks, **values):
        self.env.flush_all()
        assignments = ", ".join("%s = %%s" % column for column in values)
        self.env.cr.execute(
            "UPDATE project_task SET %s WHERE id IN %%s" % assignments, (*values.values(), tuple(tasks.ids))
        )
        self.env.invalidate_all()

    def _write_dates(self, tasks):
        self.env.cr.execute("SELECT id, write_date FROM project_task WHERE id IN %s", (tuple(tasks.ids),))
        return dict(self.env.cr.fetchall())

    def test_a_subtask_change_that_leaves_the_plan_keeps_the_write_date_of_the_parent(self):
        parent = self._create_task("Parent", project=self.project_plain)
        self._create_task(
            "Outer sub-task",
            project=self.project_plain,
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 12),
        )
        inner_subtask = self._create_task(
            "Inner sub-task",
            project=self.project_plain,
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 3, 9),
            planned_date_end=datetime(2026, 3, 4, 12),
        )
        self._write_in_sql(parent, write_date=datetime(2026, 1, 5, 8))
        write_dates = self._write_dates(parent)
        # read() fetches the named fields alone, as a view does: the plan of the parent is not in cache
        parent.read(["parent_id", "child_ids", "project_id", "planned_date_start", "planned_date_end"])

        inner_subtask.planned_date_start = datetime(2026, 3, 3, 10)
        self.env.flush_all()

        self.assertEqual(self._write_dates(parent), write_dates)

    def test_install_and_update_align_the_open_tasks_of_ai_driven_projects(self):
        stale_deadline = date(2026, 6, 30)
        planner_groups = ["base.group_user", "project.group_project_user"]
        assignee_without_tz, planner_of_an_unknown_zone, archived_planner = self.env["res.users"].create(
            [
                dict(self._user_vals("Assignee Without Zone", planner_groups), tz=False),
                self._user_vals("Planner Of An Unknown Zone", planner_groups),
                dict(self._user_vals("Archived Planner", planner_groups), tz="Australia/Perth", active=False),
            ]
        )
        self.env.flush_all()
        # a zone pytz does not know: the ORM refuses it, SQL stores it
        self.env.cr.execute(
            "UPDATE res_partner SET tz = 'Mars/Olympus_Mons' WHERE id = %s", (planner_of_an_unknown_zone.partner_id.id,)
        )
        for name, align in (
            ("install", lambda: post_init_hook(self.env.cr, self.env.registry)),
            ("update", lambda: _run_update_migration(self.env.cr)),
        ):
            with self.subTest(name):
                open_task = self._create_task("Open", planned_date_end=EVENING_UTC)
                assigned_task = self._create_task(
                    "Assigned",
                    planned_date_end=EVENING_UTC,
                    user_ids=[Command.set((assignee_without_tz | self.planner_utc_plus_8).ids)],
                )
                unknown_zone_assignee_task = self._create_task(
                    "Unknown Zone Assignee",
                    planned_date_end=EVENING_UTC,
                    user_ids=[Command.set((planner_of_an_unknown_zone | self.planner_utc_plus_8).ids)],
                )
                undated_task = self._create_task("Without Deadline", planned_date_end=EVENING_UTC)
                unknown_zone_task = self._create_task("Unknown Zone", planned_date_end=EVENING_UTC)
                archived_assignee_task = self._create_task(
                    "Archived Assignee",
                    planned_date_end=EVENING_UTC,
                    user_ids=[Command.set((archived_planner | self.planner_utc).ids)],
                )
                archived_writer_task = self._create_task("Archived Writer", planned_date_end=EVENING_UTC)
                closed_task = self._create_task("Closed", planned_date_end=EVENING_UTC, stage_id=self.stage_done.id)
                archived_task = self._create_task("Archived", planned_date_end=EVENING_UTC, active=False)
                plain_task = self._create_task("Plain", project=self.project_plain, planned_date_end=EVENING_UTC)
                unplanned_task = self._create_task("Unplanned")
                tasks = (
                    open_task
                    | assigned_task
                    | unknown_zone_assignee_task
                    | undated_task
                    | unknown_zone_task
                    | archived_assignee_task
                    | archived_writer_task
                    | closed_task
                    | archived_task
                    | plain_task
                    | unplanned_task
                )
                # whatever plan the columns hold, the hooks record every task's
                empty_plan = dict.fromkeys(DEADLINE_PLAN_FIELDS)
                self._write_in_sql(
                    tasks, date_deadline=stale_deadline, write_date=datetime(2026, 1, 5, 8), **empty_plan
                )
                self._write_in_sql(open_task, write_uid=self.planner_utc_plus_8.id)
                self._write_in_sql(assigned_task | unknown_zone_assignee_task, write_uid=self.planner_utc.id)
                self._write_in_sql(undated_task, write_uid=self.planner_without_tz.id, date_deadline=None)
                self._write_in_sql(unknown_zone_task, write_uid=planner_of_an_unknown_zone.id)
                self._write_in_sql(archived_assignee_task, write_uid=self.planner_utc_plus_8.id)
                self._write_in_sql(archived_writer_task, write_uid=archived_planner.id)
                write_dates = self._write_dates(tasks)
                deadlines = [date(2026, 3, 3)] * 3 + [date(2026, 3, 2)] * 4 + [stale_deadline] * 4

                align()

                self.assertEqual(tasks.mapped("date_deadline"), deadlines)
                self.assertEqual(self._write_dates(tasks), write_dates)

                self._move_the_start_alone(tasks - unplanned_task)

                self.assertEqual(tasks.mapped("date_deadline"), deadlines)

    def test_missing_deadline_plan_columns_are_created_with_the_current_plan_of_every_task(self):
        aligned_task = self._create_task("Aligned", planned_date_end=MIDDAY_UTC)
        task_set_by_hand = self._create_task("Set by hand", planned_date_end=MIDDAY_UTC, date_deadline=date(2026, 4, 1))
        plain_task = self._create_task("Plain", project=self.project_plain)
        tasks = aligned_task | task_set_by_hand | plain_task
        self._write_in_sql(tasks, write_date=datetime(2026, 1, 5, 8))
        write_dates = self._write_dates(tasks)
        for field_name in DEADLINE_PLAN_FIELDS:
            self.env.cr.execute(sql.SQL("ALTER TABLE project_task DROP COLUMN {}").format(sql.Identifier(field_name)))

        # the attributes _auto_init uses exist only while a registry loads
        with (
            patch.object(self.registry, "_post_init_queue", deque(), create=True),
            patch.object(self.registry, "_foreign_keys", {}, create=True),
            patch.object(self.registry, "_is_install", False, create=True),
        ):
            self.Task._auto_init()

        self.assertEqual(
            [name for name in DEADLINE_PLAN_FIELDS if self.env.records_to_compute(self.Task._fields[name])], []
        )
        self.env.flush_all()
        self.assertEqual(self._write_dates(tasks), write_dates)
        self.env.cr.execute(
            "SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'project_task'"
            " AND column_name IN %s",
            (DEADLINE_PLAN_FIELDS,),
        )
        self.assertEqual(
            dict(self.env.cr.fetchall()),
            {"date_deadline_planned_end": "timestamp without time zone", "date_deadline_project_id": "integer"},
        )
        self.env.cr.execute(
            "SELECT date_deadline_planned_end, date_deadline_project_id FROM project_task WHERE id IN %s ORDER BY id",
            (tuple(tasks.ids),),
        )
        self.assertEqual(
            self.env.cr.fetchall(),
            [(MIDDAY_UTC, self.project.id), (MIDDAY_UTC, self.project.id), (None, self.project_plain.id)],
        )
        self.env.invalidate_all()

        self._move_the_start_alone(aligned_task | task_set_by_hand)

        self.assertEqual(tasks.mapped("date_deadline"), [date(2026, 3, 2), date(2026, 4, 1), False])
