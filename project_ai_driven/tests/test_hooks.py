from lxml import etree
from psycopg2 import sql
from psycopg2.extras import Json

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools.sql import column_exists, rename_column

from ..hooks import (
    ADOPTED_MODELS,
    DROPPED_FIELDS,
    DROPPED_FOREIGN_KEYS,
    FIELD_NAMING_COLUMNS,
    FLAG,
    FLAG_TABLE,
    FORMER_FLAG,
    FORMER_MODULE,
    LEGACY_MODULE,
    MODULE,
    OWNERSHIP_TABLES,
    RENAMED_EXTERNAL_IDS,
    SPLIT_VIEWS,
    forget_dropped_fields,
    pre_init_hook,
    rename_external_ids,
    take_over_module,
)

EMPTY_ARCH = "<data/>"
# The nodes that named the fields in DROPPED_FIELDS in the two task form views that had them,
# cut down to one arch; the page title is its translated term.
OLD_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="%s"><group><field name="tester_id"/></group><field name="testcase_ids"/></page>'
    "</xpath>"
    """<xpath expr="//field[@name='tag_ids']" position="before">"""
    '<field name="difficulty"/><field name="knowledge_area_level"/><field name="task_type"/>'
    "</xpath>"
    "</data>"
)
KEPT_FIELDS = ["testcase_ids", "task_type"]
SUB_VIEW_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="Test Cases"><field name="testcase_ids"><tree><field name="tester_id"/></tree></field></page>'
    "</xpath>"
    "</data>"
)
OTHER_MODULE = "other_module"
EXTENDED_MODELS = ("project.project", "project.task")
# Added after the cut from the legacy module, which never held them.
ADDED_FIELDS = (
    ("project.project", "estimate_required_states"),
    ("project.project", "pr_required_states"),
    ("project.task", "pr_legacy"),
    ("project.task", "pr_required_states"),
    ("project.task", "pr_uri"),
    ("project.task", "pull_request_ids"),
    ("project.task", "resolution"),
)
ADDED_RECORDS = (
    ("ir.model", "model_project_pull_request"),
    ("ir.model.constraint", "constraint_project_pull_request_url_unique"),
    ("ir.model.access", "access_project_pull_request_internal"),
    ("ir.model.access", "access_project_pull_request_user"),
    ("ir.ui.view", "project_pull_request_view_form"),
    ("ir.ui.view", "project_pull_request_view_tree"),
    ("ir.ui.view", "project_pull_request_view_kanban"),
    ("ir.ui.view", "project_pull_request_view_search"),
    ("ir.actions.act_window", "project_pull_request_action"),
    ("ir.ui.menu", "project_pull_request_menu"),
    ("ir.model", "model_project_task_stage_log"),
    ("ir.model.access", "access_project_task_stage_log_system"),
    ("ir.ui.view", "project_task_view_kanban_inherit_ai_driven"),
    ("ir.ui.view", "project_task_view_calendar_inherit_ai_driven"),
    # a model this module only extends, and did not extend at the cut
    ("ir.model", "model_project_task_recurrence"),
    *(
        ("ir.model.fields.selection", "selection__%s__%s" % (field, value))
        for field, values in (
            ("project_task__resolution", ("code", "data", "configuration", "no_change")),
            ("project_pull_request__state", ("open", "merged", "closed")),
            ("project_task_stage_log__source", ("live", "tracking", "stage_date", "creation")),
        )
        for value in values
    ),
)
ADDED_CONSTRAINTS = (
    "project_pull_request%",
    "project_pr_required%",
    "project_estimate_required%",
    "project_task_stage_log%",
)
DELEGATING_MODULE = "delegating_module"
DELEGATING_MODEL = "res.partner"
REPLACED_MODULE = "replaced_module"
REPLACING_MODULE = "replacing_module"
BYSTANDER_MODULE = "bystander_" + REPLACED_MODULE
DATABASE_STATE_QUERIES = {
    "external ids": "SELECT id, module, name, model, res_id FROM ir_model_data ORDER BY id",
    "constraints": "SELECT id, module, name FROM ir_model_constraint ORDER BY id",
    "relations": "SELECT id, module, name FROM ir_model_relation ORDER BY id",
    "fields": "SELECT id, model, name, related FROM ir_model_fields ORDER BY id",
    "views": "SELECT id, arch_fs, arch_db::text FROM ir_ui_view ORDER BY id",
    "rules": "SELECT id, domain_force FROM ir_rule ORDER BY id",
    "filters": "SELECT id, domain, context, sort FROM ir_filters ORDER BY id",
    "window actions": "SELECT id, domain, context FROM ir_act_window ORDER BY id",
    "project columns": """
        SELECT column_name FROM information_schema.columns WHERE table_name = 'project_project' ORDER BY column_name
    """,
}


