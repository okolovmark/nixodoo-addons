from psycopg2 import sql

from odoo.tests.common import TransactionCase, tagged
from odoo.tools.convert import convert_file

from ..hooks import (
    ADOPTED_FOREIGN_KEYS,
    ADOPTED_RECORDS,
    EXTENDED_MODEL_RECORDS,
    MODEL_RECORDS,
    MODULE,
    OWN_MODEL_FIELDS,
    OWN_MODELS,
    REPORT_MODEL,
    SETTINGS_FILE,
    post_init_hook,
    pre_init_hook,
)
from odoo.addons.ai_claude.hooks import OWNERSHIP_TABLES, declared_fields, declared_selections

HOLDER = "ai_assistant_former_holder"
EXTENDER = "ai_assistant_extender"
# Records this module brought along with the move, which no holder has.
NEW_RECORDS = {
    ("ir.cron", "cron_claude_incident_watch"),
    ("ir.actions.server", "cron_claude_incident_watch_ir_actions_server"),
}
REFLECTED_MODELS = (
    "ir.model",
    "ir.model.fields",
    "ir.model.fields.selection",
    "ir.model.constraint",
    "ir.model.relation",
)
BREAKER_SEEDS = ("config_claude_consecutive_failures", "config_claude_circuit_open_until")


