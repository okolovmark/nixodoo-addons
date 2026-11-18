from psycopg2 import sql

from odoo.tests.common import TransactionCase, tagged

from ..hooks import (
    ADOPTED_FOREIGN_KEYS,
    ADOPTED_MODELS,
    ADOPTED_RECORDS,
    MODEL_RECORDS,
    MODULE,
    OWNERSHIP_TABLES,
    declared_fields,
    pre_init_hook,
)

HOLDER = "team_dashboard_former_holder"
OTHER_MODULE = "team_dashboard_other_module"


class AdoptRecordsCase(TransactionCase):
    """The records of this module are first handed to another module, as a database holds them
    that had them from that module, and the hook is run to take them back."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Not "installed": ir.module.module caches those past the rollback.
        cls.module = cls.env["ir.module.module"].search([("name", "=", MODULE)])
        cls.holder, cls.other_module = cls.env["ir.module.module"].create([{"name": HOLDER}, {"name": OTHER_MODULE}])
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
            "external_ids": {name: self._external_ids(name) for name in (MODULE, HOLDER, OTHER_MODULE)},
            "owned_rows": {module.name: self._owned_rows(module) for module in self.module | self.holder},
            "view_files": self._view_files(self._adopted_view_ids()),
        }

    def _hand_over_to_holder(self):
        params = {
            "module": MODULE,
            "holder": HOLDER,
            "records": ADOPTED_RECORDS + MODEL_RECORDS,
            "fields": declared_fields(MODULE),
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
                {
                    "holder_id": self.holder.id,
                    "module_id": self.module.id,
                    "models": ADOPTED_MODELS,
                    "foreign_keys": ADOPTED_FOREIGN_KEYS,
                },
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
        self.assertFalse(self._owned_rows(self.module)["ir_model_constraint"])

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), external_ids)
        self.assertEqual(self._owned_rows(self.module), owned_rows)
        self.assertEqual(self._external_ids(HOLDER), {})
        self.assertEqual(self._owned_rows(self.holder), {table: {} for table in OWNERSHIP_TABLES})

    def test_registers_the_foreign_keys_of_its_fields_on_other_models(self):
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        foreign_keys = set(self._owned_rows(self.module)["ir_model_constraint"].values()) & set(ADOPTED_FOREIGN_KEYS)
        self.assertEqual(foreign_keys, set(ADOPTED_FOREIGN_KEYS))

    def test_views_are_read_from_the_files_of_this_module(self):
        view_files = self._view_files(self._adopted_view_ids())
        handed_view_ids = self._hand_over_to_holder()
        self.assertTrue(
            all(self._view_files(handed_view_ids)[view_id].startswith(HOLDER + "/") for view_id in handed_view_ids)
        )

        pre_init_hook(self.env.cr)

        self.assertEqual(self._view_files(view_files), view_files)

    def test_leaves_the_other_records_of_the_holder_alone(self):
        kept_field = self.env["ir.model.fields"]._get("project.task", "name")
        kept_view = self.env.ref("project.view_task_form2")
        kept_view_file = "%s/views/kept_views.xml" % HOLDER
        self.env["ir.model.data"].create(
            [
                {"module": HOLDER, "name": "field_kept", "model": "ir.model.fields", "res_id": kept_field.id},
                {"module": HOLDER, "name": "view_kept", "model": "ir.ui.view", "res_id": kept_view.id},
            ]
        )
        self.env.flush_all()
        self.env.cr.execute("UPDATE ir_ui_view SET arch_fs = %s WHERE id = %s", (kept_view_file, kept_view.id))
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertEqual(
            self._external_ids(HOLDER),
            {("ir.model.fields", "field_kept"): kept_field.id, ("ir.ui.view", "view_kept"): kept_view.id},
        )
        self.assertEqual(self._view_files([kept_view.id]), {kept_view.id: kept_view_file})

    def test_leaves_the_holder_its_own_field_on_a_model_of_this_module(self):
        holder_field = self.env["ir.model.fields"].create(
            {
                "model_id": self.env["ir.model"]._get_id("project.baseline"),
                "name": "holder_note",
                "field_description": "Holder Note",
                "ttype": "char",
                "state": "base",
                "store": False,
            }
        )
        self.env["ir.model.data"].create(
            {
                "module": HOLDER,
                "name": "field_project_baseline__holder_note",
                "model": "ir.model.fields",
                "res_id": holder_field.id,
            }
        )
        self.env.flush_all()
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertEqual(
            self._external_ids(HOLDER), {("ir.model.fields", "field_project_baseline__holder_note"): holder_field.id}
        )

    def test_leaves_the_field_of_another_module_that_defines_it_too(self):
        planned_start = self.env["ir.model.fields"]._get("project.task", "planned_date_start")
        self.env["ir.model.data"].create(
            {
                "module": OTHER_MODULE,
                "name": "field_project_task__planned_date_start",
                "model": "ir.model.fields",
                "res_id": planned_start.id,
            }
        )
        self.env.flush_all()
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertEqual(
            self._external_ids(OTHER_MODULE),
            {("ir.model.fields", "field_project_task__planned_date_start"): planned_start.id},
        )
        self.assertEqual(
            self._external_ids(MODULE)[("ir.model.fields", "field_project_task__planned_date_start")],
            planned_start.id,
        )

    def test_leaves_a_record_of_the_same_name_in_another_module(self):
        own_action = self.env["ir.actions.client"].create({"name": "Other Dashboard", "tag": "other_dashboard"})
        self.env["ir.model.data"].create(
            {
                "module": OTHER_MODULE,
                "name": "action_team_dashboard",
                "model": "ir.actions.client",
                "res_id": own_action.id,
            }
        )
        self.env.flush_all()
        dashboard_action_id = self._external_ids(MODULE)[("ir.actions.client", "action_team_dashboard")]
        self._hand_over_to_holder()

        pre_init_hook(self.env.cr)

        self.assertEqual(
            self._external_ids(OTHER_MODULE), {("ir.actions.client", "action_team_dashboard"): own_action.id}
        )
        self.assertEqual(
            self._external_ids(MODULE)[("ir.actions.client", "action_team_dashboard")], dashboard_action_id
        )

    def test_second_run_changes_nothing(self):
        self._hand_over_to_holder()
        pre_init_hook(self.env.cr)
        state_after_first_run = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_after_first_run)


@tagged("post_install", "-at_install")
class TestNothingToAdopt(AdoptRecordsCase):
    def test_leaves_a_record_of_the_same_name_held_only_by_another_module(self):
        other_action = self.env["ir.actions.client"].create({"name": "Other Dashboard", "tag": "other_dashboard"})
        self.env["ir.model.data"].create(
            {
                "module": OTHER_MODULE,
                "name": "action_team_dashboard",
                "model": "ir.actions.client",
                "res_id": other_action.id,
            }
        )
        self.env.flush_all()
        self.env.cr.execute(
            "DELETE FROM ir_model_data WHERE module = %s AND model = 'ir.actions.client' AND name = %s",
            (MODULE, "action_team_dashboard"),
        )
        self.env.registry.clear_caches()
        state_before = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_before)
        self.assertEqual(
            self._external_ids(OTHER_MODULE), {("ir.actions.client", "action_team_dashboard"): other_action.id}
        )

    def test_leaves_the_database_untouched(self):
        state_before = self._database_state()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._database_state(), state_before)
