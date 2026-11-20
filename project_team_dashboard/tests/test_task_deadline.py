from collections import deque
from datetime import datetime
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests import Form, tagged
from odoo.tools import SQL

from ..models.project_task import DEADLINE_PLAN_FIELDS
from .common import TeamDashboardCommon

# 01:30 on 3 March eight hours ahead of UTC, 12:30 on 2 March five hours behind
EVENING_UTC = datetime(2026, 3, 2, 17, 30)
MIDDAY_UTC = datetime(2026, 3, 2, 12)
GANTT_START = datetime(2026, 2, 27, 9)
TYPED_DEADLINE = datetime(2026, 4, 1, 12)
REPLANNED_END = datetime(2026, 3, 9, 12)
STALE_DEADLINE = datetime(2026, 6, 30, 12)


@tagged("post_install", "-at_install")
class TestTaskDeadline(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        planner_groups = ["base.group_user", "project.group_project_user"]
        cls.planner_utc_plus_8, cls.planner_utc_minus_5, cls.planner_without_tz = cls.env["res.users"].create(
            [
                dict(cls._user_vals("Planner Plus Eight", planner_groups), tz="Australia/Perth"),
                dict(cls._user_vals("Planner Minus Five", planner_groups), tz="America/New_York"),
                dict(cls._user_vals("Planner Without Zone", planner_groups), tz=False),
            ]
        )
        # The sub-tasks editable in the list itself; the plan fields as on the module's form.
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
                            <list editable="bottom">
                                <field name="name"/>
                                <field name="date_deadline"/>
                            </list>
                        </field>
                    </form>
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

    def _create_parent_and_subtask(self, **parent_vals):
        parent = self._create_task("Parent", **parent_vals)
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 12),
        )
        return parent, subtask

    def _move_the_start_alone(self, tasks):
        """Write what the Gantt chart writes when a bar's start is dragged: both planned dates."""
        for task in tasks:
            task.write({"planned_date_start": GANTT_START, "planned_date_end": task.planned_date_end})

    def _get_followed_plans(self, tasks):
        return [tuple(task[field_name] for field_name in DEADLINE_PLAN_FIELDS) for task in tasks]

    def _form_deadline(self, task_form):
        # Form holds a datetime as typed, or as the string an onchange returns
        return fields.Datetime.to_datetime(task_form.date_deadline)

    def test_the_deadline_keeps_the_attributes_core_gives_it(self):
        deadline_field = self.Task._fields["date_deadline"]

        self.assertEqual(
            (deadline_field.string, deadline_field.index, deadline_field.tracking, deadline_field.copy),
            ("Deadline", True, 7, False),
        )
        self.assertEqual((deadline_field.store, deadline_field.readonly), (True, False))

    def test_savers_in_different_time_zones_give_the_same_deadline(self):
        for planner in (self.planner_utc_plus_8, self.planner_utc_minus_5, self.planner_without_tz):
            with self.subTest(planner=planner.name):
                task = self._create_task("Planned by %s" % planner.name).with_user(planner)

                task.planned_date_end = EVENING_UTC

                self.assertEqual(task.date_deadline, EVENING_UTC)

    def test_create_with_a_planned_end_sets_the_deadline(self):
        task = self.Task.with_user(self.planner_utc_plus_8).create(
            {"name": "Planned", "project_id": self.project.id, "planned_date_end": EVENING_UTC}
        )
        self.assertEqual(task.date_deadline, EVENING_UTC)
        self.assertEqual(self._get_followed_plans(task), [(EVENING_UTC, self.project)])

    def test_a_deadline_saved_with_the_planned_end_stays_until_the_planned_end_changes(self):
        created_with_both = self._create_task("Planned", planned_date_end=MIDDAY_UTC, date_deadline=TYPED_DEADLINE)
        created_by_default = self.Task.with_context(default_date_deadline=TYPED_DEADLINE).create(
            {"name": "From the calendar", "project_id": self.project.id, "planned_date_end": MIDDAY_UTC}
        )
        written_with_both = self._create_task("Replanned", planned_date_end=MIDDAY_UTC)
        written_with_both.write({"planned_date_end": datetime(2026, 3, 1, 12), "date_deadline": TYPED_DEADLINE})
        self.env["ir.default"].set("project.task", "date_deadline", fields.Datetime.to_string(TYPED_DEADLINE))
        created_from_a_default = self._create_task("From a default", planned_date_end=MIDDAY_UTC)
        tasks = created_with_both | created_by_default | written_with_both | created_from_a_default
        self.assertEqual(tasks.mapped("date_deadline"), [TYPED_DEADLINE] * 4)

        self._move_the_start_alone(tasks)

        self.assertEqual(tasks.mapped("date_deadline"), [TYPED_DEADLINE] * 4)

        tasks.planned_date_end = REPLANNED_END

        self.assertEqual(tasks.mapped("date_deadline"), [REPLANNED_END] * 4)

    def test_a_deadline_set_by_hand_stays_until_the_planned_end_changes(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)
        task.date_deadline = TYPED_DEADLINE

        self._move_the_start_alone(task)

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

        task.planned_date_end = REPLANNED_END

        self.assertEqual(task.date_deadline, REPLANNED_END)

    def test_a_stage_change_keeps_a_deadline_set_by_hand(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC, date_deadline=TYPED_DEADLINE)

        task.stage_id = self.stage_other
        task.state = "1_done"
        # the same planned end saved again computes the Deadline again
        task.planned_date_end = MIDDAY_UTC

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

    def test_on_the_form_the_deadline_follows_the_planned_end_and_one_typed_after_it_stays_once_saved(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)

        with Form(task.with_user(self.planner_utc_plus_8)) as task_form:
            task_form.planned_date_end = EVENING_UTC
            self.assertEqual(self._form_deadline(task_form), EVENING_UTC)
            task_form.date_deadline = TYPED_DEADLINE

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)
        self._move_the_start_alone(task)
        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

    def test_a_subtask_edited_on_the_parent_form_keeps_the_deadlines_set_by_hand(self):
        parent, subtask = self._create_parent_and_subtask()
        (parent | subtask).date_deadline = TYPED_DEADLINE

        with Form(parent, view=self.deadline_form_view) as parent_form:
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.name = "Sub-task renamed"

        self.assertEqual(subtask.name, "Sub-task renamed")
        self.assertEqual((parent | subtask).mapped("date_deadline"), [TYPED_DEADLINE] * 2)

    def test_a_project_changed_and_changed_back_on_the_form_keeps_a_typed_deadline(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)

        with Form(task, view=self.deadline_form_view) as task_form:
            task_form.date_deadline = TYPED_DEADLINE
            task_form.project_id = self.project_plain
            task_form.project_id = self.project

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

    def test_a_planned_end_changed_and_changed_back_on_the_task_form_brings_the_deadline_back(self):
        planned_end = datetime(2026, 3, 6, 12)
        task = self._create_task("Planned", planned_date_end=planned_end)

        with Form(task, view="project.view_task_form2") as task_form:
            task_form.planned_date_end = datetime(2026, 3, 16, 12)
            self.assertEqual(self._form_deadline(task_form), datetime(2026, 3, 16, 12))
            task_form.planned_date_end = planned_end
            self.assertEqual(self._form_deadline(task_form), planned_end)

        self.assertEqual((task.planned_date_end, task.date_deadline), (planned_end, planned_end))

    def test_a_deadline_typed_after_a_move_on_the_form_stays_while_a_subtask_is_edited(self):
        other_project = self.env["project.project"].create({"name": "Other AI-driven Project", "is_ai_driven": True})
        parent, _subtask = self._create_parent_and_subtask()
        parent.date_deadline = TYPED_DEADLINE
        typed_after_the_move = datetime(2026, 5, 1, 12)

        with Form(parent, view=self.deadline_form_view) as parent_form:
            parent_form.project_id = other_project
            self.assertEqual(self._form_deadline(parent_form), datetime(2026, 3, 6, 12))
            parent_form.date_deadline = typed_after_the_move
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.name = "Sub-task renamed"
            self.assertEqual(self._form_deadline(parent_form), typed_after_the_move)

        self.assertEqual((parent.project_id, parent.date_deadline), (other_project, typed_after_the_move))

    def test_a_deadline_typed_after_a_move_on_a_subtask_line_stays_when_the_line_moves_away_and_back(self):
        other_project = self.env["project.project"].create({"name": "Other AI-driven Project", "is_ai_driven": True})
        parent, subtask = self._create_parent_and_subtask()
        subtask.date_deadline = STALE_DEADLINE

        with Form(parent, view="project.view_task_form2") as parent_form:
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.project_id = other_project
                self.assertEqual(self._form_deadline(subtask_line), datetime(2026, 3, 6, 12))
                subtask_line.date_deadline = TYPED_DEADLINE
            with parent_form.child_ids.edit(0) as subtask_line:
                subtask_line.project_id = self.project_plain
                subtask_line.project_id = other_project
                self.assertEqual(self._form_deadline(subtask_line), TYPED_DEADLINE)

        self.assertEqual((subtask.project_id, subtask.date_deadline), (other_project, TYPED_DEADLINE))

    def test_the_task_lines_of_the_task_form_carry_the_plan_their_deadline_follows(self):
        # read from the arch: Form cannot edit the Blocked By lines, a many2many
        task_form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        for lines_field_name in ("child_ids", "depend_on_ids"):
            with self.subTest(lines=lines_field_name):
                hidden_columns = task_form_arch.xpath(
                    "//field[@name='%s']/list/field[@column_invisible='True']/@name" % lines_field_name
                )
                self.assertLessEqual(set(DEADLINE_PLAN_FIELDS), set(hidden_columns))

    def test_the_deadline_alone_does_not_move_the_plan_and_is_set_against_it(self):
        task = self._create_task("Planned", planned_date_start=datetime(2026, 3, 1, 9), planned_date_end=EVENING_UTC)
        # a planned end moved in SQL, past the plan the Deadline followed
        self._write_in_sql(task, date_deadline_planned_end=MIDDAY_UTC)

        task.date_deadline = TYPED_DEADLINE

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)
        self.assertEqual((task.planned_date_start, task.planned_date_end), (datetime(2026, 3, 1, 9), EVENING_UTC))
        self.assertEqual(self._get_followed_plans(task), [(EVENING_UTC, self.project)])

        self._move_the_start_alone(task)

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

    def test_a_portal_collaborator_creates_a_task_with_a_default_deadline(self):
        portal_user = self.env["res.users"].create(self._user_vals("Portal Collaborator", ["base.group_portal"]))
        self.project.write(
            {
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": portal_user.partner_id.id, "access_mode": "edit"})],
            }
        )
        self.env["ir.default"].set("project.task", "date_deadline", fields.Datetime.to_string(TYPED_DEADLINE))

        task = (
            self.Task.with_user(portal_user)
            .with_context(default_project_id=self.project.id)
            .create({"name": "From the portal"})
        )

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)
        self.assertEqual(self._get_followed_plans(task.sudo()), [(False, self.project)])

    def test_the_deadline_outside_ai_driven_projects_does_not_follow(self):
        undated = self._create_task("Undated", project=self.project_plain, planned_date_end=EVENING_UTC)
        dated = self._create_task(
            "Dated", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=TYPED_DEADLINE
        )

        dated.planned_date_end = datetime(2026, 3, 9, 9)

        self.assertFalse(undated.date_deadline)
        self.assertEqual(dated.date_deadline, TYPED_DEADLINE)

    def test_a_rolled_up_parent_follows_its_planned_end_at_every_level(self):
        grandparent = self._create_task("Grandparent", date_deadline=STALE_DEADLINE)
        parent = self._create_task("Parent", parent_id=grandparent.id, date_deadline=STALE_DEADLINE)
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 9),
        )
        self.assertEqual((parent | grandparent).mapped("date_deadline"), [datetime(2026, 3, 6, 9)] * 2)

        subtask.with_user(self.planner_utc_plus_8).planned_date_end = datetime(2026, 3, 9, 17, 30)

        self.assertEqual(grandparent.planned_date_end, subtask.planned_date_end)
        self.assertEqual((parent | grandparent).mapped("date_deadline"), [datetime(2026, 3, 9, 17, 30)] * 2)

    def test_a_planned_start_read_first_rolls_up_every_level_and_their_deadlines(self):
        grandparent = self._create_task("Grandparent")
        parent = self._create_task("Parent", parent_id=grandparent.id)
        self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 3, 9),
            planned_date_end=datetime(2026, 3, 8, 9),
        )
        planned_dates = (datetime(2026, 3, 3, 9), datetime(2026, 3, 8, 9))

        # read first, the start of the parent is rolled up while a search reads its sub-tasks
        self.assertEqual((parent.planned_date_start, parent.planned_date_end), planned_dates)

        self.assertEqual((grandparent.planned_date_start, grandparent.planned_date_end), planned_dates)
        self.assertEqual((parent | grandparent).mapped("date_deadline"), [datetime(2026, 3, 8, 9)] * 2)

    def test_a_rollup_computes_no_deadline_while_it_reads_the_subtasks(self):
        task_model = type(self.Task)
        subtask_date_spans = task_model._subtask_date_spans
        compute_date_deadline = task_model._compute_date_deadline
        rollups_running = []
        deadlines_computed_in_a_rollup = []

        def note_the_rollup(tasks):
            rollups_running.append(tasks)
            try:
                return subtask_date_spans(tasks)
            finally:
                rollups_running.pop()

        def note_the_deadline(tasks):
            if rollups_running:
                deadlines_computed_in_a_rollup.append(tasks)
            return compute_date_deadline(tasks)

        grandparent = self._create_task("Grandparent")
        parent = self._create_task("Parent", parent_id=grandparent.id)
        subtask = self._create_task(
            "Sub-task",
            parent_id=parent.id,
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=datetime(2026, 3, 6, 9),
        )
        self.env.flush_all()
        # a later request: no sub-task in cache
        self.env.invalidate_all()

        with (
            patch.object(task_model, "_subtask_date_spans", note_the_rollup),
            patch.object(task_model, "_compute_date_deadline", note_the_deadline),
        ):
            subtask.planned_date_end = REPLANNED_END
            # read first, the start rolls up while the Deadlines wait to be computed
            self.assertEqual(grandparent.planned_date_start, datetime(2026, 3, 2, 9))
            self.env.flush_all()

        self.assertEqual(deadlines_computed_in_a_rollup, [])
        self.assertEqual((subtask | parent | grandparent).mapped("date_deadline"), [REPLANNED_END] * 3)

    def test_a_parent_keeps_a_deadline_set_by_hand_while_its_rolled_up_end_stays(self):
        parent, subtask = self._create_parent_and_subtask()
        parent.date_deadline = TYPED_DEADLINE

        subtask.planned_date_start = datetime(2026, 3, 3, 9)

        self.assertEqual(parent.planned_date_start, datetime(2026, 3, 3, 9))
        self.assertEqual(parent.date_deadline, TYPED_DEADLINE)

        subtask.planned_date_end = REPLANNED_END

        self.assertEqual(parent.date_deadline, REPLANNED_END)

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
                "planned_date_end": REPLANNED_END,
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
            parent_set_by_hand = create_parent("Parent set by hand", date_deadline=TYPED_DEADLINE)
        parents = parent | parent_set_by_hand

        self.assertEqual(parents.mapped("planned_date_end"), [REPLANNED_END] * 2)
        self.assertEqual(parents.mapped("date_deadline"), [REPLANNED_END, TYPED_DEADLINE])

        parents.child_ids.planned_date_start = datetime(2026, 3, 3, 9)

        self.assertEqual(parents.mapped("date_deadline"), [REPLANNED_END, TYPED_DEADLINE])

    def test_clearing_the_planned_end_keeps_the_deadline(self):
        task = self._create_task("Planned", planned_date_end=MIDDAY_UTC)
        parent, subtask = self._create_parent_and_subtask()
        self.assertEqual((task | parent).mapped("date_deadline"), [MIDDAY_UTC, datetime(2026, 3, 6, 12)])

        task.planned_date_end = False
        subtask.write({"planned_date_start": False, "planned_date_end": False})

        self.assertFalse(parent.planned_date_end)
        self.assertEqual((task | parent).mapped("date_deadline"), [MIDDAY_UTC, datetime(2026, 3, 6, 12)])

    def test_a_task_moved_into_an_ai_driven_project_aligns_its_deadline(self):
        task = self._create_task(
            "Moving", project=self.project_plain, planned_date_end=EVENING_UTC, date_deadline=TYPED_DEADLINE
        )

        task.project_id = self.project

        self.assertEqual(task.date_deadline, EVENING_UTC)

        task.date_deadline = TYPED_DEADLINE
        task.project_id = self.project_plain

        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

        task.project_id = self.project

        self.assertEqual(task.date_deadline, EVENING_UTC)

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

        task.with_context(test_project_key=True).write(
            {"project_id": second_project.id, "date_deadline": TYPED_DEADLINE}
        )

        self.assertEqual((task.key, task.date_deadline), ("%s-1" % second_project.key, TYPED_DEADLINE))
        self._move_the_start_alone(task)
        self.assertEqual(task.date_deadline, TYPED_DEADLINE)
        task.planned_date_end = REPLANNED_END
        self.assertEqual(task.date_deadline, REPLANNED_END)

    def test_flagging_a_project_leaves_the_deadlines_of_its_tasks_until_their_planned_end_changes(self):
        undated = self._create_task("Undated", project=self.project_plain, planned_date_end=MIDDAY_UTC)
        dated = self._create_task(
            "Dated", project=self.project_plain, planned_date_end=MIDDAY_UTC, date_deadline=TYPED_DEADLINE
        )
        tasks = undated | dated
        # saved before the project is ticked, as in two requests
        self.env.flush_all()

        self.project_plain.is_ai_driven = True
        self._move_the_start_alone(tasks)

        self.assertEqual(tasks.mapped("date_deadline"), [False, TYPED_DEADLINE])

        tasks.planned_date_end = REPLANNED_END

        self.assertEqual(tasks.mapped("date_deadline"), [REPLANNED_END] * 2)

    def test_the_next_occurrence_keeps_the_postponed_deadline_until_its_planned_end_changes(self):
        self.project.allow_recurring_tasks = True
        task = self._create_task("Weekly", planned_date_end=MIDDAY_UTC, date_deadline=TYPED_DEADLINE)
        task.write({"recurring_task": True, "repeat_type": "forever"})

        task.state = "1_done"

        occurrence = task.recurrence_id.task_ids - task
        next_end, next_deadline = datetime(2026, 3, 9, 12), datetime(2026, 4, 8, 12)
        self.assertEqual(len(occurrence), 1)
        self.assertEqual((occurrence.planned_date_end, occurrence.date_deadline), (next_end, next_deadline))
        self.assertEqual(self._get_followed_plans(occurrence), [(next_end, self.project)])
        self.assertEqual(task.date_deadline, TYPED_DEADLINE)

        self._move_the_start_alone(occurrence)

        self.assertEqual(occurrence.date_deadline, next_deadline)

        occurrence.planned_date_end = datetime(2026, 3, 10, 12)

        self.assertEqual(occurrence.date_deadline, datetime(2026, 3, 10, 12))

    def test_a_task_made_from_a_template_gets_no_deadline_until_it_is_planned(self):
        template = self._create_task(
            "Dated template",
            planned_date_start=datetime(2026, 3, 2, 9),
            planned_date_end=MIDDAY_UTC,
            is_template=True,
        )
        self.assertEqual(template.date_deadline, MIDDAY_UTC)

        task = self.Task.browse(template.action_create_from_template())

        self.assertEqual((task.planned_date_end, task.date_deadline), (False, False))
        self.assertEqual(self._get_followed_plans(task), [(False, self.project)])

        task.planned_date_end = REPLANNED_END

        self.assertEqual(task.date_deadline, REPLANNED_END)

    def test_a_copy_takes_its_planned_end_as_its_deadline(self):
        tasks = self._create_task("Aligned", planned_date_end=MIDDAY_UTC) | self._create_task(
            "Set by hand", planned_date_end=MIDDAY_UTC, date_deadline=TYPED_DEADLINE
        )

        copies = tasks.copy()

        self.assertEqual(copies.mapped("planned_date_end"), [MIDDAY_UTC] * 2)
        self.assertEqual(copies.mapped("date_deadline"), [MIDDAY_UTC] * 2)
        self.assertEqual(self._get_followed_plans(copies), [(MIDDAY_UTC, self.project)] * 2)

        copies.planned_date_end = REPLANNED_END

        self.assertEqual(copies.mapped("date_deadline"), [REPLANNED_END] * 2)
        self.assertEqual(tasks.mapped("date_deadline"), [MIDDAY_UTC, TYPED_DEADLINE])

    def _write_in_sql(self, tasks, **values):
        self.env.flush_all()
        assignments = SQL(", ").join(SQL("%s = %s", SQL.identifier(column), value) for column, value in values.items())
        self.env.cr.execute(SQL("UPDATE project_task SET %s WHERE id IN %s", assignments, tuple(tasks.ids)))
        self.env.invalidate_all()

    def _write_dates(self, tasks):
        self.env.cr.execute("SELECT id, write_date FROM project_task WHERE id IN %s", (tuple(tasks.ids),))
        return dict(self.env.cr.fetchall())

    def _add_deadline_plan_columns(self, field_names):
        """Add the columns of field_names again as an update adds them; return how often the plan was filled."""
        for field_name in field_names:
            self.env.cr.execute(SQL("ALTER TABLE project_task DROP COLUMN %s", SQL.identifier(field_name)))
        task_model = type(self.Task)
        fill_plan_columns = task_model._fill_deadline_plan_columns
        # the attributes _auto_init uses exist only while a registry loads
        post_init_queue = deque()
        with (
            patch.object(self.registry, "_post_init_queue", post_init_queue, create=True),
            patch.object(self.registry, "_foreign_keys", {}, create=True),
            patch.object(self.registry, "_is_install", False, create=True),
            patch.object(
                task_model, "_fill_deadline_plan_columns", autospec=True, side_effect=fill_plan_columns
            ) as fill_mock,
        ):
            self.Task._auto_init()
            while post_init_queue:
                post_init_queue.popleft()()
        return fill_mock.call_count

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

    def test_added_deadline_plan_columns_record_the_current_plan_and_compute_no_deadline(self):
        open_task = self._create_task("Open", planned_date_end=MIDDAY_UTC)
        done_task = self._create_task("Done", planned_date_end=MIDDAY_UTC, state="1_done")
        set_by_hand_task = self._create_task("Set by hand", planned_date_end=MIDDAY_UTC, date_deadline=TYPED_DEADLINE)
        plain_task = self._create_task("Plain", project=self.project_plain)
        tasks = open_task | done_task | set_by_hand_task | plain_task
        # a Deadline the rule would move, as on a database updated from 20.0.1.0.0
        self._write_in_sql(open_task | done_task, date_deadline=STALE_DEADLINE)
        self._write_in_sql(tasks, write_date=datetime(2026, 1, 5, 8))
        write_dates = self._write_dates(tasks)
        deadlines = [STALE_DEADLINE, STALE_DEADLINE, TYPED_DEADLINE, False]

        fill_count = self._add_deadline_plan_columns(DEADLINE_PLAN_FIELDS)

        self.assertEqual(fill_count, 1)
        computed_fields = ("date_deadline", *DEADLINE_PLAN_FIELDS)
        self.assertEqual([name for name in computed_fields if self.env.records_to_compute(self.Task._fields[name])], [])
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
        self.env.invalidate_all()
        self.assertEqual(tasks.mapped("date_deadline"), deadlines)
        self.assertEqual(
            self._get_followed_plans(tasks),
            [(MIDDAY_UTC, self.project)] * 3 + [(False, self.project_plain)],
        )

        self._move_the_start_alone(tasks - plain_task)

        self.assertEqual(tasks.mapped("date_deadline"), deadlines)

        (open_task | done_task).planned_date_end = REPLANNED_END

        self.assertEqual((open_task | done_task).mapped("date_deadline"), [REPLANNED_END] * 2)

    def test_the_planned_end_column_added_alone_records_the_current_plan(self):
        task = self._create_task("Open", planned_date_end=MIDDAY_UTC)

        fill_count = self._add_deadline_plan_columns(["date_deadline_planned_end"])

        self.assertEqual(fill_count, 1)
        self.env.invalidate_all()
        self.assertEqual(self._get_followed_plans(task), [(MIDDAY_UTC, self.project)])