class PreInitHookCase(TransactionCase):
    """The hook is run against this module's own records, put back first in the
    state of a database that holds them under their former names.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = cls.env["ir.module.module"].search([("name", "=", MODULE)])
        cls.former_module = cls._get_module(FORMER_MODULE)
        cls.legacy_module = cls._get_module(LEGACY_MODULE)
        cls._set_module_state(cls.former_module | cls.legacy_module, "uninstalled")

    @classmethod
    def _get_module(cls, name):
        module = cls.env["ir.module.module"].search([("name", "=", name)])
        return module or cls.env["ir.module.module"].create({"name": name})

    @classmethod
    def _set_module_state(cls, modules, state):
        cls.env.cr.execute("UPDATE ir_module_module SET state = %s WHERE id IN %s", (state, tuple(modules.ids)))

    @classmethod
    def _external_ids(cls, module_name):
        cls.env.cr.execute("SELECT model, name, res_id FROM ir_model_data WHERE module = %s", (module_name,))
        return {(model, name): res_id for model, name, res_id in cls.env.cr.fetchall()}

    def _owned_rows(self, table, module):
        self.env.cr.execute(
            sql.SQL("SELECT id, name FROM {} WHERE module = %s").format(sql.Identifier(table)),
            (module.id,),
        )
        return dict(self.env.cr.fetchall())

    def _flag_fields(self):
        self.env.cr.execute(
            "SELECT id, model, name, related FROM ir_model_fields WHERE name IN %s",
            ((FLAG, FORMER_FLAG),),
        )
        return {field_id: (model, name, related) for field_id, model, name, related in self.env.cr.fetchall()}

    def _database_state(self):
        state = {}
        for name, query in DATABASE_STATE_QUERIES.items():
            self.env.cr.execute(query)
            state[name] = self.env.cr.fetchall()
        return state

    def assertDatabaseState(self, expected_state):
        for name, rows in self._database_state().items():
            self.assertEqual(rows, expected_state[name], name)

    def _store_arch(self, view, **archs):
        """Put an arch per language in the database behind the ORM, as a
        database from before the fields in DROPPED_FIELDS were deleted has it.
        """
        self.env.flush_all()
        # without its file, a server in dev mode reads the stored arch too
        self.env.cr.execute("UPDATE ir_ui_view SET arch_db = %s, arch_fs = NULL WHERE id = %s", (Json(archs), view.id))
        self.env.invalidate_all()

    def _stored_archs(self, view):
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", (view.id,))
        return self.env.cr.fetchone()[0]

    def _stored_field_names(self, view):
        return {
            lang: [node.get("name") for node in etree.fromstring(arch).iter("field")]
            for lang, arch in self._stored_archs(view).items()
        }

    def _register_foreign_keys(self, module):
        task_model_id = self.env["ir.model"]._get_id("project.task")
        registrations = self.env["ir.model.constraint"].create(
            [{"name": name, "model": task_model_id, "module": module.id, "type": "f"} for name in DROPPED_FOREIGN_KEYS],
        )
        self.env.flush_all()
        return registrations

    def _put_back_former_names(self):
        """Undo by SQL what the hook renames. The registry keeps the new names,
        so no project may be read or flushed until the hook has run.
        """
        self.env.flush_all()
        cr = self.env.cr
        params = {"flag": FLAG, "former_flag": FORMER_FLAG}
        rename_column(cr, FLAG_TABLE, FLAG, FORMER_FLAG)
        cr.execute(
            """
            UPDATE ir_model_data imd
               SET name = 'field_' || replace(field.model, '.', '_') || '__' || %(former_flag)s
              FROM ir_model_fields field
             WHERE imd.model = 'ir.model.fields'
               AND imd.res_id = field.id
               AND field.name = %(flag)s
            """,
            params,
        )
        cr.execute(
            """
            UPDATE ir_model_fields
               SET name = %(former_flag)s,
                   related = replace(related, %(flag)s, %(former_flag)s)
             WHERE name = %(flag)s
            """,
            params,
        )
        cr.execute(
            """
            UPDATE ir_ui_view
               SET arch_db = replace(arch_db::text, %(flag)s, %(former_flag)s)::jsonb
             WHERE strpos(arch_db::text, %(flag)s) > 0
            """,
            params,
        )
        for table, column in FIELD_NAMING_COLUMNS:
            cr.execute(
                sql.SQL(
                    """
                    UPDATE {table}
                       SET {column} = replace({column}, %(flag)s, %(former_flag)s)
                     WHERE strpos({column}, %(flag)s) > 0
                    """,
                ).format(table=sql.Identifier(table), column=sql.Identifier(column)),
                params,
            )
        for former_name, new_name in RENAMED_EXTERNAL_IDS.items():
            cr.execute(
                "UPDATE ir_model_data SET name = %s WHERE module = %s AND name = %s",
                (former_name, MODULE, new_name),
            )
        self.env.invalidate_all()

    def _create_delegating_field(self):
        """A model of another module that delegates to the task holds a copy of
        the flag, with its external id in that module.
        """
        self.env.cr.execute(
            """
            INSERT INTO ir_model_fields (model, model_id, name, field_description, ttype, state, store, related)
            SELECT model, id, %s, jsonb_build_object('en_US', 'Delegated Flag'), 'boolean', 'base', false, %s
              FROM ir_model
             WHERE model = %s
         RETURNING id
            """,
            (FORMER_FLAG, "task_id.%s" % FORMER_FLAG, DELEGATING_MODEL),
        )
        (field_id,) = self.env.cr.fetchone()
        self.env["ir.model.data"].create(
            {
                "module": DELEGATING_MODULE,
                "name": "field_%s__%s" % (DELEGATING_MODEL.replace(".", "_"), FORMER_FLAG),
                "model": "ir.model.fields",
                "res_id": field_id,
            },
        )
        self.env.flush_all()
        return field_id


@tagged("post_install", "-at_install")
class TestTakeOverFromFormerModule(PreInitHookCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._set_module_state(cls.former_module, "to upgrade")
        view_ids = [res_id for (model, _name), res_id in cls._external_ids(MODULE).items() if model == "ir.ui.view"]
        cls.views = cls.env["ir.ui.view"].browse(view_ids)

    def _simulate_former_module(self):
        self._put_back_former_names()
        self.env.cr.execute("UPDATE ir_model_data SET module = %s WHERE module = %s", (FORMER_MODULE, MODULE))
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_fs = %s || substr(arch_fs, %s) WHERE id IN %s",
            (FORMER_MODULE, len(MODULE) + 1, tuple(self.views.ids)),
        )
        for table in OWNERSHIP_TABLES:
            self.env.cr.execute(
                sql.SQL("UPDATE {} SET module = %s WHERE module = %s").format(sql.Identifier(table)),
                (self.former_module.id, self.module.id),
            )
        self.env.registry.clear_caches()

    def _create_relation(self):
        self.env["ir.model.relation"].create(
            {
                "name": "project_task_res_users_rel",
                "model": self.env["ir.model"]._get_id("project.task"),
                "module": self.module.id,
            },
        )
        self.env.flush_all()

    def test_takes_over_everything_under_its_database_id(self):
        self._create_relation()
        owned_external_ids = self._external_ids(MODULE)
        owned_rows = {table: self._owned_rows(table, self.module) for table in OWNERSHIP_TABLES}
        self._simulate_former_module()
        self.assertFalse(self._external_ids(MODULE))

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), owned_external_ids)
        self.assertFalse(self._external_ids(FORMER_MODULE))
        for table in OWNERSHIP_TABLES:
            self.assertTrue(owned_rows[table], table)
            self.assertEqual(self._owned_rows(table, self.module), owned_rows[table], table)
            self.assertFalse(self._owned_rows(table, self.former_module), table)

    def test_takes_over_from_an_installed_former_module(self):
        owned_external_ids = self._external_ids(MODULE)
        self._simulate_former_module()
        self._set_module_state(self.former_module, "installed")

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), owned_external_ids)

    def test_drops_what_is_already_held_under_the_same_name(self):
        owned_external_ids = self._external_ids(MODULE)
        owned_constraints = self._owned_rows("ir_model_constraint", self.module)
        self._simulate_former_module()
        task_model_id = self.env["ir.model"]._get_id("project.task")
        self.env["ir.model.data"].create(
            {"module": MODULE, "name": "model_project_task", "model": "ir.model", "res_id": task_model_id},
        )
        held_constraint = self.env["ir.model.constraint"].create(
            {
                "name": "project_task_testcase_task_id_fkey",
                "model": self.env["ir.model"]._get_id("project.task.testcase"),
                "module": self.module.id,
                "type": "f",
            },
        )
        self.env.flush_all()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), owned_external_ids)
        self.assertFalse(self._external_ids(FORMER_MODULE))
        constraints = self._owned_rows("ir_model_constraint", self.module)
        self.assertIn(held_constraint.id, constraints)
        self.assertEqual(sorted(constraints.values()), sorted(owned_constraints.values()))
        self.assertFalse(self._owned_rows("ir_model_constraint", self.former_module))

    def test_forgets_the_fields_deleted_since(self):
        view = self.env.ref("%s.view_task_form2_testcase" % MODULE)
        self._store_arch(view, en_US=OLD_ARCH % "Test Cases")
        registrations = self._register_foreign_keys(self.module)
        self._simulate_former_module()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._stored_field_names(view), {"en_US": KEPT_FIELDS})
        self.assertFalse(registrations.exists())

    def test_followers_of_the_checklist_subtypes_stay_subscribed(self):
        task_subtype = self.env.ref("%s.subtasks_subtype" % MODULE)
        project_subtype = self.env.ref("%s.all_subtask_subtype" % MODULE)
        project = self.env["project.project"].create({"name": "Followed Project"})
        task = self.env["project.task"].create({"name": "Followed Task", "project_id": project.id})
        partner = self.env["res.partner"].create({"name": "Checklist Follower"})
        project.message_subscribe(partner_ids=partner.ids, subtype_ids=project_subtype.ids)
        task.message_subscribe(partner_ids=partner.ids, subtype_ids=task_subtype.ids)
        self._simulate_former_module()

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self.env.ref("%s.subtasks_subtype" % MODULE), task_subtype)
        self.assertEqual(self.env.ref("%s.all_subtask_subtype" % MODULE), project_subtype)
        for record, subtype in ((project, project_subtype), (task, task_subtype)):
            follower = record.message_follower_ids.filtered(lambda follower: follower.partner_id == partner)
            self.assertEqual(follower.subtype_ids, subtype, record._name)

    def test_renames_the_flag_column_and_keeps_its_values(self):
        project = self.env["project.project"].create({"name": "Flagged Project", FLAG: True})
        self._simulate_former_module()
        self.assertTrue(column_exists(self.env.cr, FLAG_TABLE, FORMER_FLAG))
        self.assertFalse(column_exists(self.env.cr, FLAG_TABLE, FLAG))

        pre_init_hook(self.env.cr)

        self.assertTrue(column_exists(self.env.cr, FLAG_TABLE, FLAG))
        self.assertFalse(column_exists(self.env.cr, FLAG_TABLE, FORMER_FLAG))
        self.env.invalidate_all()
        self.assertTrue(project[FLAG])

    def test_renames_the_flag_field_on_every_model(self):
        flag_fields = self._flag_fields()
        self.assertLessEqual(set(EXTENDED_MODELS), {model for model, _name, _related in flag_fields.values()})
        self._simulate_former_module()
        delegating_field_id = self._create_delegating_field()
        self.assertEqual({name for _model, name, _related in self._flag_fields().values()}, {FORMER_FLAG})

        pre_init_hook(self.env.cr)

        self.assertEqual(
            self._flag_fields(),
            {**flag_fields, delegating_field_id: (DELEGATING_MODEL, FLAG, "task_id.%s" % FLAG)},
        )
        self.assertEqual(
            self._external_ids(DELEGATING_MODULE),
            {("ir.model.fields", "field_res_partner__%s" % FLAG): delegating_field_id},
        )
        field_ids = {model: field_id for field_id, (model, _name, _related) in flag_fields.items()}
        external_ids = self._external_ids(MODULE)
        for model in EXTENDED_MODELS:
            external_id = ("ir.model.fields", "field_%s__%s" % (model.replace(".", "_"), FLAG))
            self.assertEqual(external_ids.get(external_id), field_ids[model], model)

    def test_renames_the_external_ids_the_data_files_renamed(self):
        records = {new_name: self.env.ref("%s.%s" % (MODULE, new_name)) for new_name in RENAMED_EXTERNAL_IDS.values()}
        self._simulate_former_module()
        former_names = {name for _model, name in self._external_ids(FORMER_MODULE)}
        self.assertLessEqual(set(RENAMED_EXTERNAL_IDS), former_names)

        pre_init_hook(self.env.cr)

        for former_name, new_name in RENAMED_EXTERNAL_IDS.items():
            self.assertEqual(self.env.ref("%s.%s" % (MODULE, new_name)), records[new_name])
            self.assertFalse(self.env.ref("%s.%s" % (MODULE, former_name), raise_if_not_found=False))

    def test_views_are_read_from_the_files_of_this_module(self):
        view_files = self.views.mapped("arch_fs")
        self.assertTrue(all(view_file.startswith(MODULE + "/") for view_file in view_files), view_files)
        self._simulate_former_module()
        former_view_files = self.views.mapped("arch_fs")
        self.assertTrue(
            all(view_file.startswith(FORMER_MODULE + "/") for view_file in former_view_files),
            former_view_files,
        )

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self.views.mapped("arch_fs"), view_files)

    def test_renames_the_flag_in_every_translation_of_a_view(self):
        view = self.env.ref("%s.view_task_form2_common" % MODULE)
        arch = view.arch_db
        self.assertIn(FLAG, arch)
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_db = arch_db || jsonb_build_object('fr_FR', arch_db->>'en_US') WHERE id = %s",
            (view.id,),
        )
        self._simulate_former_module()
        former_arch = arch.replace(FLAG, FORMER_FLAG)
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", (view.id,))
        self.assertEqual(self.env.cr.fetchone()[0], {"en_US": former_arch, "fr_FR": former_arch})

        pre_init_hook(self.env.cr)

        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", (view.id,))
        self.assertEqual(self.env.cr.fetchone()[0], {"en_US": arch, "fr_FR": arch})

    def test_renames_the_flag_in_rules_filters_and_window_actions(self):
        texts = {
            "domain": "[('%s', '=', True)]",
            "context": "{'default_%s': True}",
            "sort": '["%s desc"]',
        }
        new_texts = {key: text % FLAG for key, text in texts.items()}
        former_texts = {key: text % FORMER_FLAG for key, text in texts.items()}
        rule = self.env["ir.rule"].create(
            {
                "name": "Flagged projects",
                "model_id": self.env["ir.model"]._get_id("project.project"),
                "groups": [Command.set(self.env.ref("base.group_system").ids)],
                "domain_force": new_texts["domain"],
            },
        )
        # a filter of one user: Odoo strips the default_ keys from the context of a shared one
        favorite = self.env["ir.filters"].create(
            {"name": "Flagged projects", "model_id": "project.project", "user_id": self.env.uid, **new_texts},
        )
        action = self.env["ir.actions.act_window"].create(
            {
                "name": "Flagged projects",
                "res_model": "project.project",
                "domain": new_texts["domain"],
                "context": new_texts["context"],
            },
        )
        self._simulate_former_module()
        self.assertRecordValues(rule, [{"domain_force": former_texts["domain"]}])
        self.assertRecordValues(favorite, [former_texts])
        self.assertRecordValues(action, [{"domain": former_texts["domain"], "context": former_texts["context"]}])

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertRecordValues(rule, [{"domain_force": new_texts["domain"]}])
        self.assertRecordValues(favorite, [new_texts])
        self.assertRecordValues(action, [{"domain": new_texts["domain"], "context": new_texts["context"]}])

    def test_second_run_changes_nothing(self):
        self._create_relation()
        self._simulate_former_module()
        self._create_delegating_field()
        pre_init_hook(self.env.cr)
        self._set_module_state(self.former_module, "installed")
        state_after_first_run = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertDatabaseState(state_after_first_run)


@tagged("post_install", "-at_install")
class TestTakeOverFromLegacyModule(PreInitHookCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._set_module_state(cls.legacy_module, "installed")
        cls.split_views = cls.env["ir.ui.view"]
        for name in SPLIT_VIEWS:
            cls.split_views |= cls.env.ref("%s.%s" % (MODULE, name))
        view_ids = [
            res_id
            for record, res_id in cls._external_ids(MODULE).items()
            if record[0] == "ir.ui.view" and record not in ADDED_RECORDS
        ]
        cls.views = cls.env["ir.ui.view"].browse(view_ids)

    def _simulate_legacy_module(self):
        """Every record of this module was cut from the legacy one, so all of
        them change hands. A record added here later has to be left out.
        """
        self._put_back_former_names()
        # Two kinds were never the legacy module's alone: a model both modules
        # extend has an external id in each of them, and so has the copy of a
        # field on a model of another module that delegates to the task.
        self.env.cr.execute(
            """
            UPDATE ir_model_data imd
               SET module = %(legacy)s
             WHERE imd.module = %(module)s
               AND NOT (
                   imd.model = 'ir.model'
                   AND imd.res_id NOT IN (SELECT id FROM ir_model WHERE model IN %(models)s)
               )
               AND NOT (
                   imd.model = 'ir.model.fields'
                   AND imd.res_id NOT IN (
                       SELECT id
                         FROM ir_model_fields
                        WHERE model IN %(field_models)s
                          AND (model, name) NOT IN %(added_fields)s
                   )
               )
               AND (imd.model, imd.name) NOT IN %(added_records)s
            """,
            {
                "legacy": LEGACY_MODULE,
                "module": MODULE,
                "models": ADOPTED_MODELS,
                "field_models": ADOPTED_MODELS + EXTENDED_MODELS,
                "added_fields": ADDED_FIELDS,
                "added_records": ADDED_RECORDS,
            },
        )
        self.env.cr.execute(
            "UPDATE ir_model_constraint SET module = %s WHERE module = %s AND name NOT LIKE ALL(%s)",
            (self.legacy_module.id, self.module.id, list(ADDED_CONSTRAINTS)),
        )
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_fs = %s || substr(arch_fs, %s) WHERE id IN %s",
            (LEGACY_MODULE, len(MODULE) + 1, tuple(self.views.ids)),
        )
        self.env.registry.clear_caches()

    def _legacy_records(self):
        return {(model, res_id) for (model, _name), res_id in self._external_ids(LEGACY_MODULE).items()}

    def test_takes_over_every_record_under_its_database_id(self):
        owned_external_ids = self._external_ids(MODULE)
        owned_constraints = self._owned_rows("ir_model_constraint", self.module)
        legacy_external_ids = self._external_ids(LEGACY_MODULE)
        self._simulate_legacy_module()
        self.assertEqual(
            {(model, name) for model, name in self._external_ids(MODULE) if model != "ir.model.fields"},
            {("ir.model", "model_project_project"), ("ir.model", "model_project_task"), *ADDED_RECORDS},
            "every record changed hands but the two models this module only extends and what it added later",
        )

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), owned_external_ids)
        self.assertEqual(self._owned_rows("ir_model_constraint", self.module), owned_constraints)
        self.assertEqual(self._external_ids(LEGACY_MODULE), legacy_external_ids)
        self.assertTrue(column_exists(self.env.cr, FLAG_TABLE, FLAG))
        self.assertFalse(column_exists(self.env.cr, FLAG_TABLE, FORMER_FLAG))

    def _create_deleted_fields(self):
        """The fields in DROPPED_FIELDS as a database from before the cut has
        them: held by the legacy module. Returns {external id name: field id}.
        """
        field_ids = {}
        for model, name in DROPPED_FIELDS:
            self.env.cr.execute(
                """
                INSERT INTO ir_model_fields (model, model_id, name, field_description, ttype, state, store)
                SELECT model, id, %s, jsonb_build_object('en_US', 'Deleted Field'), 'char', 'base', false
                  FROM ir_model
                 WHERE model = %s
             RETURNING id
                """,
                (name, model),
            )
            (field_ids["field_%s__%s" % (model.replace(".", "_"), name)],) = self.env.cr.fetchone()
        self.env["ir.model.data"].create(
            [
                {"module": LEGACY_MODULE, "name": name, "model": "ir.model.fields", "res_id": field_id}
                for name, field_id in field_ids.items()
            ],
        )
        self.env.flush_all()
        return field_ids

    def test_takes_over_the_fields_deleted_since(self):
        field_ids = self._create_deleted_fields()
        registrations = self._register_foreign_keys(self.legacy_module)
        view = self.env.ref("%s.view_task_form2_testcase" % MODULE)
        self._store_arch(view, en_US=OLD_ARCH % "Test Cases")
        self._simulate_legacy_module()

        pre_init_hook(self.env.cr)

        external_ids = self._external_ids(MODULE)
        self.assertEqual(
            {name: external_ids.get(("ir.model.fields", name)) for name in field_ids},
            field_ids,
            "held by this module, they are deleted when it is loaded without them",
        )
        self.assertEqual(self._stored_field_names(view), {"en_US": KEPT_FIELDS})
        self.assertFalse(registrations.exists())

    def test_the_former_module_comes_first(self):
        self._simulate_legacy_module()
        handed_over_records = self._legacy_records()
        self._set_module_state(self.former_module, "installed")

        pre_init_hook(self.env.cr)

        self.assertEqual(self._legacy_records(), handed_over_records)

    def test_followers_of_the_checklist_subtype_stay_subscribed(self):
        subtype = self.env.ref("%s.subtasks_subtype" % MODULE)
        project = self.env["project.project"].create({"name": "Followed Project"})
        task = self.env["project.task"].create({"name": "Followed Task", "project_id": project.id})
        partner = self.env["res.partner"].create({"name": "Checklist Follower"})
        task.message_subscribe(partner_ids=partner.ids, subtype_ids=subtype.ids)
        self._simulate_legacy_module()

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self.env.ref("%s.subtasks_subtype" % MODULE), subtype)
        follower = task.message_follower_ids.filtered(lambda follower: follower.partner_id == partner)
        self.assertEqual(follower.subtype_ids, subtype)

    def test_split_views_are_taken_over_empty(self):
        whole_view = self.env.ref("%s.view_task_form2" % MODULE)
        whole_arch = whole_view.arch_db
        self._simulate_legacy_module()

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self.split_views.mapped("arch_db"), [EMPTY_ARCH] * len(SPLIT_VIEWS))
        self.assertEqual(whole_view.arch_db, whole_arch)

    def test_adopted_views_forget_the_file_of_the_legacy_module(self):
        kept_view = self.env["ir.ui.view"].create(
            {"name": "legacy.kept.view", "model": "project.task", "arch": "<tree><field name='name'/></tree>"},
        )
        kept_view.arch_fs = "%s/views/project_task_views.xml" % LEGACY_MODULE
        self.env["ir.model.data"].create(
            {"module": LEGACY_MODULE, "name": "view_kept", "model": "ir.ui.view", "res_id": kept_view.id},
        )
        self.env.flush_all()
        self._simulate_legacy_module()
        self.env.invalidate_all()
        legacy_view_files = self.views.mapped("arch_fs")
        self.assertTrue(
            all(view_file.startswith(LEGACY_MODULE + "/") for view_file in legacy_view_files),
            legacy_view_files,
        )

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self.views.mapped("arch_fs"), [False] * len(self.views))
        self.assertEqual(kept_view.arch_fs, "%s/views/project_task_views.xml" % LEGACY_MODULE)

    def test_second_run_changes_nothing(self):
        split_archs = self.split_views.mapped("arch_db")
        self._simulate_legacy_module()
        pre_init_hook(self.env.cr)
        self.env.invalidate_all()
        for view, arch in zip(self.split_views, split_archs):
            view.arch_db = arch
        self.env.flush_all()
        state_after_first_run = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertDatabaseState(state_after_first_run)

    def test_leaves_the_other_legacy_records_alone(self):
        kept_field = self.env["ir.model.fields"]._get("project.task", "name")
        kept_view = self.env.ref("project.view_task_form2")
        self.env["ir.model.data"].create(
            [
                {"module": LEGACY_MODULE, "name": "field_kept", "model": "ir.model.fields", "res_id": kept_field.id},
                {"module": LEGACY_MODULE, "name": "view_kept", "model": "ir.ui.view", "res_id": kept_view.id},
            ],
        )
        self.env.flush_all()
        self._simulate_legacy_module()

        pre_init_hook(self.env.cr)

        legacy_external_ids = self._external_ids(LEGACY_MODULE)
        self.assertEqual(legacy_external_ids.get(("ir.model.fields", "field_kept")), kept_field.id)
        self.assertEqual(legacy_external_ids.get(("ir.ui.view", "view_kept")), kept_view.id)


@tagged("post_install", "-at_install")
class TestNoModuleToTakeOverFrom(PreInitHookCase):
    def test_leaves_the_database_untouched(self):
        self._put_back_former_names()
        self._create_delegating_field()
        self.env.cr.execute("UPDATE ir_model_data SET module = %s WHERE module = %s", (FORMER_MODULE, MODULE))
        self.env.cr.execute(
            "UPDATE ir_model_constraint SET module = %s WHERE module = %s",
            (self.legacy_module.id, self.module.id),
        )
        state_before = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertDatabaseState(state_before)


@tagged("post_install", "-at_install")
class TestForgetDroppedFields(PreInitHookCase):
    """forget_dropped_fields on a database that already holds everything under
    this module, as its update runs it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.view = cls.env.ref("%s.view_task_form2_testcase" % MODULE)

    def test_a_view_that_names_them_no_longer_blocks_the_views_of_its_form(self):
        other_view = self.env.ref("%s.view_task_form2" % MODULE)
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases")
        with self.assertRaises(ValidationError) as refusal:
            other_view.write({"arch": other_view.arch})
        self.assertIn('Field "difficulty" does not exist', refusal.exception.args[0])

        forget_dropped_fields(self.env.cr)

        self.env.invalidate_all()
        other_view.write({"arch": other_view.arch})

    def test_takes_them_out_of_every_translation_of_a_view(self):
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases", fr_FR=OLD_ARCH % "Cas de test")

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_field_names(self.view), {"en_US": KEPT_FIELDS, "fr_FR": KEPT_FIELDS})
        self.assertIn('string="Cas de test"', self._stored_archs(self.view)["fr_FR"])

    def test_leaves_the_field_of_a_sub_view_alone(self):
        self._store_arch(self.view, en_US=SUB_VIEW_ARCH)

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_archs(self.view), {"en_US": SUB_VIEW_ARCH})

    def test_leaves_the_views_of_other_modules_alone(self):
        self.env.cr.execute(
            "UPDATE ir_model_data SET module = %s WHERE model = 'ir.ui.view' AND res_id = %s",
            (OTHER_MODULE, self.view.id),
        )
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases")

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_archs(self.view), {"en_US": OLD_ARCH % "Test Cases"})

    def test_deletes_the_registration_of_their_foreign_keys_to_this_module(self):
        other_module = self._get_module(OTHER_MODULE)
        registrations = self._register_foreign_keys(self.module) | self._register_foreign_keys(other_module)

        forget_dropped_fields(self.env.cr)

        self.assertEqual(registrations.exists().mapped("module"), other_module)

    def test_second_run_changes_nothing(self):
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases")
        self._register_foreign_keys(self.module)
        forget_dropped_fields(self.env.cr)
        state_after_first_run = self._database_state()

        forget_dropped_fields(self.env.cr)

        self.assertDatabaseState(state_after_first_run)


