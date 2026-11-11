from odoo.tests.common import TransactionCase, tagged

from ..hooks import ADOPTED_MODELS, LEGACY_MODULE, MODULE, SPLIT_VIEWS, pre_init_hook

EMPTY_ARCH = "<data/>"
EXTENDED_MODELS = ("project.project", "project.task")


@tagged("post_install", "-at_install")
class TestPreInitHook(TransactionCase):
    """The hook is run against this module's own records, handed to the legacy
    module name first: that is the state of a database the module was cut from.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.legacy_module = cls.env["ir.module.module"].search([("name", "=", LEGACY_MODULE)])
        if not cls.legacy_module:
            cls.legacy_module = cls.env["ir.module.module"].create({"name": LEGACY_MODULE, "state": "installed"})
        cls.module = cls.env["ir.module.module"].search([("name", "=", MODULE)])
        cls.split_views = cls.env["ir.ui.view"]
        for name in SPLIT_VIEWS:
            cls.split_views |= cls.env.ref("%s.%s" % (MODULE, name))

    def _external_ids(self, module):
        self.env.cr.execute("SELECT model, name, res_id FROM ir_model_data WHERE module = %s", (module,))
        return {(model, name): res_id for model, name, res_id in self.env.cr.fetchall()}

    def _constraints(self, module):
        self.env.cr.execute("SELECT name FROM ir_model_constraint WHERE module = %s", (module.id,))
        return {name for (name,) in self.env.cr.fetchall()}

    def _hand_over_to_legacy_module(self):
        """Every record of this module was cut from the legacy one, so all of
        them change hands. A record added here later has to be left out.
        """
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
                   AND imd.res_id NOT IN (SELECT id FROM ir_model_fields WHERE model IN %(field_models)s)
               )
            """,
            {
                "legacy": LEGACY_MODULE,
                "module": MODULE,
                "models": ADOPTED_MODELS,
                "field_models": ADOPTED_MODELS + EXTENDED_MODELS,
            },
        )
        self.env.cr.execute(
            "UPDATE ir_model_constraint SET module = %s WHERE module = %s",
            (self.legacy_module.id, self.module.id),
        )
        self.env.registry.clear_caches()

    def _set_legacy_module_state(self, state):
        self.env.cr.execute("UPDATE ir_module_module SET state = %s WHERE id = %s", (state, self.legacy_module.id))

    def test_takes_over_every_record_under_its_database_id(self):
        owned_external_ids = self._external_ids(MODULE)
        owned_constraints = self._constraints(self.module)
        legacy_external_ids = self._external_ids(LEGACY_MODULE)
        self._hand_over_to_legacy_module()
        self.assertEqual(
            {(model, name) for model, name in self._external_ids(MODULE) if model != "ir.model.fields"},
            {("ir.model", "model_project_project"), ("ir.model", "model_project_task")},
            "every record changed hands but the two models this module only extends",
        )

        pre_init_hook(self.env.cr)

        self.assertEqual(self._external_ids(MODULE), owned_external_ids)
        self.assertEqual(self._constraints(self.module), owned_constraints)
        self.assertEqual(self._external_ids(LEGACY_MODULE), legacy_external_ids)

    def test_followers_of_the_checklist_subtype_stay_subscribed(self):
        subtype = self.env.ref("%s.subtasks_subtype" % MODULE)
        project = self.env["project.project"].create({"name": "Followed Project"})
        task = self.env["project.task"].create({"name": "Followed Task", "project_id": project.id})
        partner = self.env["res.partner"].create({"name": "Checklist Follower"})
        task.message_subscribe(partner_ids=partner.ids, subtype_ids=subtype.ids)
        self._hand_over_to_legacy_module()

        pre_init_hook(self.env.cr)

        self.assertEqual(self.env.ref("%s.subtasks_subtype" % MODULE), subtype)
        follower = task.message_follower_ids.filtered(lambda follower: follower.partner_id == partner)
        self.assertEqual(follower.subtype_ids, subtype)

    def test_split_views_are_taken_over_empty(self):
        whole_view = self.env.ref("%s.view_task_form2" % MODULE)
        whole_arch = whole_view.arch_db
        self._hand_over_to_legacy_module()

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self.split_views.mapped("arch_db"), [EMPTY_ARCH] * len(SPLIT_VIEWS))
        self.assertEqual(whole_view.arch_db, whole_arch)

    def test_second_run_changes_nothing(self):
        split_archs = self.split_views.mapped("arch_db")
        self._hand_over_to_legacy_module()
        pre_init_hook(self.env.cr)
        self.env.invalidate_all()
        for view, arch in zip(self.split_views, split_archs):
            view.arch_db = arch
        self.env.flush_all()
        owned_external_ids = self._external_ids(MODULE)

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self._external_ids(MODULE), owned_external_ids)
        self.assertEqual(self.split_views.mapped("arch_db"), split_archs)

    def test_does_nothing_without_the_legacy_module(self):
        self._hand_over_to_legacy_module()
        handed_over_external_ids = self._external_ids(LEGACY_MODULE)
        self._set_legacy_module_state("uninstalled")

        pre_init_hook(self.env.cr)

        self.env.invalidate_all()
        self.assertEqual(self._external_ids(LEGACY_MODULE), handed_over_external_ids)
        self.assertNotIn(EMPTY_ARCH, self.split_views.mapped("arch_db"))

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
        self._hand_over_to_legacy_module()

        pre_init_hook(self.env.cr)

        legacy_external_ids = self._external_ids(LEGACY_MODULE)
        self.assertEqual(legacy_external_ids.get(("ir.model.fields", "field_kept")), kept_field.id)
        self.assertEqual(legacy_external_ids.get(("ir.ui.view", "view_kept")), kept_view.id)
