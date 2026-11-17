from pathlib import Path
from unittest.mock import patch

from psycopg2.extras import Json

from odoo.exceptions import ValidationError
from odoo.modules.module import load_script
from odoo.tests import TransactionCase
from odoo.tools import config

from .common import MODULE

BLANKING_SCRIPT = Path(__file__).parents[1] / "migrations" / "20.0.2.8.1" / "pre-migration.py"
EARLIER_VERSION = "20.0.2.4.1"
BLANK_ARCH = {"en_US": "<data/>"}
# What 20.0.2.4.1 stored: fields this version no longer has.
STALE_FORM_ARCH = {
    "en_US": '<xpath expr="//page[@name=\'checklist\']" position="after"><page string="Technical Solution"><field name="questions"/><field name="answers"/></page></xpath>',
    "fr_FR": '<xpath expr="//page[@name=\'checklist\']" position="after"><page string="Solution technique"><field name="questions"/><field name="answers"/></page></xpath>',
}
STALE_CARD_ARCH = {
    "en_US": '<templates position="before"><field name="kanban_subtasks"/></templates>',
}
STALE_CHECKLIST_ARCH = {
    "en_US": '<xpath expr="//page[@name=\'description_page\']" position="after"><page name="checklist" string="Checklist"><field name="subtask_ids"/></page></xpath>',
}
# The card view of the checklist under the xml id of 20.0.2.4.1, which no data file names any more.
FORMER_CARD_VIEW = "view_task_kanban_subtasks"
KEPT_VIEWS = (f"{MODULE}.project_task_checklist_item_view_form", "project.view_task_partner_info_form")


class TestBlankExtensionViewsMigration(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.script = load_script(str(BLANKING_SCRIPT), "pre_migration")
        cls.form_view = cls.env.ref(f"{MODULE}.view_task_form2_common")
        cls.former_card_view = cls.env["ir.ui.view"].create(
            {
                "name": "project.task.kanban.subtasks",
                "model": "project.task",
                "inherit_id": cls.env.ref("project.view_task_card").id,
                "arch": '<templates position="before"><field name="name"/></templates>',
            },
        )
        cls.env["ir.model.data"].create(
            {"module": MODULE, "name": FORMER_CARD_VIEW, "model": "ir.ui.view", "res_id": cls.former_card_view.id},
        )
        cls._store_arch(cls.form_view, STALE_FORM_ARCH)
        cls._store_arch(cls.former_card_view, STALE_CARD_ARCH)

    @classmethod
    def _store_arch(cls, view, arch):
        cls.env.flush_all()
        cls.env.cr.execute("UPDATE ir_ui_view SET arch_db = %s WHERE id = %s", [Json(arch), view.id])
        cls.env.invalidate_all()

    def _stored_arch(self, view):
        self.env.cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", [view.id])
        return self.env.cr.fetchone()[0]

    def _migrate(self, version):
        self.env.flush_all()
        self.script.migrate(self.env.cr, version)
        self.env.invalidate_all()

    def test_update_blanks_the_extension_views_of_this_module(self):
        self._migrate(EARLIER_VERSION)

        self.assertEqual(self._stored_arch(self.form_view), BLANK_ARCH)
        self.assertEqual(self._stored_arch(self.former_card_view), BLANK_ARCH)

    def test_a_view_the_update_rewrites_validates_once_the_stale_ones_are_blanked(self):
        """The update rewrites the checklist view first, while the form view
        still names the fields questions and answers.
        """
        checklist_view = self.env.ref(f"{MODULE}.view_task_form2")
        self._store_arch(checklist_view, STALE_CHECKLIST_ARCH)
        # the update reads the stored archs, where --dev=xml reads the files
        with patch.dict(config.options, {"dev_mode": []}):
            with self.assertRaisesRegex(ValidationError, "questions"):
                checklist_view.reset_arch(mode="hard")

            self._migrate(EARLIER_VERSION)
            checklist_view.reset_arch(mode="hard")

            self.env.ref("project.view_task_form2")._check_xml()
        self.assertIn("checklist_item_ids", self._stored_arch(checklist_view)["en_US"])

    def test_update_leaves_primary_views_and_views_of_other_modules_alone(self):
        kept_views = {xml_id: self.env.ref(xml_id) for xml_id in KEPT_VIEWS}
        self.assertEqual([view.mode for view in kept_views.values()], ["primary", "extension"])
        archs_before = {xml_id: self._stored_arch(view) for xml_id, view in kept_views.items()}

        self._migrate(EARLIER_VERSION)

        self.assertEqual({xml_id: self._stored_arch(view) for xml_id, view in kept_views.items()}, archs_before)

    def test_fresh_install_changes_nothing(self):
        for version in (None, False, ""):
            with self.subTest(version=version):
                self._migrate(version)

                self.assertEqual(self._stored_arch(self.form_view), STALE_FORM_ARCH)
                self.assertEqual(self._stored_arch(self.former_card_view), STALE_CARD_ARCH)

    def test_logs_the_number_of_views_blanked(self):
        view_xml_ids = self.env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "ir.ui.view")])
        module_views = self.env["ir.ui.view"].browse(view_xml_ids.mapped("res_id"))
        extension_views = module_views.filtered(lambda view: view.mode == "extension")

        with self.assertLogs(self.script._logger, "INFO") as logs:
            self._migrate(EARLIER_VERSION)

        self.assertEqual(
            logs.output,
            [f"INFO:pre_migration:Extension views blanked until the data files rewrite them: {len(extension_views)}"],
        )