@tagged("post_install", "-at_install")
class TestTakeOverModule(TransactionCase):
    """take_over_module and rename_external_ids between two modules that are not
    this one, next to a third whose name ends with the replaced module's.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        modules = cls.env["ir.module.module"].create(
            [{"name": REPLACED_MODULE}, {"name": REPLACING_MODULE}, {"name": BYSTANDER_MODULE}],
        )
        cls.replaced_module, cls.replacing_module, cls.bystander_module = modules
        cls.task_model_id = cls.env["ir.model"]._get_id("project.task")
        cls.view = cls._create_view(REPLACED_MODULE)
        cls.bystander_view = cls._create_view(BYSTANDER_MODULE)
        cls.menu = cls.env["ir.ui.menu"].create({"name": "Replaced Menu"})
        cls.external_ids = cls.env["ir.model.data"].create(
            [
                {"module": REPLACED_MODULE, "name": "replaced_view", "model": "ir.ui.view", "res_id": cls.view.id},
                {
                    "module": REPLACED_MODULE,
                    "name": "replaced_menu",
                    "model": "ir.ui.menu",
                    "res_id": cls.menu.id,
                    "noupdate": True,
                },
            ],
        )
        cls.bystander_external_id = cls.env["ir.model.data"].create(
            {
                "module": BYSTANDER_MODULE,
                "name": "replaced_view",
                "model": "ir.ui.view",
                "res_id": cls.bystander_view.id,
            },
        )
        cls.constraint = cls._create_constraint(cls.replaced_module)
        cls.bystander_constraint = cls._create_constraint(cls.bystander_module)
        cls.relation = cls._create_relation(cls.replaced_module)
        cls.env.flush_all()

    @classmethod
    def _create_view(cls, module_name):
        view = cls.env["ir.ui.view"].create(
            {"name": "%s.view" % module_name, "model": "project.task", "arch": "<tree><field name='name'/></tree>"},
        )
        view.arch_fs = "%s/views/project_task_views.xml" % module_name
        return view

    @classmethod
    def _create_constraint(cls, module):
        return cls.env["ir.model.constraint"].create(
            {"name": "replaced_constraint", "model": cls.task_model_id, "module": module.id, "type": "u"},
        )

    @classmethod
    def _create_relation(cls, module):
        return cls.env["ir.model.relation"].create(
            {"name": "replaced_rel", "model": cls.task_model_id, "module": module.id},
        )

    def _rows(self, table, record_ids):
        columns = "id, module, name, model, res_id, noupdate" if table == "ir_model_data" else "id, module, name"
        self.env.cr.execute(
            sql.SQL("SELECT {} FROM {} WHERE id IN %s ORDER BY id").format(sql.SQL(columns), sql.Identifier(table)),
            (tuple(record_ids),),
        )
        return self.env.cr.fetchall()

    def test_everything_changes_module_under_its_database_id(self):
        take_over_module(self.env.cr, REPLACED_MODULE, REPLACING_MODULE)

        self.assertEqual(
            self._rows("ir_model_data", self.external_ids.ids),
            [
                (self.external_ids[0].id, REPLACING_MODULE, "replaced_view", "ir.ui.view", self.view.id, False),
                (self.external_ids[1].id, REPLACING_MODULE, "replaced_menu", "ir.ui.menu", self.menu.id, True),
            ],
        )
        self.assertEqual(
            self._rows("ir_model_constraint", self.constraint.ids),
            [(self.constraint.id, self.replacing_module.id, "replaced_constraint")],
        )
        self.assertEqual(
            self._rows("ir_model_relation", self.relation.ids),
            [(self.relation.id, self.replacing_module.id, "replaced_rel")],
        )
        self.env.invalidate_all()
        self.assertEqual(self.view.arch_fs, "%s/views/project_task_views.xml" % REPLACING_MODULE)

    def test_a_module_whose_name_ends_the_same_keeps_what_it_owns(self):
        take_over_module(self.env.cr, REPLACED_MODULE, REPLACING_MODULE)

        self.assertEqual(
            self._rows("ir_model_data", self.bystander_external_id.ids),
            [
                (
                    self.bystander_external_id.id,
                    BYSTANDER_MODULE,
                    "replaced_view",
                    "ir.ui.view",
                    self.bystander_view.id,
                    False,
                ),
            ],
        )
        self.assertEqual(
            self._rows("ir_model_constraint", self.bystander_constraint.ids),
            [(self.bystander_constraint.id, self.bystander_module.id, "replaced_constraint")],
        )
        self.env.invalidate_all()
        self.assertEqual(self.bystander_view.arch_fs, "%s/views/project_task_views.xml" % BYSTANDER_MODULE)

    def test_a_row_the_replacing_module_already_holds_is_dropped(self):
        held_external_id = self.env["ir.model.data"].create(
            {"module": REPLACING_MODULE, "name": "replaced_view", "model": "ir.ui.view", "res_id": self.view.id},
        )
        held_constraint = self._create_constraint(self.replacing_module)
        held_relation = self._create_relation(self.replacing_module)
        self.env.flush_all()

        take_over_module(self.env.cr, REPLACED_MODULE, REPLACING_MODULE)

        self.assertEqual(
            [row[:3] for row in self._rows("ir_model_data", (self.external_ids | held_external_id).ids)],
            [
                (self.external_ids[1].id, REPLACING_MODULE, "replaced_menu"),
                (held_external_id.id, REPLACING_MODULE, "replaced_view"),
            ],
        )
        self.assertEqual(
            self._rows("ir_model_constraint", (self.constraint | held_constraint).ids),
            [(held_constraint.id, self.replacing_module.id, "replaced_constraint")],
        )
        self.assertEqual(
            self._rows("ir_model_relation", (self.relation | held_relation).ids),
            [(held_relation.id, self.replacing_module.id, "replaced_rel")],
        )
        self.env.invalidate_all()
        self.assertTrue(self.view.exists(), "the record a dropped external id stood for stays")

    def test_second_take_over_changes_nothing(self):
        take_over_module(self.env.cr, REPLACED_MODULE, REPLACING_MODULE)
        record_ids = {
            "ir_model_data": (self.external_ids | self.bystander_external_id).ids,
            "ir_model_constraint": (self.constraint | self.bystander_constraint).ids,
            "ir_model_relation": self.relation.ids,
        }
        rows_after_first_run = {table: self._rows(table, ids) for table, ids in record_ids.items()}

        take_over_module(self.env.cr, REPLACED_MODULE, REPLACING_MODULE)

        for table, ids in record_ids.items():
            self.assertEqual(self._rows(table, ids), rows_after_first_run[table], table)

    def test_ownership_is_kept_when_the_replacing_module_is_not_listed(self):
        take_over_module(self.env.cr, REPLACED_MODULE, "module_not_in_the_list")

        self.assertEqual(
            self._rows("ir_model_constraint", self.constraint.ids),
            [(self.constraint.id, self.replaced_module.id, "replaced_constraint")],
        )
        self.assertEqual(
            self._rows("ir_model_relation", self.relation.ids),
            [(self.relation.id, self.replaced_module.id, "replaced_rel")],
        )

    def test_renames_the_external_ids_of_one_module(self):
        rename_external_ids(self.env.cr, REPLACED_MODULE, {"replaced_view": "renamed_view", "unknown": "renamed"})

        self.assertEqual(
            [row[:3] for row in self._rows("ir_model_data", (self.external_ids | self.bystander_external_id).ids)],
            [
                (self.external_ids[0].id, REPLACED_MODULE, "renamed_view"),
                (self.external_ids[1].id, REPLACED_MODULE, "replaced_menu"),
                (self.bystander_external_id.id, BYSTANDER_MODULE, "replaced_view"),
            ],
        )

    def test_a_new_name_already_taken_leaves_the_former_one(self):
        rename_external_ids(self.env.cr, REPLACED_MODULE, {"replaced_view": "replaced_menu"})

        self.assertEqual(
            [row[:3] for row in self._rows("ir_model_data", self.external_ids.ids)],
            [
                (self.external_ids[0].id, REPLACED_MODULE, "replaced_view"),
                (self.external_ids[1].id, REPLACED_MODULE, "replaced_menu"),
            ],
        )
