import importlib.util
import logging
from pathlib import Path
from unittest.mock import patch

from odoo import Command
from odoo.models import BaseModel
from odoo.tests.common import tagged
from odoo.tools import config

from ..models.project_task_key import ProjectTask as KeyedTask
from .common import ProjectAiDrivenCommon

KEY_PREFIX = "ZQ"
STALE_KEY_MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.17.3" / "post-migration.py"


def _load_script(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


@tagged("post_install", "-at_install")
class TestTaskKeys(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, test_project_key=True))
        cls.Project = cls.env["project.project"]
        cls.Task = cls.env["project.task"]

    def setUp(self):
        super().setUp()
        self.assertFalse(
            self.Project.with_context(active_test=False).search_count([("key", "=like", KEY_PREFIX + "%")]),
            "the fixture keys starting with %s must be free on this database" % KEY_PREFIX,
        )
        # Per test: a sequence draws its numbers outside the transaction, a test rolled back spends them.
        self.key_project = self.Project.create({"name": "Zulu Quebec Xray"})
        self.other_project = self.Project.create({"name": "Zulu Quebec Oscar", "key": "ZQOTHER"})
        self.parent_task = self.Task.create({"name": "Parent", "project_id": self.key_project.id})
        self.sub_task = self.Task.create(
            {"name": "Sub-task", "parent_id": self.parent_task.id, "project_id": self.key_project.id}
        )

    def _create_portal_collaborator_project(self):
        user_portal = (
            self.env["res.users"]
            .with_context(no_reset_password=True)
            .create(
                {
                    "name": "Portal Collaborator",
                    "login": "project_ai_driven_key_portal",
                    "email": "portal-key@example.com",
                    "groups_id": [Command.set(self.env.ref("base.group_portal").ids)],
                },
            )
        )
        shared_project = self.Project.create(
            {
                "name": "Zulu Quebec Portal",
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": user_portal.partner_id.id})],
            },
        )
        shared_project.message_subscribe(partner_ids=user_portal.partner_id.ids)
        return user_portal, shared_project

    def test_a_project_key_is_made_of_the_initials_of_its_name(self):
        single_word_project = self.Project.create({"name": "Zqwerty"})
        named_project = self.Project.create({"name": "Zulu Quebec Kilo", "key": "ZQKEY"})

        self.assertEqual(self.key_project.key, "ZQX")
        self.assertEqual(single_word_project.key, "ZQW")
        self.assertEqual(named_project.key, "ZQKEY", "A key given at creation is kept")

    def test_a_taken_key_gets_the_lowest_free_counter_archived_projects_included(self):
        archived_project = self.Project.create({"name": "Zulu Quebec Xray"})
        archived_project.active = False

        self.assertEqual(archived_project.key, "ZQX1")
        self.assertEqual(self.Project.create({"name": "Zulu Quebec Xray"}).key, "ZQX2")

    def test_a_generated_key_keeps_only_letters_and_digits(self):
        projects = self.Project.create(
            [
                {"name": "Zulu - Quebec [Golf]"},
                {"name": "Zq-Uniform_Hotel"},
                {"name": "[Zqwerty]"},
                {"name": "Zulu\tQuebec 7"},
                {"name": "Zulu Quebec Ωmega"},
            ]
        )
        punctuation_project = self.Project.create({"name": "- [ ] -"})
        given_project = self.Project.create({"name": "Zulu Quebec Given", "key": "ZQ-[G]"})

        self.assertEqual(projects.mapped("key"), ["ZQG", "ZQU", "ZQW", "ZQ7", "ZQΩ"])
        self.assertFalse(punctuation_project.key)
        self.assertEqual(given_project.key, "ZQ-[G]", "A key given is kept as it is")

    def test_a_generated_key_with_its_counter_fits_the_key_size(self):
        long_name = "Zulu Quebec Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India"

        first_project = self.Project.create({"name": long_name})
        second_project = self.Project.create({"name": long_name})

        self.assertEqual((first_project | second_project).mapped("key"), ["ZQABCDEFGH", "ZQABCDEFG1"])

    def test_a_batch_create_keeps_every_project_with_its_key_and_sequence(self):
        company = self.env["res.company"].create({"name": "Zulu Quebec Company"})

        projects = self.Project.create(
            [
                {"name": "Zulu Quebec Bravo", "company_id": company.id},
                {"name": "Zulu Quebec Bravo"},
                {"name": "Zulu Quebec Charlie", "key": "ZQB2"},
            ]
        )

        self.assertEqual(projects.mapped("name"), ["Zulu Quebec Bravo", "Zulu Quebec Bravo", "Zulu Quebec Charlie"])
        self.assertEqual(projects.mapped("key"), ["ZQB", "ZQB1", "ZQB2"])
        self.assertEqual(projects.task_key_sequence_id.mapped("prefix"), ["ZQB-", "ZQB1-", "ZQB2-"])
        self.assertEqual(
            projects.task_key_sequence_id.mapped("code"), ["project.task.key.%s" % project.id for project in projects]
        )
        self.assertEqual(
            [project.task_key_sequence_id.company_id for project in projects],
            [company, self.env.company, self.env.company],
        )

    def test_a_generated_key_skips_a_key_given_later_in_the_same_batch(self):
        projects = self.Project.create([{"name": "Zulu Quebec Echo"}, {"name": "Zulu Quebec Lima", "key": "ZQE"}])

        self.assertEqual(projects.mapped("key"), ["ZQE1", "ZQE"])
        self.assertEqual(projects.task_key_sequence_id.mapped("prefix"), ["ZQE1-", "ZQE-"])

    def test_a_batch_create_of_no_values_creates_nothing(self):
        self.assertEqual(self.Project.create([]), self.Project.browse())
        self.assertEqual(self.Task.create([]), self.Task.browse())

    def test_tasks_created_with_their_project_are_numbered_in_order(self):
        projects = self.Project.create(
            [
                {
                    "name": "Zulu Quebec Alpha",
                    "task_ids": [Command.create({"name": "A1"}), Command.create({"name": "A2"})],
                },
                {"name": "Zulu Quebec Delta", "task_ids": [Command.create({"name": "D1"})]},
            ]
        )

        self.assertEqual(projects[0].task_ids.sorted("id").mapped("key"), ["ZQA-1", "ZQA-2"])
        self.assertEqual(projects[1].task_ids.mapped("key"), ["ZQD-1"])

    def test_the_form_proposes_a_key_from_the_name_until_one_is_set(self):
        new_project = self.Project.new({"name": "Zulu Quebec Yankee"})
        new_project._onchange_name()
        self.assertEqual(new_project.key, "ZQY")

        keyed_project = self.Project.new({"name": "Zulu Quebec Yankee", "key": "ZQOWN"})
        keyed_project._onchange_name()
        self.assertEqual(keyed_project.key, "ZQOWN")

        unnamed_project = self.Project.new({})
        unnamed_project._onchange_name()
        self.assertIs(unnamed_project.key, False)

    def test_a_new_project_key_renames_its_tasks_and_its_sequence(self):
        self.key_project.key = "ZQNEW"

        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQNEW-1", "ZQNEW-2"])
        self.assertEqual(self.parent_task.display_name, "ZQNEW-1 - Parent")
        self.assertEqual(self.key_project.task_key_sequence_id.prefix, "ZQNEW-")
        self.assertEqual(self.Task.create({"name": "Next", "project_id": self.key_project.id}).key, "ZQNEW-3")

    def test_a_key_renamed_from_or_to_hyphens_spaces_and_brackets_keeps_each_task_number(self):
        tasks = self.parent_task | self.sub_task

        for key in ("ZQ-X", "ZQ [X-Y]", "ZQPLAIN"):
            self.key_project.key = key
            self.assertEqual(tasks.mapped("key"), ["%s-1" % key, "%s-2" % key])

        self.assertEqual(self.Task.create({"name": "Next", "project_id": self.key_project.id}).key, "ZQPLAIN-3")

    def test_a_new_project_key_leaves_the_task_keys_without_a_hyphen_and_a_number_as_they_are(self):
        hand_keyed_tasks = self.Task.create(
            [{"name": "Spec", "project_id": self.key_project.id}, {"name": "Hand", "project_id": self.key_project.id}]
        )
        hand_keyed_tasks[0].key = "ZQSPEC7"
        hand_keyed_tasks[1].key = "ZQ-HAND"
        numbered_tasks = self.parent_task | self.sub_task

        self.key_project.key = "ZQNEW"

        self.assertEqual(hand_keyed_tasks.mapped("key"), ["ZQSPEC7", "ZQ-HAND"])
        self.assertEqual(numbered_tasks.mapped("key"), ["ZQNEW-1", "ZQNEW-2"])

        self.key_project.key = False

        self.assertEqual(
            (hand_keyed_tasks | numbered_tasks).mapped("key"), [False] * 4, "A cleared key clears them all"
        )

    def test_a_cleared_key_draws_no_key_until_the_project_has_one_again(self):
        self.key_project.key = False
        keyless_task = self.Task.create({"name": "Keyless", "project_id": self.key_project.id})

        self.assertEqual(self.key_project.task_key_sequence_id.prefix, "ZQX-")
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), [False, False])
        self.assertFalse(keyless_task.key)
        self.assertEqual(keyless_task.display_name, "Keyless")

        self.key_project.key = "ZQBACK"

        self.assertEqual(self.key_project.task_key_sequence_id.prefix, "ZQBACK-")
        self.assertEqual(self.Task.create({"name": "Back", "project_id": self.key_project.id}).key, "ZQBACK-3")

    def test_a_deleted_project_takes_its_sequence_along(self):
        project = self.Project.create({"name": "Zulu Quebec Gone"})
        sequence = project.task_key_sequence_id

        project.unlink()

        self.assertFalse(sequence.exists())

    def test_a_copied_project_gets_a_sequence_of_its_own(self):
        copied_project = self.key_project.copy()
        # With keys on, create replaces a copied sequence; with keys off, nothing would.
        keyless_copy = self.key_project.with_context(test_project_key=False).copy()

        self.assertEqual(copied_project.task_key_sequence_id.prefix, "%s-" % copied_project.key)
        self.assertNotEqual(copied_project.task_key_sequence_id, self.key_project.task_key_sequence_id)
        self.assertFalse(keyless_copy.task_key_sequence_id)

    def test_a_project_is_found_by_its_key(self):
        found_ids = [project_id for project_id, _name in self.Project.name_search("ZQOTH")]
        self.assertEqual(found_ids, self.other_project.ids)

        excluded_ids = [project_id for project_id, _name in self.Project.name_search("ZQOTH", operator="not ilike")]
        self.assertNotIn(self.other_project.id, excluded_ids)

    def test_a_task_gets_the_next_key_of_its_project(self):
        sub_task_of_parent_only = self.Task.create({"name": "Sub-task of the parent", "parent_id": self.parent_task.id})
        context_task = self.Task.with_context(default_project_id=self.key_project.id).create({"name": "From context"})
        private_task = self.Task.create({"name": "Private"})

        self.assertEqual(self.parent_task.key, "ZQX-1")
        self.assertEqual(self.sub_task.key, "ZQX-2")
        self.assertEqual(sub_task_of_parent_only.key, "ZQX-3", "A sub-task takes its project from its parent")
        self.assertEqual(context_task.key, "ZQX-4")
        self.assertFalse(private_task.key)

    def test_a_task_created_or_copied_with_no_project_gets_no_key_of_the_default_project(self):
        template_task = self.Task.create({"name": "Template"})
        task_model = self.Task.with_context(default_project_id=self.key_project.id)

        created_task = task_model.create({"name": "No project", "project_id": False})
        copied_task = template_task.with_context(default_project_id=self.key_project.id).copy({"project_id": False})

        self.assertRecordValues(created_task | copied_task, [{"project_id": False, "key": False}] * 2)
        self.assertEqual(task_model.create({"name": "Next"}).key, "ZQX-3", "No key of the project was drawn")

    def test_a_task_shows_and_is_found_by_its_key(self):
        self.assertEqual(self.parent_task.display_name, "ZQX-1 - Parent")
        self.assertEqual(self.Task.create({"name": "Private"}).display_name, "Private")
        found_ids = [task_id for task_id, _name in self.Task.name_search("ZQX-2")]
        self.assertEqual(found_ids, self.sub_task.ids)
        self.assertEqual(
            self.parent_task.url,
            "/web#id=%s&view_type=form&model=project.task&action=%s"
            % (self.parent_task.id, self.env.ref("project.action_view_task").id),
        )

    def test_the_display_name_puts_the_key_before_the_name_other_modules_return(self):
        def name_get(records):
            return [(record.id, "%s (other module)" % record.name) for record in records]

        with patch.object(BaseModel, "name_get", name_get):
            names = self.parent_task.name_get()

        self.assertEqual(names, [(self.parent_task.id, "ZQX-1 - Parent (other module)")])

    def test_a_task_written_with_its_own_project_keeps_its_key(self):
        self.parent_task.project_id = self.key_project

        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQX-1", "ZQX-2"])

    def test_a_moved_task_and_its_sub_tasks_get_keys_of_their_new_project(self):
        self.Task.create({"name": "Other", "project_id": self.other_project.id})

        self.parent_task.project_id = self.other_project

        self.assertEqual(self.sub_task.project_id, self.other_project)
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQOTHER-2", "ZQOTHER-3"])

    def test_an_archived_sub_task_moves_with_its_parent_and_gets_a_key_of_the_new_project(self):
        archived_sub_task = self.Task.create(
            {"name": "Archived sub-task", "parent_id": self.parent_task.id, "project_id": self.key_project.id}
        )
        archived_sub_task.active = False
        self.Task.create({"name": "Other", "project_id": self.other_project.id})

        self.parent_task.project_id = self.other_project
        self.other_project.key = "ZQMOVED"

        self.assertEqual(archived_sub_task.project_id, self.other_project)
        self.assertEqual(
            (self.parent_task | self.sub_task | archived_sub_task).mapped("key"),
            ["ZQMOVED-2", "ZQMOVED-3", "ZQMOVED-4"],
        )

    def test_tasks_moved_into_a_project_without_a_key_lose_theirs(self):
        third_task = self.Task.create(
            {"name": "Third", "project_id": self.Project.create({"name": "Zulu Quebec Third"}).id}
        )
        self.other_project.key = False

        third_task.project_id = self.other_project
        self.parent_task.project_id = self.other_project
        self.other_project.key = "ZQBACK"
        next_task = self.Task.create({"name": "Next", "project_id": self.other_project.id})

        moved_tasks = third_task | self.parent_task | self.sub_task
        self.assertEqual(moved_tasks.project_id, self.other_project)
        self.assertEqual(moved_tasks.mapped("key"), [False, False, False], "ZQT-1 and ZQX-1 would both become ZQBACK-1")
        self.assertEqual(next_task.key, "ZQBACK-1")

    def test_a_task_taken_out_of_its_project_loses_its_key_with_the_sub_tasks_that_follow_it(self):
        shown_sub_task = self.Task.create(
            {"name": "Shown elsewhere", "parent_id": self.parent_task.id, "display_project_id": self.other_project.id}
        )

        self.parent_task.project_id = False

        self.assertFalse(self.sub_task.project_id)
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), [False, False])
        self.assertRecordValues(shown_sub_task, [{"project_id": self.other_project.id, "key": "ZQOTHER-1"}])

    def test_a_sub_task_moved_by_its_new_parent_or_displayed_project_gets_a_key_of_its_new_project(self):
        other_parent = self.Task.create({"name": "Other parent", "project_id": self.other_project.id})
        grandchild = self.Task.create({"name": "Grandchild", "parent_id": self.sub_task.id})
        shown_sub_task = self.Task.create({"name": "Shown", "parent_id": self.parent_task.id})
        private_sub_task = self.Task.create({"name": "Private", "parent_id": self.parent_task.id})

        self.sub_task.parent_id = other_parent
        shown_sub_task.display_project_id = self.other_project
        private_sub_task.parent_id = self.Task.create({"name": "Private parent"})

        self.assertEqual((self.sub_task | grandchild | shown_sub_task).project_id, self.other_project)
        self.assertEqual(
            (self.sub_task | grandchild | shown_sub_task).mapped("key"), ["ZQOTHER-2", "ZQOTHER-3", "ZQOTHER-4"]
        )
        self.assertRecordValues(private_sub_task, [{"project_id": False, "key": False}])
        self.assertEqual(self.parent_task.key, "ZQX-1")

    def test_a_project_user_moves_a_task_with_sub_tasks_they_cannot_read(self):
        followers_project = self.Project.create({"name": "Zulu Quebec Followers", "privacy_visibility": "followers"})
        shown_sub_task = self.Task.create(
            {"name": "Shown", "parent_id": self.parent_task.id, "display_project_id": followers_project.id}
        )
        assigned_task = self.Task.create(
            {"name": "Assigned", "project_id": followers_project.id, "user_ids": [Command.link(self.user_dev.id)]}
        )
        hidden_sub_task = self.Task.create({"name": "Hidden", "parent_id": assigned_task.id})
        self.assertFalse(
            (shown_sub_task | hidden_sub_task).with_user(self.user_dev)._filter_access_rules("read"),
            "the developer must not read the sub-tasks",
        )
        # As in a request of its own: only what the developer reads is in the cache; a value cached
        # from the creates would be read without an access check.
        self.env.invalidate_all()
        written_tasks = (self.parent_task | assigned_task).with_user(self.user_dev)
        (written_tasks | self.sub_task).mapped("project_id")

        # This module's own write: another module's may read the sub-tasks in sudo first, and cache them.
        KeyedTask.write(written_tasks, {"project_id": self.other_project.id})

        self.assertRecordValues(
            shown_sub_task | hidden_sub_task,
            [
                {"project_id": followers_project.id, "key": "ZQF-1"},
                {"project_id": self.other_project.id, "key": "ZQOTHER-4"},
            ],
        )
        self.assertEqual(
            (self.parent_task | self.sub_task | assigned_task).mapped("key"), ["ZQOTHER-1", "ZQOTHER-2", "ZQOTHER-3"]
        )

    def test_a_copied_task_gets_the_next_key_of_its_project_or_none(self):
        template_task = self.Task.create({"name": "Template", "project_id": self.key_project.id})
        self.parent_task.project_id = False

        out_of_project_copy = self.parent_task.copy()
        template_copy_without_project = template_task.copy({"project_id": False})
        template_copy = template_task.copy()

        self.assertEqual(template_task.key, "ZQX-3")
        self.assertFalse(out_of_project_copy.key)
        self.assertFalse(template_copy_without_project.key)
        self.assertEqual(template_copy.key, "ZQX-4")

    def _create_recurrence_of_three(self):
        self.key_project.allow_recurring_tasks = True
        first_task = self.Task.create(
            {
                "name": "Every Monday",
                "project_id": self.key_project.id,
                "recurring_task": True,
                "repeat_interval": 1,
                "repeat_unit": "week",
                "mon": True,
                "repeat_type": "forever",
            }
        )
        first_task.recurrence_id._create_next_task()
        first_task.recurrence_id._create_next_task()
        recurring_tasks = first_task.recurrence_id.task_ids.sorted("id")
        self.assertEqual(recurring_tasks.mapped("key"), ["ZQX-3", "ZQX-4", "ZQX-5"])
        return recurring_tasks

    def test_the_next_task_of_a_recurrence_taken_out_of_its_project_has_no_key(self):
        last_task = self._create_recurrence_of_three()[2]

        last_task.project_id = False
        last_task.recurrence_id._create_next_task()

        next_task = last_task.recurrence_id.task_ids.sorted("id")[-1]
        self.assertNotEqual(next_task, last_task)
        self.assertEqual((last_task | next_task).mapped("key"), [False, False])

    def test_a_project_written_on_all_tasks_of_a_recurrence_gives_each_a_key_of_it(self):
        recurring_tasks = self._create_recurrence_of_three()

        recurring_tasks[2].write({"project_id": self.other_project.id, "recurrence_update": "all"})

        self.assertEqual(recurring_tasks.project_id, self.other_project)
        self.assertEqual(recurring_tasks.mapped("key"), ["ZQOTHER-1", "ZQOTHER-2", "ZQOTHER-3"])

    def test_a_project_written_on_subsequent_tasks_of_a_recurrence_gives_those_a_key_of_it(self):
        recurring_tasks = self._create_recurrence_of_three()
        self.env.flush_all()
        for day, task in enumerate(recurring_tasks, start=1):
            self.env.cr.execute(
                "UPDATE project_task SET create_date = %s WHERE id = %s", ("2020-01-0%s" % day, task.id)
            )
        recurring_tasks.invalidate_recordset(["create_date"])
        self.key_project.key = False
        self.key_project.key = "ZQBACK"

        recurring_tasks[1].write({"project_id": self.other_project.id, "recurrence_update": "subsequent"})

        self.assertRecordValues(
            recurring_tasks,
            [
                {"project_id": self.key_project.id, "key": False},
                {"project_id": self.other_project.id, "key": "ZQOTHER-1"},
                {"project_id": self.other_project.id, "key": "ZQOTHER-2"},
            ],
        )

    def test_keyless_tasks_of_a_recurrence_written_with_their_own_project_get_a_key(self):
        recurring_tasks = self._create_recurrence_of_three()
        self.key_project.key = False
        self.key_project.key = "ZQBACK"

        recurring_tasks[2].write({"project_id": self.key_project.id, "recurrence_update": "all"})

        self.assertEqual(recurring_tasks.mapped("key"), ["ZQBACK-6", "ZQBACK-7", "ZQBACK-8"])

    def test_a_project_given_a_key_freed_by_a_rename_or_a_deletion_numbers_its_tasks_from_1(self):
        gone_project = self.Project.create({"name": "Zulu Quebec Gone", "key": "ZQGONE"})
        gone_task = self.Task.create({"name": "Taken out", "project_id": gone_project.id})
        (self.parent_task | gone_task).project_id = False
        self.key_project.key = "ZQRENAMED"
        gone_project.unlink()

        reused_projects = self.Project.create(
            [{"name": "Zulu Quebec Reused", "key": "ZQX"}, {"name": "Zulu Quebec Again", "key": "ZQGONE"}]
        )
        first_tasks = self.Task.create([{"name": "First", "project_id": project.id} for project in reused_projects])

        self.assertEqual(first_tasks.mapped("key"), ["ZQX-1", "ZQGONE-1"])

    def test_a_task_moved_into_a_marked_stage_with_its_resolution_passes_the_closing_rule(self):
        # The key is renewed after the write: a write of the project alone first would meet the rule
        # without the Resolution of the same write.
        self._skip_next_to_pull_request_module()
        task = self.Task.create({"name": "Closing", "project_id": self.other_project.id})
        closing_project = self.Project.create(
            {"name": "Zulu Quebec Closing", "is_ai_driven": True, "type_ids": [Command.clear()]}
        )
        stage_done = self._create_stage("Done", 1, project=closing_project)
        closing_project.pr_required_states = stage_done

        task.write({"project_id": closing_project.id, "stage_id": stage_done.id, "resolution": "no_change"})

        self.assertRecordValues(task, [{"stage_id": stage_done.id, "resolution": "no_change", "key": "ZQC-1"}])

    def test_a_portal_collaborator_creates_a_task_with_its_key(self):
        user_portal, shared_project = self._create_portal_collaborator_project()

        task = (
            self.Task.with_user(user_portal)
            .with_context(default_project_id=shared_project.id)
            .create({"name": "Report the leak"})
        )

        self.assertEqual(task.project_id, shared_project)
        self.assertEqual(task.key, "ZQP-1")

    def test_keys_are_left_to_oca_project_key_while_it_is_loaded(self):
        loaded_modules = self.env.registry._init_modules | {"project_key"}
        with patch.object(self.env.registry, "_init_modules", loaded_modules):
            project = self.Project.create({"name": "Zulu Quebec Left"})
            task = self.Task.create({"name": "Left", "project_id": self.other_project.id})
            self.parent_task.project_id = self.other_project
            self.key_project.key = "ZQLEFT"
            sequence = self.key_project.task_key_sequence_id
            self.key_project.unlink()
            names = (self.parent_task | self.sub_task).name_get()

        self.assertFalse(project.key)
        self.assertFalse(project.task_key_sequence_id)
        self.assertFalse(task.key)
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQX-1", "ZQX-2"])
        self.assertEqual(sequence.prefix, "ZQX-")
        self.assertEqual(names, [(self.parent_task.id, "Parent"), (self.sub_task.id, "Sub-task")])

    def test_keys_stay_off_in_a_test_run_unless_the_context_asks(self):
        project_model = self.Project.with_context(test_project_key=False)
        project = project_model.create({"name": "Zulu Quebec Off"})
        keyed_project = project_model.create({"name": "Zulu Quebec Keyed", "key": "ZQKEYED"})
        task = self.Task.with_context(test_project_key=False).create({"name": "Off", "project_id": keyed_project.id})

        self.assertFalse(project.key)
        self.assertFalse(project.task_key_sequence_id)
        self.assertEqual(keyed_project.task_key_sequence_id.prefix, "ZQKEYED-")
        self.assertFalse(task.key)
        self.assertEqual(task.display_name, "Off")

    def test_stale_keys_are_cleared_or_renewed_without_an_edit(self):
        keyless_project = self.Project.create({"name": "Zulu Quebec Keyless"})
        stale_tasks = self.Task.create(
            [
                {"name": "Copied out of its project"},
                {"name": "Moved by project_key", "project_id": self.key_project.id},
                {"name": "Prefix lookalike", "project_id": self.key_project.id},
                {"name": "In a project without a key", "project_id": keyless_project.id},
            ]
        )
        left_out_task = self.Task.create({"name": "Left out of the run"})
        left_out_task.key = "ZQX-91"
        keyless_project.key = False
        for task, stale_key in zip(stale_tasks, ("ZQX-90", "ZQOTHER-90", "ZQXY-90", "ZQK-90")):
            task.key = stale_key
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET write_date = '2020-01-01' WHERE id IN %s", (tuple(stale_tasks.ids),)
        )
        stale_tasks.invalidate_recordset(["write_date"])
        run_tasks = stale_tasks | self.parent_task | self.sub_task

        # The level, not its name: Odoo names its own level 25 INFO.
        with self.assertLogs(KeyedTask.__module__, logging.INFO) as captured_logs:
            self.assertEqual(self.Task._renew_stale_keys(run_tasks), (2, 2))

        self.assertEqual(stale_tasks.mapped("key"), [False, "ZQX-5", "ZQX-6", False])
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQX-1", "ZQX-2"])
        self.assertEqual(left_out_task.key, "ZQX-91", "Only the tasks given to the run are renewed")
        self.assertEqual({str(write_date) for write_date in stale_tasks.mapped("write_date")}, {"2020-01-01 00:00:00"})
        self.assertEqual(
            [record.getMessage() for record in captured_logs.records],
            [
                "Stale key of task %s: ZQX-90 -> none" % stale_tasks[0].id,
                "Stale key of task %s: ZQOTHER-90 -> ZQX-5" % stale_tasks[1].id,
                "Stale key of task %s: ZQXY-90 -> ZQX-6" % stale_tasks[2].id,
                "Stale key of task %s: ZQK-90 -> none" % stale_tasks[3].id,
                "Stale task keys: 2 cleared, 2 renewed",
            ],
        )
        self.assertEqual(self.Task._renew_stale_keys(run_tasks), (0, 0), "A second run changes nothing")

    def test_keys_typed_by_hand_are_no_stale_keys(self):
        hand_tasks = self.Task.create(
            [{"name": "Typed in a project", "project_id": self.key_project.id}, {"name": "Typed without a project"}]
        )
        for task, hand_key in zip(hand_tasks, ("ZQSPEC2", "ZQ-HAND")):
            task.key = hand_key

        self.assertEqual(self.Task._renew_stale_keys(hand_tasks), (0, 0))

        self.assertEqual(hand_tasks.mapped("key"), ["ZQSPEC2", "ZQ-HAND"])

    def test_a_stale_key_is_renewed_into_the_key_cleared_on_a_later_task(self):
        moved_task = self.Task.create({"name": "Moved with its key", "project_id": self.other_project.id})
        projectless_task = self.Task.create({"name": "Left by a deleted ZQGONE project"})
        reused_project = self.Project.create({"name": "Zulu Quebec Reused", "key": "ZQGONE"})
        self.env.flush_all()
        self.env.cr.execute("UPDATE project_task SET project_id = %s WHERE id = %s", (reused_project.id, moved_task.id))
        moved_task.invalidate_recordset(["project_id"])
        # Written last, its row follows the moved task's both by id and on disk: an update reads it second.
        projectless_task.key = "ZQGONE-1"

        self.assertEqual(self.Task._renew_stale_keys(moved_task | projectless_task), (1, 1))

        self.assertEqual((moved_task | projectless_task).mapped("key"), ["ZQGONE-1", False])

    def test_the_stale_key_migration_runs_on_an_update_with_keys_on(self):
        projectless_task = self.Task.create({"name": "Copied out of its project"})
        projectless_task.key = "ZQX-90"
        self.parent_task.key = "ZQOTHER-90"
        stale_tasks = projectless_task | self.parent_task
        script = _load_script(STALE_KEY_MIGRATION_SCRIPT)
        renew_stale_keys = KeyedTask._renew_stale_keys

        def renew_own_stale_keys(task_model):
            return renew_stale_keys(task_model, stale_tasks)

        # The migration runs over every task: limited to this test's, so that it spends no numbers of
        # the database's projects, which no rollback gives back.
        with patch.object(KeyedTask, "_renew_stale_keys", renew_own_stale_keys):
            script.migrate(self.env.cr, "16.0.2.17.2")
            self.assertEqual(stale_tasks.mapped("key"), ["ZQX-90", "ZQOTHER-90"], "Keys are off in a test run")

            with patch.dict(config.options, test_enable=False):
                script.migrate(self.env.cr, None)
                self.assertEqual(stale_tasks.mapped("key"), ["ZQX-90", "ZQOTHER-90"], "An install changes nothing")

                script.migrate(self.env.cr, "16.0.2.17.2")

        self.assertEqual(stale_tasks.mapped("key"), [False, "ZQX-3"])


