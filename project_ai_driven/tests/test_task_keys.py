from pathlib import Path
from unittest.mock import patch

from lxml import etree

from odoo import Command
from odoo.models import BaseModel
from odoo.modules.module import load_script
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from ..hooks import post_init_hook
from ..models.project_task_key import ProjectTask as KeyedTask
from .common import ProjectAiDrivenCommon

KEY_PREFIX = "ZQ"
KEYS_MIGRATION = Path(__file__).parents[1] / "migrations" / "20.0.2.17.0" / "post-migration.py"


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
                    "group_ids": [Command.set(self.env.ref("base.group_portal").ids)],
                },
            )
        )
        shared_project = self.Project.create(
            {
                "name": "Zulu Quebec Portal",
                "privacy_visibility": "portal",
                "collaborator_ids": [Command.create({"partner_id": user_portal.partner_id.id, "access_mode": "edit"})],
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

    def test_a_generated_key_is_cut_to_the_size_of_the_key_with_its_counter(self):
        long_name = "Zulu Quebec Alpha Bravo Charlie Delta Echo Foxtrot Golf Hotel India"
        first_project, second_project = self.Project.create([{"name": long_name}, {"name": long_name}])
        task = self.Task.create({"name": "Long", "project_id": first_project.id})

        self.assertEqual((first_project | second_project).mapped("key"), ["ZQABCDEFGH", "ZQABCDEFG1"])
        self.assertEqual(task.key, "ZQABCDEFGH-1")

    def test_a_generated_key_keeps_only_letters_and_digits(self):
        projects = self.Project.create([{"name": "Zulu - Quebec [Sierra] 9"}, {"name": "Zq.w-erty"}, {"name": "- [ ]"}])

        self.assertEqual(projects.mapped("key"), ["ZQS9", "ZQW", False])
        self.assertFalse(projects[2].task_key_sequence_id)

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
            [company, self.env["res.company"], self.env["res.company"]],
            "Each sequence takes the company of its project, and none for a project of no company",
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

    def test_a_new_project_key_renames_only_the_task_keys_ending_in_a_hyphen_and_digits(self):
        hand_keyed_tasks = self.Task.create(
            [{"name": "Hand %s" % number, "project_id": self.key_project.id} for number in range(2)]
        )
        hand_keyed_tasks[0].key = "ZQX-HAND"
        hand_keyed_tasks[1].key = "ZQSPEC2"
        project_tasks = self.parent_task | self.sub_task | hand_keyed_tasks

        self.key_project.key = "ZQNEW"

        self.assertEqual(project_tasks.mapped("key"), ["ZQNEW-1", "ZQNEW-2", "ZQX-HAND", "ZQSPEC2"])

        self.key_project.key = False

        self.assertEqual(project_tasks.mapped("key"), [False] * 4, "A cleared key clears every task key")

    def test_a_project_key_with_hyphens_renames_its_tasks_by_the_number_after_the_last_hyphen(self):
        self.key_project.key = "ZQ-[X]"
        self.key_project.key = "ZQ-Y"

        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQ-Y-1", "ZQ-Y-2"])
        self.assertEqual(self.Task.create({"name": "Next", "project_id": self.key_project.id}).key, "ZQ-Y-3")

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

    def test_a_task_shows_and_is_found_by_its_key(self):
        self.assertEqual(self.parent_task.display_name, "ZQX-1 - Parent")
        self.assertEqual(self.Task.create({"name": "Private"}).display_name, "Private")
        found_ids = [task_id for task_id, _name in self.Task.name_search("ZQX-2")]
        self.assertEqual(found_ids, self.sub_task.ids)
        self.assertEqual(self.parent_task.url, "/odoo/action-project.action_view_task/%s" % self.parent_task.id)

    def test_the_search_views_find_projects_and_tasks_by_key(self):
        for model, key, record in (
            (self.Project, "zqoth", self.other_project),
            (self.Task, "zqx-2", self.sub_task),
        ):
            arch = etree.fromstring(model.get_views([(False, "search")])["views"]["search"]["arch"])
            name_filter = arch.xpath("//field[@name='name']")[0].get("filter_domain")
            self.assertEqual(model.search(safe_eval(name_filter, {"self": key})), record, model._name)

    def test_the_display_name_puts_the_key_before_the_name_other_modules_return(self):
        def compute_display_name(records):
            for record in records:
                record.display_name = "%s (other module)" % record.name

        with patch.object(BaseModel, "_compute_display_name", compute_display_name):
            self.parent_task.invalidate_recordset(["display_name"])
            display_name = self.parent_task.display_name

        self.assertEqual(display_name, "ZQX-1 - Parent (other module)")

    def test_a_task_written_with_its_own_project_keeps_its_key(self):
        self.parent_task.project_id = self.key_project

        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQX-1", "ZQX-2"])

    def test_a_moved_task_and_its_sub_tasks_get_keys_of_their_new_project(self):
        self.Task.create({"name": "Other", "project_id": self.other_project.id})

        self.parent_task.project_id = self.other_project

        self.assertEqual(self.sub_task.project_id, self.other_project)
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQOTHER-2", "ZQOTHER-3"])

    def test_a_sub_task_in_a_project_of_its_own_stays_there_with_its_key(self):
        own_sub_task = self.Task.create(
            {"name": "Own sub-task", "parent_id": self.parent_task.id, "project_id": self.other_project.id}
        )
        third_project = self.Project.create({"name": "Zulu Quebec Third"})

        self.parent_task.project_id = third_project

        self.assertEqual(own_sub_task.project_id, self.other_project)
        self.assertEqual(own_sub_task.key, "ZQOTHER-1")
        self.assertEqual((self.parent_task | self.sub_task).mapped("key"), ["ZQT-1", "ZQT-2"])

    def test_an_archived_sub_task_moves_with_its_parent_and_gets_a_key_of_the_new_project(self):
        # Archived at its create: the sub-task tree is read by SQL, which sees no unflushed archiving.
        archived_sub_task = self.Task.create(
            {
                "name": "Archived sub-task",
                "parent_id": self.parent_task.id,
                "project_id": self.key_project.id,
                "active": False,
            }
        )
        sub_task_of_archived = self.Task.create({"name": "Under the archived", "parent_id": archived_sub_task.id})
        self.Task.create({"name": "Other", "project_id": self.other_project.id})

        self.parent_task.project_id = self.other_project
        self.other_project.key = "ZQMOVED"

        moved_tasks = self.parent_task | self.sub_task | archived_sub_task | sub_task_of_archived
        self.assertEqual(moved_tasks.project_id, self.other_project)
        self.assertEqual(moved_tasks.mapped("key"), ["ZQMOVED-2", "ZQMOVED-3", "ZQMOVED-4", "ZQMOVED-5"])

    def test_a_new_parent_needs_no_read_access_to_the_sub_tasks_of_the_task(self):
        hidden_project = self.Project.create({"name": "Zulu Quebec Hidden", "privacy_visibility": "followers"})
        hidden_sub_task = self.Task.create(
            {"name": "Hidden sub-task", "parent_id": self.parent_task.id, "project_id": hidden_project.id}
        )
        new_parent_task = self.Task.create({"name": "New parent", "project_id": self.key_project.id})
        readable_tasks = (self.parent_task | self.sub_task).with_user(self.user_dev)
        self.assertFalse(hidden_sub_task.with_user(self.user_dev).has_access("read"))
        self.env.invalidate_all()
        # A caller that read what it can: the hidden sub-task is then fetched alone, which raises.
        readable_tasks.mapped("project_id")

        # This module's write, without project_member_access's, which reads every project as sudo first.
        KeyedTask.write(readable_tasks[0], {"parent_id": new_parent_task.id})

        self.assertEqual(self.parent_task.parent_id, new_parent_task)
        self.assertEqual((self.parent_task | hidden_sub_task).mapped("key"), ["ZQX-1", "ZQH-1"])

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

    def test_a_task_taken_out_of_its_project_loses_its_key(self):
        task = self.Task.create({"name": "Taken out", "project_id": self.key_project.id})

        task.write({"project_id": False})

        self.assertFalse(task.key)
        self.assertEqual(task.display_name, "Taken out")

    def test_a_copy_outside_a_project_gets_no_key(self):
        task = self.Task.create({"name": "Source", "project_id": self.key_project.id})

        copied_task = task.copy({"project_id": False})

        self.assertFalse(copied_task.project_id)
        self.assertFalse(copied_task.key)
        self.assertEqual(task.key, "ZQX-3")

    def test_a_task_given_no_project_draws_no_key_of_the_default_project(self):
        source_task = self.Task.create({"name": "Source", "project_id": self.key_project.id})
        default_project_tasks = self.Task.with_context(default_project_id=self.key_project.id)

        created_task = default_project_tasks.create({"name": "No project", "project_id": False})
        copied_task = source_task.with_context(default_project_id=self.key_project.id).copy({"project_id": False})
        next_task = default_project_tasks.create({"name": "Next"})

        self.assertFalse((created_task | copied_task).project_id)
        self.assertEqual((created_task | copied_task).mapped("key"), [False, False])
        self.assertEqual(next_task.key, "ZQX-4", "No number of the default project was drawn for them")

    def test_a_sub_task_given_a_parent_in_another_project_gets_a_key_of_that_project(self):
        other_parent_task = self.Task.create({"name": "Other parent", "project_id": self.other_project.id})

        self.sub_task.parent_id = other_parent_task

        self.assertEqual(self.sub_task.project_id, self.other_project)
        self.assertEqual(self.sub_task.key, "ZQOTHER-2")

    def test_a_reused_project_key_draws_no_key_of_a_task_that_left_its_project(self):
        taken_out_task = self.Task.create({"name": "Taken out", "project_id": self.key_project.id})
        other_parent_task = self.Task.create({"name": "Other parent", "project_id": self.other_project.id})
        taken_out_task.project_id = False
        self.sub_task.parent_id = other_parent_task
        self.key_project.key = "ZQRENAMED"

        reused_project = self.Project.create({"name": "Zulu Quebec Reused", "key": "ZQX"})
        new_tasks = self.Task.create(
            [{"name": "New %s" % number, "project_id": reused_project.id} for number in range(3)]
        )

        self.assertEqual(new_tasks.mapped("key"), ["ZQX-1", "ZQX-2", "ZQX-3"])
        self.assertEqual(self.parent_task.key, "ZQRENAMED-1")

    def test_a_task_moved_into_a_marked_stage_with_its_resolution_passes_the_closing_rule(self):
        # The key is renewed after the write: a write of the project alone first would meet the rule
        # without the Resolution of the same write.
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
        self.assertEqual(task.display_name, "ZQP-1 - Report the leak", "The portal user reads the key in the name")

    def test_a_portal_collaborator_finds_a_task_by_its_key(self):
        user_portal, shared_project = self._create_portal_collaborator_project()
        task = self.Task.create({"name": "Shared", "project_id": shared_project.id})
        portal_tasks = self.Task.with_user(user_portal)

        self.assertEqual([task_id for task_id, _name in portal_tasks.name_search("zqp-1")], task.ids)
        self.assertEqual(portal_tasks.search([("key", "=ilike", "ZQP-1")]), task)

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


@tagged("post_install", "-at_install")
class TestMissingKeys(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Without keys: the projects and tasks of a database that had none.
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
        cls.symbol_named_project = cls.env["project.project"].create({"name": "- [ ]"})
        cls.symbol_named_task = cls.env["project.task"].create(
            {"name": "Under symbols", "project_id": cls.symbol_named_project.id}
        )
        cls.env = cls.env(context=dict(cls.env.context, test_project_key=True))

    def _assert_keys_filled(self):
        self.assertEqual(self.keyless_project.key, "ZQF")
        self.assertEqual(self.keyless_project.task_key_sequence_id.prefix, "ZQF-")
        self.assertEqual(self.tasks.mapped("key"), ["ZQF-1", "ZQF-2", "ZQF-3"])
        self.assertEqual(
            self.env["project.task"].create({"name": "Next", "project_id": self.keyless_project.id}).key, "ZQF-4"
        )
        self.assertRecordValues(self.symbol_named_project, [{"key": False, "task_key_sequence_id": False}])
        self.assertFalse(self.symbol_named_task.key, "A name without letters or digits gives no key")

    def test_a_project_named_without_letters_or_digits_keys_its_tasks_once_given_a_key(self):
        project = self.symbol_named_project.with_env(self.env)
        self.env["project.project"]._set_missing_keys()

        project.key = "ZQTYPED"
        new_task = self.env["project.task"].create({"name": "New", "project_id": project.id})
        self.env["project.project"]._set_missing_keys()

        self.assertEqual(new_task.key, "ZQTYPED-1")
        self.assertFalse(self.symbol_named_task.key, "Its former tasks keep none: the project no longer misses a key")

        project._number_keyless_tasks()

        self.assertEqual(self.symbol_named_task.key, "ZQTYPED-2")

    def test_projects_without_a_key_get_one_and_their_tasks_theirs(self):
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET write_date = '2020-01-01' WHERE id IN %s", (tuple(self.tasks.ids),)
        )
        self.tasks.invalidate_recordset(["write_date"])

        self.env["project.project"]._set_missing_keys()

        self._assert_keys_filled()
        self.assertEqual(
            {str(write_date) for write_date in self.tasks.mapped("write_date")},
            {"2020-01-01 00:00:00"},
            "A key given afterwards is no edit",
        )

    def test_a_project_with_a_sequence_numbers_its_tasks_from_its_next_number(self):
        # What a cleared key leaves: the project keeps its sequence.
        self.keyless_project.task_key_sequence_id = self.env["ir.sequence"].create(
            {"name": "Cleared", "prefix": "ZQOLD-", "number_next": 5}
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

    def test_the_install_gives_the_missing_keys(self):
        post_init_hook(self.env)

        self._assert_keys_filled()

    def test_the_update_to_20_0_2_17_0_gives_the_missing_keys(self):
        script = load_script(str(KEYS_MIGRATION), "post_migration")
        # The script works in an environment of its own, without the context that turns keys on.
        project_class = type(self.env["project.project"])
        with patch.object(project_class, "_is_task_key_off", lambda project: False):
            self.env.flush_all()
            script.migrate(self.env.cr, None)
            self.assertFalse(self.keyless_project.key, "A fresh install gives keys through its hook")

            script.migrate(self.env.cr, "20.0.2.16.0")
            self.env.invalidate_all()

        self._assert_keys_filled()
