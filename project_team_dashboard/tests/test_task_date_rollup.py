import json
from datetime import date, datetime, timedelta

from lxml import etree
from psycopg2 import sql

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import Form, tagged

from ..hooks import roll_up_planned_dates
from ..models.project_task import PLANNED_DATE_FIELDS
from ..models.project_task import Task as DashboardTask
from .common import TeamDashboardCommon

READ_ONLY_ON_ROLLED_UP_TASKS = [["is_planned_from_subtasks", "=", True]]
# With project_timeline and the glue module, these views show the planned dates instead of this module's.
TIMELINE_VIEWS = (
    "project_timeline.view_task_form2",
    "project_timeline.view_task_tree2",
    "project_team_dashboard_timeline.project_task_view_form_planned_dates",
    "project_team_dashboard_timeline.project_task_view_tree_planned_dates",
    "project_team_dashboard_timeline.view_task_form2_planned_dates_once",
    "project_team_dashboard_timeline.view_task_tree2_planned_dates_once",
)


@tagged("post_install", "-at_install")
class TestTaskDateRollup(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Task = cls.env["project.task"]
        cls.project.allow_subtasks = True
        cls.day_one = datetime(2026, 3, 2, 9)

    def _day(self, offset):
        return self.day_one + timedelta(days=offset)

    def _create_task(self, name, date_start=False, date_end=False, **vals):
        task_vals = {
            "name": name,
            "project_id": self.project.id,
            "stage_id": self.stage_in_progress.id,
            "planned_date_start": date_start,
            "planned_date_end": date_end,
        }
        task_vals.update(vals)
        return self.Task.create(task_vals)

    def _assert_planned_dates(self, task, date_start, date_end):
        self.assertEqual((task.planned_date_start, task.planned_date_end), (date_start, date_end))

    def test_leaf_task_dates_stay_manual(self):
        task = self._create_task("Leaf", self._day(0), self._day(3))
        self.assertFalse(task.is_planned_from_subtasks)
        task.write({"planned_date_start": self._day(1), "planned_date_end": self._day(5)})
        self._assert_planned_dates(task, self._day(1), self._day(5))

    def test_parent_takes_earliest_start_and_latest_end_of_subtasks(self):
        """`_compute_planned_date_start` and `_compute_planned_date_end` follow the active sub-tasks,
        and `_compute_is_planned_from_subtasks` flags the parent."""
        parent = self._create_task("Parent", self._day(0), self._day(30))
        early = self._create_task("Early", self._day(2), self._day(4), parent_id=parent.id)
        late = self._create_task("Late", self._day(3), self._day(9), parent_id=parent.id)
        self._assert_planned_dates(parent, early.planned_date_start, late.planned_date_end)
        self.assertTrue(parent.is_planned_from_subtasks)

        late.planned_date_end = self._day(12)
        self.assertEqual(parent.planned_date_end, late.planned_date_end)

        added = self._create_task("Added", self._day(1), self._day(2), parent_id=parent.id)
        self.assertEqual(parent.planned_date_start, added.planned_date_start)

        added.unlink()
        self.assertEqual(parent.planned_date_start, early.planned_date_start)

        late.active = False
        self.assertEqual(parent.planned_date_end, early.planned_date_end)
        late.active = True
        self.assertEqual(parent.planned_date_end, late.planned_date_end)

    def test_reparenting_rolls_up_old_and_new_parent(self):
        old_parent = self._create_task("Old Parent")
        new_parent = self._create_task("New Parent")
        staying = self._create_task("Staying", self._day(0), self._day(2), parent_id=old_parent.id)
        moving = self._create_task("Moving", self._day(5), self._day(8), parent_id=old_parent.id)
        self._assert_planned_dates(old_parent, staying.planned_date_start, moving.planned_date_end)

        moving.parent_id = new_parent
        self._assert_planned_dates(old_parent, staying.planned_date_start, staying.planned_date_end)
        self._assert_planned_dates(new_parent, moving.planned_date_start, moving.planned_date_end)

    def test_parent_with_undated_subtasks_has_no_dates(self):
        parent = self._create_task("Parent", self._day(0), self._day(4))
        undated = self._create_task("Undated", parent_id=parent.id)
        self._assert_planned_dates(parent, False, False)
        self.assertTrue(parent.is_planned_from_subtasks)
        with self.assertRaises(UserError):
            parent.write({"planned_date_start": self._day(1), "planned_date_end": self._day(6)})
        parent.write({"planned_date_start": False, "planned_date_end": False})
        self._assert_planned_dates(parent, False, False)

        undated.write({"planned_date_start": self._day(2), "planned_date_end": self._day(3)})
        self._assert_planned_dates(parent, self._day(2), self._day(3))

    def test_half_dated_subtasks_drive_both_sides(self):
        parent = self._create_task("Parent", self._day(0), self._day(20))
        start_only = self._create_task("Start Only", self._day(3), parent_id=parent.id)
        self._create_task("Undated", parent_id=parent.id)
        self._assert_planned_dates(parent, start_only.planned_date_start, start_only.planned_date_start)
        self.assertTrue(parent.is_planned_from_subtasks)

        end_only = self._create_task("End Only", date_end=self._day(1), parent_id=parent.id)
        self._assert_planned_dates(parent, end_only.planned_date_end, start_only.planned_date_start)
        with self.assertRaises(UserError):
            parent.planned_date_end = self._day(25)
        with self.assertRaises(UserError):
            parent.planned_date_start = self._day(0)

    def test_assigned_undated_subtask_leaves_its_parent_undated(self):
        now = fields.Datetime.now()
        parent = self._create_task("Parent", now - timedelta(days=60), now - timedelta(days=30))
        subtask = self.Task.create(
            {
                "name": "Assigned",
                "project_id": self.project.id,
                "parent_id": parent.id,
                "user_ids": [Command.set(self.user_dev1.ids)],
            }
        )
        self.assertTrue(subtask.date_assign)
        self._assert_planned_dates(subtask, False, False)
        self._assert_planned_dates(parent, False, False)

    def test_undated_subtask_moved_to_a_folded_stage_leaves_its_parent_undated(self):
        now = fields.Datetime.now()
        parent = self._create_task("Parent", now + timedelta(days=30), now + timedelta(days=60))
        subtask = self.Task.create(
            {
                "name": "Closing",
                "project_id": self.project.id,
                "parent_id": parent.id,
                "stage_id": self.stage_in_progress.id,
            }
        )
        subtask.stage_id = self.stage_done
        self.assertTrue(subtask.date_end)
        self._assert_planned_dates(subtask, False, False)
        self._assert_planned_dates(parent, False, False)

    def test_milestone_rolls_up_through_every_level(self):
        milestone = self._create_task("Milestone", self._day(40), self._day(40), task_type="milestone")
        task = self._create_task("Task", parent_id=milestone.id)
        subtask = self._create_task("Sub-task", self._day(1), self._day(6), parent_id=task.id)
        self._assert_planned_dates(task, subtask.planned_date_start, subtask.planned_date_end)
        self._assert_planned_dates(milestone, subtask.planned_date_start, subtask.planned_date_end)

        subtask.planned_date_end = self._day(8)
        self.assertEqual(task.planned_date_end, subtask.planned_date_end)
        self.assertEqual(milestone.planned_date_end, subtask.planned_date_end)
        with self.assertRaises(UserError):
            milestone.planned_date_end = self._day(40)

    def test_parent_in_closed_stage_is_still_rolled_up_and_locked(self):
        parent = self._create_task("Closed Parent", self._day(0), self._day(30), stage_id=self.stage_done.id)
        child = self._create_task("Child", self._day(4), self._day(7), parent_id=parent.id)
        self._assert_planned_dates(parent, child.planned_date_start, child.planned_date_end)
        with self.assertRaises(UserError):
            parent.planned_date_end = self._day(30)

    def test_archived_subtasks_are_ignored(self):
        parent = self._create_task("Parent", self._day(0), self._day(30))
        archived = self._create_task("Archived", self._day(10), self._day(12), parent_id=parent.id, active=False)
        self._assert_planned_dates(parent, self._day(0), self._day(30))

        child = self._create_task("Child", self._day(4), self._day(7), parent_id=parent.id)
        self._assert_planned_dates(parent, child.planned_date_start, child.planned_date_end)
        self.assertNotEqual(parent.planned_date_end, archived.planned_date_end)

        child.active = False
        self.assertFalse(parent.is_planned_from_subtasks)
        parent.write({"planned_date_start": self._day(1), "planned_date_end": self._day(2)})
        self._assert_planned_dates(parent, self._day(1), self._day(2))

    def test_rollup_never_touches_the_deadline(self):
        deadline = date(2026, 6, 30)
        parent = self._create_task("Parent", date_deadline=deadline)
        child = self._create_task("Child", self._day(4), self._day(7), parent_id=parent.id)
        child.planned_date_end = self._day(9)
        self.assertEqual(parent.planned_date_end, child.planned_date_end)
        self.assertEqual(parent.date_deadline, deadline)

    def test_write_of_other_value_on_rolled_up_side_is_refused(self):
        """`_check_subtask_driven_dates` refuses a write that moves a rolled-up date."""
        parent = self._create_task("Parent")
        self._create_task("Child", self._day(4), self._day(7), parent_id=parent.id)
        with self.assertRaisesRegex(UserError, "Parent"):
            parent.write({"planned_date_start": self._day(1)})

    def test_write_of_rolled_up_value_is_allowed(self):
        """`_check_subtask_driven_dates` lets a write of the rolled-up values through."""
        parent = self._create_task("Parent")
        child = self._create_task("Child", self._day(4), self._day(7), parent_id=parent.id)
        parent.write({"planned_date_start": parent.planned_date_start, "planned_date_end": parent.planned_date_end})
        self._assert_planned_dates(parent, child.planned_date_start, child.planned_date_end)

    def test_parent_created_with_subtasks_rolls_up(self):
        parent = self._create_task(
            "Parent",
            self._day(0),
            self._day(30),
            child_ids=[
                Command.create(
                    {
                        "name": "Child",
                        "project_id": self.project.id,
                        "planned_date_start": self._day(5),
                        "planned_date_end": self._day(6),
                    }
                ),
            ],
        )
        self._assert_planned_dates(parent, parent.child_ids.planned_date_start, parent.child_ids.planned_date_end)

    def test_write_with_new_subtasks_lets_the_rollup_decide(self):
        parent = self._create_task("Parent")
        existing = self._create_task("Existing", self._day(4), self._day(7), parent_id=parent.id)
        parent.write(
            {
                "planned_date_start": self._day(0),
                "child_ids": [
                    Command.create(
                        {
                            "name": "Added",
                            "project_id": self.project.id,
                            "planned_date_start": self._day(6),
                            "planned_date_end": self._day(10),
                        }
                    ),
                ],
            }
        )
        added = parent.child_ids - existing
        self._assert_planned_dates(parent, existing.planned_date_start, added.planned_date_end)

    def test_write_linking_dated_subtasks_overrides_the_written_dates(self):
        parent = self._create_task("Parent", self._day(0), self._day(30))
        orphan = self._create_task("Orphan", self._day(4), self._day(7))
        parent.write(
            {
                "planned_date_start": self._day(1),
                "planned_date_end": self._day(20),
                "child_ids": [Command.link(orphan.id)],
            }
        )
        self._assert_planned_dates(parent, orphan.planned_date_start, orphan.planned_date_end)

    def test_copy_of_parent_rolls_up_over_the_dates_it_is_given(self):
        parent = self._create_task("Parent")
        self._create_task("Early", self._day(2), self._day(4), parent_id=parent.id)
        self._create_task("Late", self._day(3), self._day(9), parent_id=parent.id)
        parent_copy = parent.copy({"planned_date_start": self._day(20), "planned_date_end": self._day(30)})
        self.assertEqual(len(parent_copy.child_ids), 2)
        self._assert_planned_dates(
            parent_copy,
            min(parent_copy.child_ids.mapped("planned_date_start")),
            max(parent_copy.child_ids.mapped("planned_date_end")),
        )

    def test_form_planned_dates_are_read_only_on_a_rolled_up_parent(self):
        parent = self._create_task("Parent")
        leaf = self._create_task("Leaf", self._day(4), self._day(7), parent_id=parent.id)
        parent_form = Form(parent, view="project.view_task_form2")
        for field_name in ("planned_date_start", "planned_date_end"):
            with self.assertRaisesRegex(AssertionError, "readonly"):
                setattr(parent_form, field_name, self._day(1))
        leaf_form = Form(leaf, view="project.view_task_form2")
        leaf_form.planned_date_start = self._day(3)
        leaf_form.planned_date_end = self._day(8)
        leaf_form.save()
        self._assert_planned_dates(leaf, self._day(3), self._day(8))

    def test_list_planned_dates_are_read_only_on_a_rolled_up_parent(self):
        arch = etree.fromstring(self.Task.get_view(self.env.ref("project.view_task_tree2").id, "tree")["arch"])
        for field_name in ("planned_date_start", "planned_date_end"):
            nodes = arch.xpath("//field[@name='%s']" % field_name)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(
                json.loads(nodes[0].get("modifiers"))["readonly"], [["is_planned_from_subtasks", "=", True]]
            )

    def test_form_shows_the_planned_dates_once_read_only_on_a_rolled_up_parent(self):
        arch = etree.fromstring(self.Task.get_view(self.env.ref("project.view_task_form2").id, "form")["arch"])
        self.assertEqual(len(arch.xpath("//label[@for='planned_date_start']")), 1)
        for field_name in PLANNED_DATE_FIELDS:
            nodes = arch.xpath("//field[@name='%s'][not(ancestor::field)]" % field_name)
            self.assertEqual(len(nodes), 1)
            self.assertEqual(
                json.loads(nodes[0].get("modifiers"))["readonly"], [["is_planned_from_subtasks", "=", True]]
            )

    def test_the_views_of_this_module_show_the_planned_dates_read_only_on_a_rolled_up_parent(self):
        if self.env["ir.module.module"].search_count([("name", "=", "project_timeline"), ("state", "=", "installed")]):
            for xmlid in TIMELINE_VIEWS:
                self.env.ref(xmlid).active = False

        form = etree.fromstring(self.Task.get_view(self.env.ref("project.view_task_form2").id, "form")["arch"])
        self.assertEqual(len(form.xpath("//label[@name='planned_dates'][@for='planned_date_start']")), 1)
        tree = etree.fromstring(self.Task.get_view(self.env.ref("project.view_task_tree2").id, "tree")["arch"])
        for field_name in PLANNED_DATE_FIELDS:
            form_nodes = form.xpath("//field[@name='%s'][not(ancestor::field)]" % field_name)
            self.assertEqual(form_nodes, form.xpath("//div[@name='planned_dates']/field[@name='%s']" % field_name))
            tree_nodes = tree.xpath("//field[@name='%s']" % field_name)
            self.assertEqual(len(form_nodes + tree_nodes), 2, field_name)
            self.assertEqual(tree_nodes[0].get("optional"), "hide")
            for node in form_nodes + tree_nodes:
                self.assertEqual(json.loads(node.get("modifiers", "{}")).get("readonly"), READ_ONLY_ON_ROLLED_UP_TASKS)
        parent = self._create_task("Parent")
        self._create_task("Leaf", self._day(4), self._day(7), parent_id=parent.id)
        parent_form = Form(parent, view="project.view_task_form2")
        for field_name in PLANNED_DATE_FIELDS:
            with self.assertRaisesRegex(AssertionError, "readonly"):
                setattr(parent_form, field_name, self._day(1))

    def _create_undated_task(self, name, **vals):
        return self.Task.create(
            dict({"name": name, "project_id": self.project.id, "stage_id": self.stage_in_progress.id}, **vals)
        )

    def test_assignment_leaves_an_undated_leaf_undated(self):
        task = self._create_undated_task("Leaf", user_ids=[Command.set(self.user_dev1.ids)])
        assigned_later = self._create_undated_task("Assigned Later", user_ids=False)
        assigned_later.user_ids = self.user_dev1

        for assigned_task in task | assigned_later:
            self.assertTrue(assigned_task.date_assign)
            self._assert_planned_dates(assigned_task, False, False)

    def test_closing_leaves_an_undated_leaf_undated(self):
        task = self._create_undated_task("Leaf", user_ids=[Command.set(self.user_dev1.ids)])

        task.stage_id = self.stage_done

        self.assertTrue(task.date_end)
        self._assert_planned_dates(task, False, False)

    def test_a_leaf_dated_on_one_side_keeps_the_other_side_empty(self):
        now = fields.Datetime.now()
        later, earlier = now + timedelta(days=30), now - timedelta(days=30)
        end_only = self._create_task("End Only", date_end=later, user_ids=False)
        start_only = self._create_task("Start Only", earlier)

        end_only.user_ids = self.user_dev1
        start_only.stage_id = self.stage_done

        self.assertTrue(end_only.date_assign)
        self.assertTrue(start_only.date_end)
        self._assert_planned_dates(end_only, False, later)
        self._assert_planned_dates(start_only, earlier, False)

    def test_end_before_start_is_refused(self):
        task = self._create_task("Leaf", self._day(3), self._day(5))
        with self.assertRaisesRegex(ValidationError, "The end date must be after the start date."):
            task.planned_date_end = self._day(1)

    def test_missing_columns_are_created_empty(self):
        task = self._create_undated_task("Assigned and Closed", user_ids=[Command.set(self.user_dev1.ids)])
        task.stage_id = self.stage_done
        self.env.flush_all()
        self.assertTrue(task.date_assign and task.date_end)
        for field_name in PLANNED_DATE_FIELDS:
            self.env.cr.execute(
                sql.SQL("ALTER TABLE project_task RENAME COLUMN {} TO {}").format(
                    sql.Identifier(field_name), sql.Identifier(field_name + "_kept")
                )
            )

        self.Task._init_planned_date_columns()

        self.env.cr.execute("SELECT planned_date_start, planned_date_end FROM project_task WHERE id = %s", (task.id,))
        self.assertEqual(self.env.cr.fetchone(), (None, None))
        self.env.invalidate_all()

    def test_existing_columns_are_left_alone(self):
        dated_task = self._create_task("Dated", self._day(1), self._day(2))
        undated_task = self._create_undated_task("Assigned and Closed", user_ids=[Command.set(self.user_dev1.ids)])
        undated_task.stage_id = self.stage_done
        self.env.flush_all()
        self.assertTrue(undated_task.date_assign and undated_task.date_end)

        self.Task._init_planned_date_columns()

        self.env.invalidate_all()
        self._assert_planned_dates(dated_task, self._day(1), self._day(2))
        self._assert_planned_dates(undated_task, False, False)

    def test_gantt_chart_data_carries_the_lock_flag(self):
        parent = self._create_task("Parent")
        start_only = self._create_task("Start Only", self._day(1), parent_id=parent.id)
        tasks_by_id = {
            task_data["id"]: task_data
            for task_data in self.env["project.project"].get_gantt_chart_data(self.project.id)["tasks"]
        }
        self.assertIs(tasks_by_id[parent.id]["is_planned_from_subtasks"], True)
        self.assertIs(tasks_by_id[start_only.id]["is_planned_from_subtasks"], False)

    def _write_stale_planned_dates(self, tasks, date_start, date_end):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET planned_date_start = %s, planned_date_end = %s WHERE id IN %s",
            (date_start, date_end, tuple(tasks.ids)),
        )
        self.env.invalidate_all()

    def test_the_computes_of_this_module_roll_up_a_parent_when_called_directly(self):
        """With project_team_dashboard_timeline installed, Odoo calls the glue module's computes, which run these."""
        parent = self._create_task("Parent")
        child = self._create_task("Child", self._day(4), self._day(7), parent_id=parent.id)
        for compute in (DashboardTask._compute_planned_date_start, DashboardTask._compute_planned_date_end):
            with self.subTest(compute=compute.__name__):
                self._write_stale_planned_dates(parent, self._day(40), self._day(50))

                compute(parent)

                self._assert_planned_dates(parent, child.planned_date_start, child.planned_date_end)

    def test_install_rolls_up_deepest_first_and_clears_undated_families(self):
        milestone = self._create_task("Milestone", task_type="milestone")
        task = self._create_task("Task", parent_id=milestone.id)
        subtask = self._create_task("Sub-task", self._day(1), self._day(6), parent_id=task.id)
        undated_parent = self._create_task("Undated Parent", user_ids=[Command.set(self.user_dev1.ids)])
        self._create_task("Undated", parent_id=undated_parent.id)
        self.assertTrue(undated_parent.date_assign)
        self._write_stale_planned_dates(milestone | task, self._day(40), self._day(50))
        self._write_stale_planned_dates(undated_parent, self._day(40), self._day(50))

        roll_up_planned_dates(self.env)

        self._assert_planned_dates(task, subtask.planned_date_start, subtask.planned_date_end)
        self._assert_planned_dates(milestone, subtask.planned_date_start, subtask.planned_date_end)
        self._assert_planned_dates(undated_parent, False, False)
