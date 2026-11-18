from odoo.tests.common import TransactionCase, tagged

from odoo.addons.project_team_dashboard.hooks import GLUE_MODULE, GLUE_RECORDS, MODEL_RECORDS, pre_init_hook

DASHBOARD_MODULE = "project_team_dashboard"
HOLDER = "team_dashboard_timeline_former_holder"
OTHER_MODULE = "team_dashboard_timeline_other_module"


@tagged("post_install", "-at_install")
class TestAdoptViews(TransactionCase):
    """The project_team_dashboard hook hands these views to this module, from the module that
    holds the models of the dashboard."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Not "installed": ir.module.module caches those past the rollback.
        cls.env["ir.module.module"].create([{"name": HOLDER}, {"name": OTHER_MODULE}])
        cls.views = cls.env["ir.ui.view"].browse(
            [cls.env.ref("%s.%s" % (GLUE_MODULE, name)).id for _model, name in GLUE_RECORDS]
        )
        cls.env.flush_all()

    def _external_ids(self, module_name):
        self.env.cr.execute("SELECT model, name, res_id FROM ir_model_data WHERE module = %s", (module_name,))
        return {(model, name): res_id for model, name, res_id in self.env.cr.fetchall()}

    def _view_files(self):
        self.env.cr.execute("SELECT id, arch_fs FROM ir_ui_view WHERE id IN %s", (tuple(self.views.ids),))
        return dict(self.env.cr.fetchall())

    def _hand_over(self, module_name, records, from_module):
        self.env.cr.execute(
            "UPDATE ir_model_data SET module = %s WHERE module = %s AND (model, name) IN %s",
            (module_name, from_module, records),
        )
        self.env.registry.clear_caches()

    def _hand_views_over(self, module_name):
        self._hand_over(module_name, GLUE_RECORDS, GLUE_MODULE)
        self.env.cr.execute(
            "UPDATE ir_ui_view SET arch_fs = %s || substr(arch_fs, %s) WHERE id IN %s",
            (module_name, len(GLUE_MODULE) + 1, tuple(self.views.ids)),
        )

    def test_takes_the_views_back_from_the_holder_of_the_dashboard(self):
        external_ids = self._external_ids(GLUE_MODULE)
        view_files = self._view_files()
        self._hand_over(HOLDER, MODEL_RECORDS, DASHBOARD_MODULE)
        self._hand_views_over(HOLDER)
        self.assertEqual(set(self._external_ids(HOLDER)), set(GLUE_RECORDS + MODEL_RECORDS))

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(GLUE_MODULE), external_ids)
        self.assertEqual(self._external_ids(HOLDER), {})
        self.assertEqual(self._view_files(), view_files)

    def test_leaves_views_of_the_same_names_in_a_module_that_holds_no_dashboard_model(self):
        self._hand_views_over(OTHER_MODULE)
        other_ids = self._external_ids(OTHER_MODULE)
        view_files = self._view_files()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(OTHER_MODULE), other_ids)
        self.assertEqual(self._view_files(), view_files)
        self.assertFalse(set(GLUE_RECORDS) & set(self._external_ids(GLUE_MODULE)))

    def test_takes_only_the_views_of_the_holder_of_the_dashboard(self):
        self._hand_over(HOLDER, MODEL_RECORDS, DASHBOARD_MODULE)
        self._hand_views_over(OTHER_MODULE)
        other_ids = self._external_ids(OTHER_MODULE)

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(OTHER_MODULE), other_ids)
        self.assertEqual(self._external_ids(HOLDER), {})

    def test_without_a_holder_leaves_the_database_untouched(self):
        external_ids = self._external_ids(GLUE_MODULE)
        view_files = self._view_files()

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(GLUE_MODULE), external_ids)
        self.assertEqual(self._view_files(), view_files)
