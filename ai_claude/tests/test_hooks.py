from psycopg2 import sql

from odoo.tests.common import TransactionCase, tagged

from ..hooks import (
    ADOPTED_FOREIGN_KEYS,
    ADOPTED_RECORDS,
    MODEL_RECORDS,
    MODULE,
    OWNERSHIP_TABLES,
    declared_fields,
    declared_selections,
    pre_init_hook,
)

HOLDER = "ai_claude_former_holder"
# Sorts before the holder, so that it would be the first to hand over.
EXTENDER = "ai_claude_extender"
EXTENDED_NAMES = ("model_ai_exchange_mixin", "field_ai_exchange_mixin__provider")


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

    def _database_state(self):
        return {
            "external_ids": {name: self._external_ids(name) for name in (MODULE, HOLDER)},
            "owned_rows": {module.name: self._owned_rows(module) for module in self.module | self.holder},
            "view_files": self._view_files(self._adopted_view_ids()),
        }

    def _hand_over_to_holder(self):
        params = {
            "module": MODULE,
            "holder": HOLDER,
            "records": ADOPTED_RECORDS + MODEL_RECORDS,
            "fields": declared_fields(MODULE),
            "selections": declared_selections(MODULE),
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
        for table in OWNERSHIP_TABLES:
            self.env.cr.execute(
                sql.SQL("UPDATE {} SET module = %s WHERE module = %s AND name IN %s").format(sql.Identifier(table)),
                (self.holder.id, self.module.id, ADOPTED_FOREIGN_KEYS),
            )
        self.env.registry.clear_caches()
        return handed_view_ids


@tagged("post_install", "-at_install")
class TestAdoptRecords(AdoptRecordsCase):
    def test_takes_every_record_back_under_its_database_id(self):
        external_ids = self._external_ids(MODULE)
        owned_rows = self._owned_rows(self.module)
        self._hand_over_to_holder()
        self.assertFalse(set(ADOPTED_RECORDS + MODEL_RECORDS) & set(self._external_ids(MODULE)))
        self.assertFalse(set(ADOPTED_FOREIGN_KEYS) & set(self._owned_rows(self.module)["ir_model_constraint"].values()))

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), external_ids)
        self.assertEqual(self._owned_rows(self.module), owned_rows)
        self.assertEqual(self._external_ids(HOLDER), {})
        self.assertEqual(self._owned_rows(self.holder), {table: {} for table in OWNERSHIP_TABLES})

    def test_views_are_read_from_the_files_of_this_module(self):
        view_files = self._view_files(self._adopted_view_ids())
        handed_view_ids = self._hand_over_to_holder()
        self.assertTrue(
            all(self._view_files(handed_view_ids)[view_id].startswith(HOLDER + "/") for view_id in handed_view_ids)
        )

        pre_init_hook(self.env.cr)

        self.assertEqual(self._view_files(view_files), view_files)

    def test_leaves_the_holder_its_own_assistant_value(self):
        """An assistant's value cascades on delete: left without the module that declares it, an update
        of that module would delete every conversation of that assistant."""
        field = self.env["ir.model.fields"]._get("ai.conversation", "assistant")
        self.env.cr.execute(
            "INSERT INTO ir_model_fields_selection (field_id, value, name, sequence)"
            " VALUES (%s, 'holder_value', '{\"en_US\": \"Holder value\"}', 1) RETURNING id",
            (field.id,),
        )
        [selection_id] = self.env.cr.fetchone()
        name = "selection__ai_conversation__assistant__holder_value"
        self.env.cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id) VALUES (%s, %s, 'ir.model.fields.selection', %s)",
            (HOLDER, name, selection_id),
        )
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(HOLDER), {("ir.model.fields.selection", name): selection_id})

    def test_second_run_changes_nothing(self):
        self._hand_over_to_holder()
        pre_init_hook(self.env.cr)
        state_after_first_run = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_after_first_run)

    def test_names_this_module_holds_stay_with_the_holder(self):
        self.env.cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id, noupdate)"
            " SELECT %s, name, model, res_id, noupdate FROM ir_model_data WHERE module = %s",
            (HOLDER, MODULE),
        )
        state_before = self._database_state()
        self.assertIn(MODEL_RECORDS[1], state_before["external_ids"][HOLDER])

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_before)

    def test_a_module_that_depends_on_this_one_keeps_what_it_extends(self):
        extender = self.env["ir.module.module"].create({"name": EXTENDER})
        self.env["ir.module.module.dependency"].create({"module_id": extender.id, "name": MODULE})
        self._hand_over_to_holder()
        self.env.cr.execute(
            "INSERT INTO ir_model_data (module, name, model, res_id)"
            " SELECT %s, name, model, res_id FROM ir_model_data WHERE module = %s AND name IN %s",
            (EXTENDER, HOLDER, EXTENDED_NAMES),
        )
        extended_ids = self._external_ids(EXTENDER)
        self.assertEqual({name for _model, name in extended_ids}, set(EXTENDED_NAMES))

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(EXTENDER), extended_ids)
        self.assertEqual(self._external_ids(HOLDER), {})

    def test_before_install_a_holder_that_depends_on_this_one_hands_over(self):
        """The update that cuts this module out makes its holder depend on it before it is installed."""
        self.env["ir.module.module.dependency"].create({"module_id": self.holder.id, "name": MODULE})
        self.env.cr.execute("UPDATE ir_module_module SET state = 'to install' WHERE id = %s", (self.module.id,))
        external_ids = self._external_ids(MODULE)
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), external_ids)
        self.assertEqual(self._external_ids(HOLDER), {})

    def test_declared_selections_are_the_values_this_module_labels(self):
        selections = declared_selections(MODULE)
        self.assertIn(("ai.exchange.mixin", "provider", "claude"), selections)
        self.assertFalse(
            [value for model, field, value in selections if (model, field) == ("ai.conversation", "assistant")],
            "every assistant value is its assistant's",
        )


@tagged("post_install", "-at_install")
class TestNothingToAdopt(AdoptRecordsCase):
    def test_leaves_the_database_untouched(self):
        state_before = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_before)