class AdoptRecordsCase(TransactionCase):
    """The records of this module are first handed to another module, as a database holds them
    that had them from that module, and the hook is run to take them back."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Not "installed": ir.module.module caches those past the rollback.
        cls.module = cls.env["ir.module.module"].search([("name", "=", MODULE)])
        cls.holder = cls.env["ir.module.module"].create({"name": HOLDER})
        cls.env.flush_all()

    def _external_ids(self, module_name):
        self.env.cr.execute("SELECT model, name, res_id FROM ir_model_data WHERE module = %s", (module_name,))
        return {(model, name): res_id for model, name, res_id in self.env.cr.fetchall()}

    def _owned_rows(self, module):
        rows = {}
        for table in OWNERSHIP_TABLES:
            self.env.cr.execute(
                sql.SQL("SELECT id, name FROM {} WHERE module = %s").format(sql.Identifier(table)), (module.id,)
            )
            rows[table] = dict(self.env.cr.fetchall())
        return rows

    def _view_files(self, view_ids):
        self.env.cr.execute("SELECT id, arch_fs FROM ir_ui_view WHERE id IN %s", (tuple(view_ids),))
        return dict(self.env.cr.fetchall())

    def _adopted_view_ids(self):
        return [res_id for (model, _name), res_id in self._external_ids(MODULE).items() if model == "ir.ui.view"]

    def _parameters(self, prefix):
        self.env.cr.execute(
            "SELECT id, key, value FROM ir_config_parameter WHERE left(key, length(%s) + 1) = %s || '.'",
            (prefix, prefix),
        )
        return {key.split(".", 1)[1]: (param_id, value) for param_id, key, value in self.env.cr.fetchall()}

    def _database_state(self):
        return {
            "external_ids": {name: self._external_ids(name) for name in (MODULE, HOLDER)},
            "owned_rows": {module.name: self._owned_rows(module) for module in self.module | self.holder},
            "view_files": self._view_files(self._adopted_view_ids()),
            "parameters": {name: self._parameters(name) for name in (MODULE, HOLDER)},
        }

    def _report_model_id(self, module_name):
        self.env.cr.execute("SELECT id FROM ir_model WHERE model = %s", (REPORT_MODEL % module_name,))
        return (self.env.cr.fetchone() or [None])[0]

    def _hand_over_to_holder(self):
        self.env.cr.execute(OWN_MODEL_FIELDS, {"models": OWN_MODELS})
        params = {
            "module": MODULE,
            "holder": HOLDER,
            "records": ADOPTED_RECORDS + MODEL_RECORDS + EXTENDED_MODEL_RECORDS,
            "fields": tuple({*declared_fields(MODULE), *self.env.cr.fetchall()}),
            "selections": declared_selections(MODULE),
            "models": OWN_MODELS,
            "foreign_keys": ADOPTED_FOREIGN_KEYS,
        }
        self.env.cr.execute(
            """
            UPDATE ir_model_data imd
               SET module = %(holder)s
             WHERE imd.module = %(module)s
               AND (
                   (imd.model, imd.name) IN %(records)s
                   OR (
                       imd.model = 'ir.model.fields'
                       AND imd.res_id IN (SELECT id FROM ir_model_fields WHERE (model, name) IN %(fields)s)
                   )
                   OR (
                       imd.model = 'ir.model.fields.selection'
                       AND imd.res_id IN (
                           SELECT selection.id
                             FROM ir_model_fields_selection selection
                             JOIN ir_model_fields field ON field.id = selection.field_id
                            WHERE (field.model, field.name, selection.value) IN %(selections)s
                       )
                   )
                   OR (
                       imd.model = 'ir.model.constraint'
                       AND imd.res_id IN (
                           SELECT owned.id
                             FROM ir_model_constraint owned
                             JOIN ir_model ON ir_model.id = owned.model
                            WHERE ir_model.model IN %(models)s OR owned.name IN %(foreign_keys)s
                       )
                   )
               )
         RETURNING imd.model, imd.res_id
            """,
            params,
        )
        handed_view_ids = tuple(res_id for model, res_id in self.env.cr.fetchall() if model == "ir.ui.view")
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_fs = %s || substr(arch_fs, %s) WHERE id IN %s",
            (HOLDER, len(MODULE) + 1, handed_view_ids),
        )
        self.env.cr.execute(
            "UPDATE ir_config_parameter SET key = %(holder)s || substr(key, length(%(module)s) + 1)"
            " WHERE key LIKE %(module)s || '.%%'",
            params,
        )
        for table in OWNERSHIP_TABLES:
            self.env.cr.execute(
                sql.SQL(
                    """
                    UPDATE {} owned
                       SET module = %(holder_id)s
                      FROM ir_model
                     WHERE owned.module = %(module_id)s
                       AND owned.model = ir_model.id
                       AND (ir_model.model IN %(models)s OR owned.name IN %(foreign_keys)s)
                    """
                ).format(sql.Identifier(table)),
                dict(params, holder_id=self.holder.id, module_id=self.module.id),
            )
        self._hand_over_report_model(HOLDER)
        self.env.registry.clear_caches()
        return handed_view_ids

    def _hand_over_report_model(self, holder):
        """The xlsx report model is named after the module that defines it, as are the external ids of its
        model and fields."""
        model_id = self._report_model_id(MODULE)
        self.env.cr.execute(
            "UPDATE ir_model SET model = %(old_model)s WHERE id = %(model_id)s;"
            " UPDATE ir_model_fields SET model = %(old_model)s WHERE model_id = %(model_id)s;"
            " UPDATE ir_model_data SET module = %(holder)s, name = replace(name, %(new_ref)s, %(old_ref)s)"
            "  WHERE module = %(module)s"
            "    AND ((model = 'ir.model' AND res_id = %(model_id)s)"
            "      OR (model = 'ir.model.fields' AND res_id IN (SELECT id FROM ir_model_fields WHERE model_id = %(model_id)s)))",
            {
                "model_id": model_id,
                "old_model": REPORT_MODEL % holder,
                "module": MODULE,
                "holder": holder,
                "new_ref": (REPORT_MODEL % MODULE).replace(".", "_"),
                "old_ref": (REPORT_MODEL % holder).replace(".", "_"),
            },
        )

    def _add_report_model_field(self):
        """Abstract models have no reflected field here: one stands for those of a database that has."""
        model_id = self._report_model_id(MODULE)
        self.env.cr.execute(
            "INSERT INTO ir_model_fields (name, model, model_id, ttype, state, field_description)"
            " VALUES ('x_sheet', %s, %s, 'char', 'manual', '{\"en_US\": \"Sheet\"}') RETURNING id",
            (REPORT_MODEL % MODULE, model_id),
        )
        [field_id] = self.env.cr.fetchone()
        name = "field_%s__x_sheet" % (REPORT_MODEL % MODULE).replace(".", "_")
        self.env.cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id) VALUES (%s, %s, 'ir.model.fields', %s)",
            (MODULE, name, field_id),
        )
        return field_id

    def _add_unique_constraint(self):
        """A constraint `_sql_constraints` declares has an external id."""
        self.env.cr.execute(
            "INSERT INTO ir_model_constraint (name, model, module, type, definition)"
            " VALUES ('ai_report_name_uniq', %s, %s, 'u', 'unique (name)') RETURNING id",
            (self.env["ir.model"]._get_id("ai.report"), self.module.id),
        )
        [constraint_id] = self.env.cr.fetchone()
        self.env.cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id)"
            " VALUES (%s, 'constraint_ai_report_name_uniq', 'ir.model.constraint', %s)",
            (MODULE, constraint_id),
        )

    def _install(self):
        """What installing this module does to its settings: the hooks around its settings file loaded in
        init mode."""
        pre_init_hook(self.env.cr)
        convert_file(self.env.cr, MODULE, SETTINGS_FILE, {}, mode="init", kind="data")
        self.env.flush_all()
        post_init_hook(self.env.cr, self.env.registry)

    def _setting(self, name):
        return self.env.ref(f"{MODULE}.{name}", raise_if_not_found=False)


@tagged("post_install", "-at_install")
class TestAdoptRecords(AdoptRecordsCase):
    def test_takes_every_record_back_under_its_database_id(self):
        self._add_unique_constraint()
        external_ids = self._external_ids(MODULE)
        owned_rows = self._owned_rows(self.module)
        self._hand_over_to_holder()
        self.assertFalse(set(ADOPTED_RECORDS + MODEL_RECORDS) & set(self._external_ids(MODULE)))
        self.assertFalse(self._owned_rows(self.module)["ir_model_constraint"])

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), external_ids)
        self.assertEqual(self._owned_rows(self.module), owned_rows)
        self.assertEqual(self._external_ids(HOLDER), {})
        self.assertEqual(self._owned_rows(self.holder), {table: {} for table in OWNERSHIP_TABLES})

    def test_adopted_records_are_every_record_of_its_data_files(self):
        data_records = {(model, name) for model, name in self._external_ids(MODULE) if model not in REFLECTED_MODELS}
        self.assertEqual(data_records - set(ADOPTED_RECORDS), NEW_RECORDS)
        self.assertEqual(set(ADOPTED_RECORDS) - data_records, set())

    def test_the_conversations_of_its_assistants_go_with_their_values(self):
        selections = declared_selections(MODULE)
        self.assertIn(("ai.conversation", "assistant", "ask_ai"), selections)
        self.assertIn(("ai.conversation", "assistant", "app"), selections)
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertFalse(
            [name for model, name in self._external_ids(HOLDER) if model == "ir.model.fields.selection"],
            "a value left with the holder would delete its conversations when the holder is updated",
        )

    def test_views_are_read_from_the_files_of_this_module(self):
        view_files = self._view_files(self._adopted_view_ids())
        handed_view_ids = self._hand_over_to_holder()
        self.assertTrue(
            all(self._view_files(handed_view_ids)[view_id].startswith(HOLDER + "/") for view_id in handed_view_ids)
        )

        pre_init_hook(self.env.cr)

        self.assertEqual(self._view_files(view_files), view_files)

    def test_the_holder_s_report_model_keeps_its_id_under_this_module_s_name(self):
        model_id = self._report_model_id(MODULE)
        field_id = self._add_report_model_field()
        external_ids = self._external_ids(MODULE)
        self._hand_over_to_holder()
        self.assertEqual(self._report_model_id(HOLDER), model_id)

        pre_init_hook(self.env.cr)

        self.assertEqual(self._report_model_id(MODULE), model_id)
        self.assertIsNone(self._report_model_id(HOLDER))
        self.env.cr.execute("SELECT model FROM ir_model_fields WHERE id = %s", (field_id,))
        self.assertEqual(self.env.cr.fetchone()[0], REPORT_MODEL % MODULE)
        self.assertEqual(self._external_ids(MODULE), external_ids)

    def test_a_report_model_of_this_module_s_name_leaves_the_holder_s_alone(self):
        self._hand_over_to_holder()
        held_model_id = self._report_model_id(HOLDER)
        self.env.cr.execute(
            "INSERT INTO ir_model (model, name, state, \"order\") VALUES (%s, '{\"en_US\": \"Report\"}', 'base', 'id')"
            " RETURNING id",
            (REPORT_MODEL % MODULE,),
        )
        [model_id] = self.env.cr.fetchone()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._report_model_id(MODULE), model_id)
        self.assertEqual(self._report_model_id(HOLDER), held_model_id)

    def test_the_holder_s_settings_take_this_module_s_name_with_their_value(self):
        self.env["ir.config_parameter"].set_param(f"{MODULE}.max_rows", "120")
        self.env.flush_all()
        parameters = self._parameters(MODULE)
        self._hand_over_to_holder()
        self.assertEqual(self._parameters(HOLDER), parameters)
        self.env.cr.execute(
            "INSERT INTO ir_config_parameter (key, value) VALUES (%s, '7'), (%s, '9')",
            (f"{HOLDER}.digest_recipients_old", f"{MODULE}.tool_seconds"),
        )

        pre_init_hook(self.env.cr)

        renamed = self._parameters(MODULE)
        self.assertEqual(renamed["max_rows"], parameters["max_rows"])
        self.assertEqual(renamed["digest_recipients_old"][1], "7")
        self.assertEqual(renamed["tool_seconds"][1], "9", "a name this module holds already is left alone")
        self.assertEqual(self._parameters(HOLDER), {"tool_seconds": parameters["tool_seconds"]})

    def test_noupdate_records_keep_the_values_the_data_files_overwrite_at_install(self):
        external_ids = self._external_ids(MODULE)
        max_rows_id = external_ids[("ir.config_parameter", "config_max_rows")]
        cron = self.env["ir.cron"].browse(external_ids[("ir.cron", "cron_close_stale_answers")])
        self.env["ir.config_parameter"].browse(max_rows_id).value = "120"
        cron.write({"interval_number": 9, "active": False})
        self.env.flush_all()
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)
        # What loading the data files at install does to them.
        self.env["ir.config_parameter"].browse(max_rows_id).value = "200"
        cron.write({"interval_number": 5, "active": True})
        self.env.flush_all()
        post_init_hook(self.env.cr, self.env.registry)

        self.assertEqual(self.env["ir.config_parameter"].browse(max_rows_id).value, "120")
        self.assertEqual((cron.interval_number, cron.active), (9, False))

    def test_a_setting_the_holder_had_removed_is_removed_again(self):
        self._hand_over_to_holder()
        self.env.ref(f"{HOLDER}.config_max_rows").unlink()

        self._install()

        self.assertFalse(self._setting("config_max_rows"))
        self.assertNotIn("max_rows", self._parameters(MODULE))
        self.assertTrue(self._setting("config_claude_max_tool_rounds"), "the others stay")

    def test_a_setting_emptied_then_filled_again_keeps_its_value(self):
        """Emptied, a setting loses its row and its external id; filled again, it is a row of its own."""
        self.env["ir.config_parameter"].set_param(f"{MODULE}.incident_emails", False)
        self.env["ir.config_parameter"].set_param(f"{MODULE}.incident_emails", "ops@example.com")
        self.env.flush_all()
        self._hand_over_to_holder()
        self.assertFalse(
            self.env["ir.model.data"].search(
                [("module", "in", (MODULE, HOLDER)), ("name", "=", "config_incident_emails")]
            )
        )

        self._install()

        self.assertEqual(self._parameters(MODULE)["incident_emails"][1], "ops@example.com")
        self.assertEqual(self._setting("config_incident_emails").value, "ops@example.com")

    def test_a_setting_whose_name_is_taken_keeps_the_taken_row(self):
        self._hand_over_to_holder()
        held_max_rows = self._parameters(HOLDER)["max_rows"]
        self.env.cr.execute(
            "INSERT INTO ir_config_parameter (key, value) VALUES (%s, '9') RETURNING id", (f"{MODULE}.max_rows",)
        )
        [taken_id] = self.env.cr.fetchone()

        self._install()

        self.assertEqual(self._setting("config_max_rows").id, taken_id)
        self.assertEqual(self._parameters(MODULE)["max_rows"], (taken_id, "9"))
        self.assertEqual(self._parameters(HOLDER)["max_rows"], held_max_rows, "the holder's row keeps its name")

    def test_the_breaker_seeds_the_holder_had_removed_are_seeded_again(self):
        """The seeds have no forcecreate="0": an update of the holder created them again."""
        self._hand_over_to_holder()
        for name in BREAKER_SEEDS:
            self.env.ref(f"{HOLDER}.{name}").unlink()

        self._install()

        self.assertEqual([self._setting(name).value for name in BREAKER_SEEDS], ["0", "0"])

    def test_second_run_changes_nothing(self):
        self._hand_over_to_holder()
        pre_init_hook(self.env.cr)
        state_after_first_run = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_after_first_run)


@tagged("post_install", "-at_install")
class TestExtenders(AdoptRecordsCase):
    """A module that extends a model of this module has the external id of that model too."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.extender = cls.env["ir.module.module"].create({"name": EXTENDER})
        cls.env.flush_all()

    def _extend_report_model(self):
        self.env.cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id) VALUES (%s, 'model_ai_report', 'ir.model', %s);"
            " INSERT INTO ir_config_parameter (key, value) VALUES (%s, '1')",
            (EXTENDER, self.env["ir.model"]._get_id("ai.report"), f"{EXTENDER}.own_setting"),
        )

    def test_a_module_that_depends_on_this_one_keeps_what_it_extends(self):
        self.env["ir.module.module.dependency"].create({"module_id": self.extender.id, "name": MODULE})
        self._hand_over_to_holder()
        self._extend_report_model()

        pre_init_hook(self.env.cr)

        self.assertEqual(set(self._external_ids(EXTENDER)), {("ir.model", "model_ai_report")})
        self.assertEqual(set(self._parameters(EXTENDER)), {"own_setting"})
        self.assertEqual(self._external_ids(HOLDER), {})

    def test_only_the_holder_of_its_records_hands_over_its_settings(self):
        self._extend_report_model()

        pre_init_hook(self.env.cr)

        self.assertEqual(set(self._parameters(EXTENDER)), {"own_setting"})
        self.assertNotIn("own_setting", self._parameters(MODULE))


@tagged("post_install", "-at_install")
class TestNothingToAdopt(AdoptRecordsCase):
    def test_leaves_the_database_untouched(self):
        state_before = self._database_state()

        pre_init_hook(self.env.cr)
        post_init_hook(self.env.cr, self.env.registry)

        self.assertEqual(self._database_state(), state_before)