@tagged("post_install", "-at_install")
class TestMissingKeys(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Without keys: the projects and tasks of a database where OCA project_key never ran.
        cls.env = cls.env(context=dict(cls.env.context, test_project_key=False))
        cls.keyless_project = cls.env["project.project"].create({"name": "Zulu Quebec Fill"})
        cls.folded_stage = cls._create_stage("Done", 1, project=cls.keyless_project, fold=True)
        cls.tasks = cls.env["project.task"].create(
            [
                {"name": "First", "project_id": cls.keyless_project.id},
                {"name": "Archived", "project_id": cls.keyless_project.id, "active": False},
                {"name": "Done", "project_id": cls.keyless_project.id, "stage_id": cls.folded_stage.id},
            ]
        )
        cls.keyed_project = cls.env["project.project"].create({"name": "Zulu Quebec Keyed", "key": "ZQKEPT"})
        cls.keyed_task = cls.env["project.task"].create({"name": "Keyed", "project_id": cls.keyed_project.id})
        cls.keyed_task.key = "ZQKEPT-7"
        cls.env = cls.env(context=dict(cls.env.context, test_project_key=True))

    def test_projects_without_a_key_get_one_and_their_tasks_theirs(self):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET write_date = '2020-01-01' WHERE id IN %s", (tuple(self.tasks.ids),)
        )
        self.tasks.invalidate_recordset(["write_date"])

        self.env["project.project"]._set_missing_keys()

        self.assertEqual(self.keyless_project.key, "ZQF")
        self.assertEqual(self.keyless_project.task_key_sequence_id.prefix, "ZQF-")
        self.assertEqual(self.tasks.mapped("key"), ["ZQF-1", "ZQF-2", "ZQF-3"])
        self.assertEqual(
            {str(write_date) for write_date in self.tasks.mapped("write_date")},
            {"2020-01-01 00:00:00"},
            "A key given afterwards is no edit",
        )
        self.assertEqual(
            self.env["project.task"].create({"name": "Next", "project_id": self.keyless_project.id}).key, "ZQF-4"
        )

    def test_projects_get_their_sequence_while_oca_project_key_is_loaded(self):
        loaded_modules = self.env.registry._init_modules | {"project_key"}
        with patch.object(self.env.registry, "_init_modules", loaded_modules):
            self.env["project.project"]._set_missing_keys()

        self.assertEqual(self.keyless_project.task_key_sequence_id.prefix, "ZQF-")
        self.assertEqual(self.tasks.mapped("key"), ["ZQF-1", "ZQF-2", "ZQF-3"])
        self.assertEqual(
            self.env["project.task"].create({"name": "Next", "project_id": self.keyless_project.id}).key, "ZQF-4"
        )

    def test_a_project_with_a_sequence_numbers_its_tasks_from_its_next_number(self):
        # What project_key left of a project whose key was cleared.
        self.keyless_project.task_key_sequence_id = self.env["ir.sequence"].create(
            {"name": "Cleared", "prefix": "False-", "number_next": 5}
        )

        self.env["project.project"]._set_missing_keys()

        self.assertEqual(self.keyless_project.task_key_sequence_id.prefix, "ZQF-")
        self.assertEqual(self.tasks.mapped("key"), ["ZQF-5", "ZQF-6", "ZQF-7"])
        self.assertEqual(
            self.env["project.task"].create({"name": "Next", "project_id": self.keyless_project.id}).key, "ZQF-8"
        )

    def test_projects_with_a_key_keep_it_and_their_tasks_theirs(self):
        sequence_number = self.keyed_project.task_key_sequence_id.number_next_actual

        self.env["project.project"]._set_missing_keys()
        self.env["project.project"]._set_missing_keys()

        self.assertEqual(self.keyed_project.key, "ZQKEPT")
        self.assertEqual(self.keyed_task.key, "ZQKEPT-7")
        self.assertEqual(self.keyed_project.task_key_sequence_id.number_next_actual, sequence_number)
        self.assertEqual(self.keyless_project.key, "ZQF", "A second run changes nothing")
        self.assertEqual(self.tasks.mapped("key"), ["ZQF-1", "ZQF-2", "ZQF-3"])
