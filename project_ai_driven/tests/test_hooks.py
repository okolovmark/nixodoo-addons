import importlib.util
import logging
from pathlib import Path

from lxml import etree
from psycopg2 import sql
from psycopg2.extras import Json

from odoo import Command
from odoo.exceptions import MissingError, ValidationError
from odoo.tests.common import TransactionCase, tagged
from odoo.tools import convert_file
from odoo.tools.sql import table_exists

from ..hooks import (
    CHECKLIST_MODEL,
    CHECKLIST_TABLE,
    CHECKLIST_VIEW_FILE,
    DROPPED_CHECKLIST_FIELD,
    DROPPED_FOREIGN_KEYS,
    DROPPED_TEST_CASE_BUTTONS,
    FIELD_NAMING_COLUMNS,
    FORMER_CHECKLIST_MODEL,
    FORMER_CHECKLIST_TABLE,
    FORMER_CHECKLIST_VIEW_FILE,
    MODULE,
    RENAMED_CHECKLIST_EXTERNAL_IDS,
    RENAMED_CHECKLIST_FIELDS,
    _model_name_columns,
    _reference_columns,
    _remove_sub_view_nodes,
    _rewrite_module_views,
    forget_checklist_activities,
    forget_dropped_fields,
    forget_dropped_test_case_buttons,
    rename_checklist,
    rename_external_ids,
)
from odoo.addons.base.models.ir_model import selection_xmlid

# The nodes that named the fields in DROPPED_FIELDS in the task form views that had them, the
# cases' sub-view included, cut down to one arch; the page title is its translated term.
OLD_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="%s"><group><field name="tester_id"/></group>'
    '<field name="testcase_ids"><form><field name="name"/><field name="passed"/></form></field></page>'
    "</xpath>"
    """<xpath expr="//field[@name='tag_ids']" position="before">"""
    '<field name="difficulty"/><field name="knowledge_area_level"/><field name="task_type"/>'
    "</xpath>"
    "</data>"
)
KEPT_FIELDS = ["testcase_ids", "name", "task_type"]
# The case form before its passed flag was deleted; the group title is its translated term.
OLD_CASE_FORM_ARCH = '<form><group string="%s"><field name="state"/><field name="passed"/></group></form>'
OLD_CASE_FORM_ARCHS = {"en_US": OLD_CASE_FORM_ARCH % "Result", "fr_FR": OLD_CASE_FORM_ARCH % "Résultat"}
KEPT_CASE_FORM_FIELDS = ["state"]
PASSED_SUB_VIEW_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="Test Cases"><field name="testcase_ids"><form><field name="passed"/></form></field></page>'
    "</xpath>"
    "</data>"
)
# The passed flag in the sub-view of another relation, whose model never had it.
OTHER_SUB_VIEW_ARCH = (
    "<data>"
    '<xpath expr="//page[1]" position="before">'
    '<page string="Checklist"><field name="checklist_item_ids"><tree><field name="passed"/></tree></field></page>'
    "</xpath>"
    "</data>"
)
# The Technical Solution page before its questions and answers became rows; the page title is its translated term.
OLD_QUESTIONS_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="%s"><group><field name="questions"/><field name="answers"/><field name="solution"/></group></page>'
    "</xpath>"
    "</data>"
)
OLD_QUESTIONS_ARCHS = {
    "en_US": OLD_QUESTIONS_ARCH % "Technical Solution",
    "fr_FR": OLD_QUESTIONS_ARCH % "Solution technique",
}
KEPT_QUESTION_PAGE_FIELDS = ["solution"]
SUB_VIEW_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="Test Cases"><field name="testcase_ids"><tree><field name="tester_id"/></tree></field></page>'
    "</xpath>"
    "</data>"
)
OLD_BUTTONS = "".join(
    '<button name="%s" type="object" icon="fa-check" title="Change state"/>' % name
    for name in DROPPED_TEST_CASE_BUTTONS
)
# The buttons in DROPPED_TEST_CASE_BUTTONS in the cases' sub-view of a task form view, next to the checklist
# sub-view with buttons of the same names; the page titles are its translated terms.
OLD_BUTTONS_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<page string="%(cases)s"><field name="testcase_ids"><tree><field name="name"/>'
    + OLD_BUTTONS
    + '<button name="action_set_passed" type="object" icon="fa-check" title="Passed"/>'
    "</tree></field></page>"
    "</xpath>"
    '<xpath expr="//page[1]" position="before">'
    '<page string="%(checklist)s"><field name="checklist_item_ids"><tree><field name="name"/>'
    + OLD_BUTTONS
    + "</tree></field></page>"
    "</xpath>"
    "</data>"
)
OLD_BUTTONS_ARCHS = {
    "en_US": OLD_BUTTONS_ARCH % {"cases": "Test Cases", "checklist": "Checklist"},
    "fr_FR": OLD_BUTTONS_ARCH % {"cases": "Cas de test", "checklist": "Liste de contrôle"},
}
KEPT_BUTTONS = [("testcase_ids", "action_set_passed")] + [
    ("checklist_item_ids", name) for name in DROPPED_TEST_CASE_BUTTONS
]
BUTTONS_MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.5.0" / "pre-migration.py"
CHECKLIST_MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.6.0" / "pre-migration.py"
ACTIVITIES_MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.6.1" / "pre-migration.py"
QUESTIONS_MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.7.0" / "pre-migration.py"
PASSED_MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.15.0" / "pre-migration.py"
CHECKLIST_LIST_NODE = '<field name="checklist_item_ids">'
# How the checklist page of the task form named its list, and the default assignee of a new line.
FORMER_CHECKLIST_LIST_NODE = '<field name="subtask_ids">'
FORMER_CHECKLIST_NODES = (
    '<field name="default_user" invisible="True"/>'
    '<field name="subtask_ids" context="{\'default_user_id\': default_user}">'
)
NESTED_BUTTONS_ARCH = (
    "<data>"
    '<xpath expr="//page[2]" position="after">'
    '<field name="testcase_ids"><form>'
    '<button name="change_state_done" type="object"/>'
    '<field name="attachment_ids"><tree><button name="change_state_todo" type="object"/></tree></field>'
    "</form></field>"
    "</xpath>"
    "</data>"
)
# A record of each, naming the checklist model.
MODEL_NAME_RECORDS = {
    "mail.message": ("mail_message", "model"),
    "mail.followers": ("mail_followers", "res_model"),
    "mail.activity": ("mail_activity", "res_model"),
    "ir.attachment": ("ir_attachment", "res_model"),
    "ir.filters": ("ir_filters", "model_id"),
    "ir.actions.act_window": ("ir_act_window", "res_model"),
}
# Written out, not taken from the hooks: _put_back_former_checklist_names uses the functions under test.
REQUIRED_MODEL_NAME_COLUMNS = (
    ("ir_model_data", "model"),
    ("ir_ui_view", "model"),
    ("ir_act_window", "res_model"),
    ("ir_filters", "model_id"),
    ("mail_message", "model"),
    ("mail_followers", "res_model"),
    ("mail_activity", "res_model"),
    ("ir_attachment", "res_model"),
)
REFERENCE_COLUMN = ("ir_ui_menu", "action")
SUBTYPE_DATA_FILE = "data/mail_message_subtype_data.xml"
HOOKS_LOGGER = "odoo.addons.%s.hooks" % MODULE
OTHER_MODULE = "other_module"
# The activity fields of the checklist model as a database from before 16.0.2.6.1 holds them,
# cut down to three: {name: (type, the modules holding an external id of the field)}.
ACTIVITY_FIELDS = {
    "activity_state": ("selection", (MODULE,)),
    "activity_calendar_event_id": ("many2one", (MODULE, OTHER_MODULE)),
    "my_activity_date_deadline": ("date", (OTHER_MODULE,)),
}
ACTIVITY_STATES = ("overdue", "today", "planned")
DELEGATING_MODULE = "delegating_module"
DELEGATING_MODEL = "res.partner"
REPLACED_MODULE = "replaced_module"
BYSTANDER_MODULE = "bystander_" + REPLACED_MODULE
DATABASE_STATE_QUERIES = {
    "external ids": "SELECT id, module, name, model, res_id FROM ir_model_data ORDER BY id",
    "constraints": "SELECT id, module, name FROM ir_model_constraint ORDER BY id",
    "relations": "SELECT id, module, name FROM ir_model_relation ORDER BY id",
    "models": "SELECT id, model FROM ir_model ORDER BY id",
    "fields": "SELECT id, model, name, relation, related, depends FROM ir_model_fields ORDER BY id",
    "views": "SELECT id, model, arch_fs, arch_db::text FROM ir_ui_view ORDER BY id",
    "rules": "SELECT id, domain_force FROM ir_rule ORDER BY id",
    "filters": "SELECT id, model_id, domain, context, sort FROM ir_filters ORDER BY id",
    "window actions": "SELECT id, res_model, domain, context FROM ir_act_window ORDER BY id",
    "checklist tables": """
        SELECT relname, relkind
          FROM pg_class
         WHERE starts_with(relname, 'project_task_subtask') OR starts_with(relname, 'project_task_checklist_item')
      ORDER BY relname
    """,
    "checklist constraints": """
        SELECT conname
          FROM pg_constraint
         WHERE conrelid IN (to_regclass('project_task_subtask'), to_regclass('project_task_checklist_item'))
      ORDER BY conname
    """,
}


def _sub_view_buttons(arch):
    """The buttons of arch, as (field of their sub-view, name)."""
    return [
        (node.xpath("ancestor::field[1]/@name")[0], node.get("name")) for node in etree.fromstring(arch).iter("button")
    ]


class PreMigrationCase(TransactionCase):
    """The functions the pre-migrations run are run against this module's own
    records, put back first in the state of a database from before the update.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.module = cls.env["ir.module.module"].search([("name", "=", MODULE)])

    @classmethod
    def _get_module(cls, name):
        module = cls.env["ir.module.module"].search([("name", "=", name)])
        return module or cls.env["ir.module.module"].create({"name": name})

    @classmethod
    def _external_ids(cls, module_name):
        cls.env.cr.execute("SELECT model, name, res_id FROM ir_model_data WHERE module = %s", (module_name,))
        return {(model, name): res_id for model, name, res_id in cls.env.cr.fetchall()}

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
        database from before the fields in DROPPED_FIELDS or the buttons in
        DROPPED_TEST_CASE_BUTTONS were deleted has it.
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

    def _stored_buttons(self, view):
        return {lang: _sub_view_buttons(arch) for lang, arch in self._stored_archs(view).items()}

    def _register_foreign_keys(self, module):
        task_model_id = self.env["ir.model"]._get_id("project.task")
        registrations = self.env["ir.model.constraint"].create(
            [{"name": name, "model": task_model_id, "module": module.id, "type": "f"} for name in DROPPED_FOREIGN_KEYS],
        )
        self.env.flush_all()
        return registrations

    def _put_back_former_checklist_names(self):
        """Undo by SQL what rename_checklist renames, and give the checklist
        page of the task form the node and the context of the dropped field in
        every translation. No checklist item or field of the checklist may be
        read or flushed until rename_checklist has run.
        """
        self.env.flush_all()
        cr = self.env.cr
        form_view_id = self.env.ref("%s.view_task_form2" % MODULE).id
        self._rename_table_objects(CHECKLIST_TABLE, FORMER_CHECKLIST_TABLE)
        params = {
            "model": CHECKLIST_MODEL,
            "former_model": FORMER_CHECKLIST_MODEL,
            "prefix": CHECKLIST_TABLE + "_",
            "former_prefix": FORMER_CHECKLIST_TABLE + "_",
            "external_id_pattern": r"^(model_|field_|selection__)%s(__|$)" % CHECKLIST_TABLE,
            "former_external_id": r"\1%s\2" % FORMER_CHECKLIST_TABLE,
            "file": CHECKLIST_VIEW_FILE,
            "former_file": FORMER_CHECKLIST_VIEW_FILE,
        }
        for query in (
            """
            UPDATE ir_model_constraint con
               SET name = %(former_prefix)s || substr(con.name, length(%(prefix)s) + 1)
              FROM ir_model
             WHERE ir_model.id = con.model AND ir_model.model = %(model)s AND starts_with(con.name, %(prefix)s)
            """,
            "UPDATE ir_model SET model = %(former_model)s WHERE model = %(model)s",
            "UPDATE ir_model_fields SET model = %(former_model)s WHERE model = %(model)s",
            "UPDATE ir_model_fields SET relation = %(former_model)s WHERE relation = %(model)s",
            """
            UPDATE ir_model_data
               SET name = regexp_replace(name, %(external_id_pattern)s, %(former_external_id)s)
             WHERE model IN ('ir.model', 'ir.model.fields', 'ir.model.fields.selection')
               AND name ~ %(external_id_pattern)s
            """,
            "UPDATE ir_ui_view SET arch_fs = %(former_file)s WHERE arch_fs = %(file)s",
        ):
            cr.execute(query, params)
        for column_query, columns in (
            (
                "UPDATE {table} SET {column} = %(former_model)s WHERE {column} = %(model)s",
                _model_name_columns(self.env),
            ),
            (
                "UPDATE {table} SET {column} = %(former_model)s || substr({column}, length(%(model)s) + 1)"
                " WHERE starts_with({column}, %(model)s || ',')",
                _reference_columns(self.env),
            ),
        ):
            for table, column in columns:
                cr.execute(
                    sql.SQL(column_query).format(table=sql.Identifier(table), column=sql.Identifier(column)), params
                )
        for former_field, field in RENAMED_CHECKLIST_FIELDS.items():
            field_params = {"field": field, "former_field": former_field}
            for query in (
                """
                UPDATE ir_model_fields
                   SET name = %(former_field)s,
                       related = array_to_string(
                           array_replace(string_to_array(related, '.'), %(field)s::text, %(former_field)s::text), '.'
                       )
                 WHERE name = %(field)s
                """,
                """
                UPDATE ir_model_fields
                   SET depends = replace(depends, %(field)s, %(former_field)s)
                 WHERE strpos(depends, %(field)s) > 0
                """,
                """
                UPDATE ir_model_data imd
                   SET name = 'field_' || replace(field.model, '.', '_') || '__' || %(former_field)s
                  FROM ir_model_fields field
                 WHERE imd.model = 'ir.model.fields'
                   AND imd.res_id = field.id
                   AND field.name = %(former_field)s
                   AND imd.name = 'field_' || replace(field.model, '.', '_') || '__' || %(field)s
                """,
                """
                UPDATE ir_ui_view
                   SET arch_db = replace(arch_db::text, %(field)s, %(former_field)s)::jsonb
                 WHERE strpos(arch_db::text, %(field)s) > 0
                """,
            ):
                cr.execute(query, field_params)
        for former_name, new_name in RENAMED_CHECKLIST_EXTERNAL_IDS.items():
            cr.execute(
                "UPDATE ir_model_data SET name = %s WHERE module = %s AND name = %s", (former_name, MODULE, new_name)
            )
        texts = {**RENAMED_CHECKLIST_FIELDS, FORMER_CHECKLIST_MODEL: CHECKLIST_MODEL}
        for table, column in FIELD_NAMING_COLUMNS:
            for former_text, text in texts.items():
                cr.execute(
                    sql.SQL("UPDATE {table} SET {column} = replace({column}, %s, %s)").format(
                        table=sql.Identifier(table), column=sql.Identifier(column)
                    ),
                    (text, former_text),
                )
        cr.execute("SELECT arch_db FROM ir_ui_view WHERE id = %s", (form_view_id,))
        former_archs = {}
        for lang, arch in cr.fetchone()[0].items():
            self.assertIn(FORMER_CHECKLIST_LIST_NODE, arch, lang)
            former_archs[lang] = arch.replace(FORMER_CHECKLIST_LIST_NODE, FORMER_CHECKLIST_NODES)
        cr.execute("UPDATE ir_ui_view SET arch_db = %s WHERE id = %s", (Json(former_archs), form_view_id))
        self.env.invalidate_all()
        self.env.registry.clear_caches()

    def _rename_table_objects(self, table, former_table):
        """Rename table, its sequence, and the constraints and indexes named after it, to former_table."""
        cr = self.env.cr
        cr.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(sql.Identifier(table), sql.Identifier(former_table)))
        cr.execute(
            sql.SQL("ALTER SEQUENCE {} RENAME TO {}").format(
                sql.Identifier(table + "_id_seq"), sql.Identifier(former_table + "_id_seq")
            )
        )
        for name in self._table_objects(former_table)["constraints"]:
            if name.startswith(table + "_"):
                cr.execute(
                    sql.SQL("ALTER TABLE {} RENAME CONSTRAINT {} TO {}").format(
                        sql.Identifier(former_table),
                        sql.Identifier(name),
                        sql.Identifier(former_table + name[len(table) :]),
                    )
                )
        for name in self._table_objects(former_table)["indexes"]:
            if name.startswith(table + "_"):
                cr.execute(
                    sql.SQL("ALTER INDEX {} RENAME TO {}").format(
                        sql.Identifier(name), sql.Identifier(former_table + name[len(table) :])
                    )
                )

    def _table_objects(self, table):
        """The sequence, constraints and indexes of table, by kind."""
        cr = self.env.cr
        cr.execute("SELECT pg_get_serial_sequence(%s, 'id')", (table,))
        objects = {"sequence": cr.fetchone()[0]}
        cr.execute("SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(%s) ORDER BY conname", (table,))
        objects["constraints"] = [name for (name,) in cr.fetchall()]
        cr.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema() AND tablename = %s ORDER BY indexname",
            (table,),
        )
        objects["indexes"] = [name for (name,) in cr.fetchall()]
        return objects


@tagged("post_install", "-at_install")
class TestForgetDroppedFields(PreMigrationCase):
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

    def test_takes_the_questions_and_answers_out_of_every_translation_of_a_view(self):
        view = self.env.ref("%s.view_task_form2_common" % MODULE)
        self._store_arch(view, **OLD_QUESTIONS_ARCHS)

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_field_names(view), dict.fromkeys(OLD_QUESTIONS_ARCHS, KEPT_QUESTION_PAGE_FIELDS))
        self.assertIn('string="Solution technique"', self._stored_archs(view)["fr_FR"])

    def test_migration_script_forgets_the_questions_and_answers_on_update_only(self):
        spec = importlib.util.spec_from_file_location("pre_migration", QUESTIONS_MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        view = self.env.ref("%s.view_task_form2_common" % MODULE)
        self._store_arch(view, **OLD_QUESTIONS_ARCHS)

        script.migrate(self.env.cr, None)
        self.assertEqual(self._stored_archs(view), OLD_QUESTIONS_ARCHS)

        script.migrate(self.env.cr, "16.0.2.6.1")
        self.assertEqual(self._stored_field_names(view), dict.fromkeys(OLD_QUESTIONS_ARCHS, KEPT_QUESTION_PAGE_FIELDS))

    def test_the_passed_flag_of_the_cases_sub_view_no_longer_blocks_the_views_of_the_task_form(self):
        other_view = self.env.ref("%s.view_task_form2" % MODULE)
        self._store_arch(self.view, en_US=PASSED_SUB_VIEW_ARCH)
        with self.assertRaises(ValidationError) as refusal:
            other_view.write({"arch": other_view.arch})
        self.assertIn('Field "passed" does not exist', refusal.exception.args[0])

        forget_dropped_fields(self.env.cr)

        self.env.invalidate_all()
        other_view.write({"arch": other_view.arch})
        self.assertEqual(self._stored_field_names(self.view), {"en_US": ["testcase_ids"]})

    def test_takes_the_passed_flag_out_of_every_translation_of_the_case_form(self):
        view = self.env.ref("%s.project_task_testcase_view_form" % MODULE)
        self._store_arch(view, **OLD_CASE_FORM_ARCHS)

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_field_names(view), dict.fromkeys(OLD_CASE_FORM_ARCHS, KEPT_CASE_FORM_FIELDS))
        self.assertIn('string="Résultat"', self._stored_archs(view)["fr_FR"])

    def test_migration_script_forgets_the_passed_flag_on_update_only(self):
        spec = importlib.util.spec_from_file_location("pre_migration", PASSED_MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        case_form = self.env.ref("%s.project_task_testcase_view_form" % MODULE)
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases")
        self._store_arch(case_form, **OLD_CASE_FORM_ARCHS)

        script.migrate(self.env.cr, None)
        self.assertEqual(self._stored_archs(self.view), {"en_US": OLD_ARCH % "Test Cases"})
        self.assertEqual(self._stored_archs(case_form), OLD_CASE_FORM_ARCHS)

        script.migrate(self.env.cr, "16.0.2.14.0")
        self.assertEqual(self._stored_field_names(self.view), {"en_US": KEPT_FIELDS})
        self.assertEqual(self._stored_field_names(case_form), dict.fromkeys(OLD_CASE_FORM_ARCHS, KEPT_CASE_FORM_FIELDS))

    def test_leaves_the_field_of_a_sub_view_alone(self):
        self._store_arch(self.view, en_US=SUB_VIEW_ARCH)

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_archs(self.view), {"en_US": SUB_VIEW_ARCH})

    def test_leaves_the_passed_flag_of_the_sub_view_of_another_relation_alone(self):
        self._store_arch(self.view, en_US=OTHER_SUB_VIEW_ARCH)

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_archs(self.view), {"en_US": OTHER_SUB_VIEW_ARCH})

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

    def test_a_view_given_to_this_module_by_a_pending_write_is_cleared_and_read_back(self):
        self.env.cr.execute(
            "UPDATE ir_model_data SET module = %s WHERE model = 'ir.ui.view' AND res_id = %s",
            (OTHER_MODULE, self.view.id),
        )
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases")
        self.assertIn('name="difficulty"', self.view.arch_db)
        external_id = self.env["ir.model.data"].search([("model", "=", "ir.ui.view"), ("res_id", "=", self.view.id)])
        external_id.module = MODULE

        forget_dropped_fields(self.env.cr)

        self.assertEqual(self._stored_field_names(self.view), {"en_US": KEPT_FIELDS})
        self.assertNotIn('name="difficulty"', self.view.arch_db)

    def test_a_registration_given_to_this_module_by_a_pending_write_is_deleted_and_read_back(self):
        registrations = self._register_foreign_keys(self._get_module(OTHER_MODULE))
        self.assertTrue(registrations[0].name)
        registrations.module = self.module

        forget_dropped_fields(self.env.cr)

        self.assertFalse(registrations.exists())
        with self.assertRaises(MissingError):
            registrations[0].name  # noqa: B018

    def test_second_run_changes_nothing(self):
        self._store_arch(self.view, en_US=OLD_ARCH % "Test Cases")
        self._register_foreign_keys(self.module)
        forget_dropped_fields(self.env.cr)
        state_after_first_run = self._database_state()

        forget_dropped_fields(self.env.cr)

        self.assertDatabaseState(state_after_first_run)


@tagged("post_install", "-at_install")
class TestForgetDroppedTestCaseButtons(PreMigrationCase):
    """forget_dropped_test_case_buttons on a database that already holds
    everything under this module, as its update runs it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.view = cls.env.ref("%s.view_task_form2_testcase" % MODULE)

    def test_a_view_that_names_them_no_longer_blocks_the_views_of_its_form(self):
        other_view = self.env.ref("%s.view_task_form2" % MODULE)
        self._store_arch(self.view, en_US=OLD_BUTTONS_ARCHS["en_US"])
        with self.assertRaises(ValidationError) as refusal:
            other_view.write({"arch": other_view.arch})
        self.assertIn("change_state_done is not a valid action on project.task.testcase", refusal.exception.args[0])

        forget_dropped_test_case_buttons(self.env.cr)

        self.env.invalidate_all()
        other_view.write({"arch": other_view.arch})

    def test_takes_them_out_of_the_cases_sub_view_in_every_translation(self):
        checklist_view = self.env.ref("%s.view_task_form2" % MODULE)
        checklist_archs = self._stored_archs(checklist_view)
        self.assertIn('name="change_state_done"', checklist_archs["en_US"])
        self._store_arch(self.view, **OLD_BUTTONS_ARCHS)

        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            forget_dropped_test_case_buttons(self.env.cr)

        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Dropped test case buttons forgotten: taken out of 1 views"],
        )
        self.assertEqual(self._stored_buttons(self.view), dict.fromkeys(OLD_BUTTONS_ARCHS, KEPT_BUTTONS))
        cleared_archs = self._stored_archs(self.view)
        self.assertIn('string="Cas de test"', cleared_archs["fr_FR"])
        self.assertIn('string="Liste de contrôle"', cleared_archs["fr_FR"])
        self.assertEqual(self._stored_archs(checklist_view), checklist_archs)

    def test_leaves_the_views_of_other_modules_alone(self):
        self.env.cr.execute(
            "UPDATE ir_model_data SET module = %s WHERE model = 'ir.ui.view' AND res_id = %s",
            (OTHER_MODULE, self.view.id),
        )
        self._store_arch(self.view, **OLD_BUTTONS_ARCHS)

        forget_dropped_test_case_buttons(self.env.cr)

        self.assertEqual(self._stored_archs(self.view), OLD_BUTTONS_ARCHS)

    def test_a_button_belongs_to_its_nearest_field(self):
        self.assertEqual(
            _sub_view_buttons(NESTED_BUTTONS_ARCH),
            [("testcase_ids", "change_state_done"), ("attachment_ids", "change_state_todo")],
        )

        cleared_arch = _remove_sub_view_nodes(NESTED_BUTTONS_ARCH, "button", "testcase_ids", DROPPED_TEST_CASE_BUTTONS)

        self.assertEqual(_sub_view_buttons(cleared_arch), [("attachment_ids", "change_state_todo")])

    def test_second_run_changes_nothing(self):
        self._store_arch(self.view, **OLD_BUTTONS_ARCHS)
        forget_dropped_test_case_buttons(self.env.cr)
        state_after_first_run = self._database_state()

        with self.assertNoLogs(HOOKS_LOGGER, level=logging.INFO):
            forget_dropped_test_case_buttons(self.env.cr)

        self.assertDatabaseState(state_after_first_run)

    def test_migration_script_forgets_them_on_update_only(self):
        spec = importlib.util.spec_from_file_location("pre_migration", BUTTONS_MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        self._store_arch(self.view, **OLD_BUTTONS_ARCHS)

        script.migrate(self.env.cr, None)
        self.assertEqual(self._stored_archs(self.view), OLD_BUTTONS_ARCHS)

        script.migrate(self.env.cr, "16.0.2.4.1")
        self.assertEqual(self._stored_buttons(self.view), dict.fromkeys(OLD_BUTTONS_ARCHS, KEPT_BUTTONS))


@tagged("post_install", "-at_install")
class TestRenameChecklist(PreMigrationCase):
    """rename_checklist on a database that holds the checklist under its former
    names, as its update runs it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.env["project.project"].create({"name": "Checklist Project"})
        cls.task = cls.env["project.task"].create({"name": "Checklist Task", "project_id": project.id})
        cls.items = (
            cls.env[CHECKLIST_MODEL]
            .with_context(mail_auto_subscribe_no_notify=True)
            .create(
                [
                    {"name": "Write the test", "task_id": cls.task.id},
                    {"name": "Run the test", "task_id": cls.task.id, "state": "done"},
                ],
            )
        )
        cls.form_view = cls.env.ref("%s.view_task_form2" % MODULE)

    def _item_rows(self, table):
        self.env.cr.execute(
            sql.SQL("SELECT id, name, state, task_id, user_id, reviewer_id FROM {} ORDER BY id").format(
                sql.Identifier(table)
            )
        )
        return self.env.cr.fetchall()

    def _model_names(self, records):
        """{model of each of records: the model name its column in MODEL_NAME_RECORDS holds}."""
        model_names = {}
        for record in records:
            table, column = MODEL_NAME_RECORDS[record._name]
            self.env.cr.execute(
                sql.SQL("SELECT {} FROM {} WHERE id = %s").format(sql.Identifier(column), sql.Identifier(table)),
                (record.id,),
            )
            model_names[record._name] = self.env.cr.fetchone()[0]
        return model_names

    def _checklist_external_ids(self):
        self.env.cr.execute(
            "SELECT module, name, res_id FROM ir_model_data WHERE strpos(name, %s) > 0", (CHECKLIST_TABLE,)
        )
        return {(module, name): res_id for module, name, res_id in self.env.cr.fetchall()}

    def _constraint_registrations(self):
        self.env.cr.execute(
            """
            SELECT con.id, con.name
              FROM ir_model_constraint con
              JOIN ir_model ON ir_model.id = con.model
             WHERE ir_model.model IN %s
            """,
            ((CHECKLIST_MODEL, FORMER_CHECKLIST_MODEL),),
        )
        return dict(self.env.cr.fetchall())

    def _insert_field(self, model, name, ttype, **columns):
        """A field of model with its external id in DELEGATING_MODULE, as a
        module that is not loaded holds it; return its id.
        """
        values = {"name": name, "ttype": ttype, "state": "base", "store": False, **columns}
        self.env.cr.execute(
            sql.SQL(
                """
                INSERT INTO ir_model_fields (model, model_id, field_description, {columns})
                SELECT model, id, jsonb_build_object('en_US', %(description)s), {values}
                  FROM ir_model
                 WHERE model = %(model)s
             RETURNING id
                """
            ).format(
                columns=sql.SQL(", ").join(map(sql.Identifier, values)),
                values=sql.SQL(", ").join(map(sql.Placeholder, values)),
            ),
            {**values, "model": model, "description": name},
        )
        (field_id,) = self.env.cr.fetchone()
        self.env["ir.model.data"].create(
            {
                "module": DELEGATING_MODULE,
                "name": "field_%s__%s" % (model.replace(".", "_"), name),
                "model": "ir.model.fields",
                "res_id": field_id,
            },
        )
        self.env.flush_all()
        return field_id

    def test_keeps_the_items_and_every_record_under_its_database_id(self):
        state = self._database_state()
        item_rows = self._item_rows(CHECKLIST_TABLE)
        self._put_back_former_checklist_names()
        self.assertFalse(table_exists(self.env.cr, CHECKLIST_TABLE))
        self.assertEqual(self._item_rows(FORMER_CHECKLIST_TABLE), item_rows)

        rename_checklist(self.env.cr)

        self.assertDatabaseState(state)
        self.assertFalse(table_exists(self.env.cr, FORMER_CHECKLIST_TABLE))
        self.assertEqual(self._item_rows(CHECKLIST_TABLE), item_rows)

    def test_the_table_keeps_its_sequence_constraints_and_indexes_under_its_name(self):
        objects = self._table_objects(CHECKLIST_TABLE)
        registrations = self._constraint_registrations()
        self.assertTrue(objects["constraints"] and objects["indexes"] and registrations)
        self._put_back_former_checklist_names()
        former_objects = self._table_objects(FORMER_CHECKLIST_TABLE)
        self.assertEqual(
            former_objects["sequence"], objects["sequence"].replace(CHECKLIST_TABLE, FORMER_CHECKLIST_TABLE)
        )
        for kind in ("constraints", "indexes"):
            self.assertEqual(
                former_objects[kind],
                [FORMER_CHECKLIST_TABLE + name[len(CHECKLIST_TABLE) :] for name in objects[kind]],
                kind,
            )
        self.assertTrue(
            all(name.startswith(FORMER_CHECKLIST_TABLE + "_") for name in self._constraint_registrations().values())
        )

        rename_checklist(self.env.cr)

        self.assertEqual(self._table_objects(CHECKLIST_TABLE), objects)
        self.assertEqual(self._constraint_registrations(), registrations)
        self.registry.check_indexes(self.env.cr, [CHECKLIST_MODEL])
        self.assertEqual(self._table_objects(CHECKLIST_TABLE)["indexes"], objects["indexes"], "core finds them by name")

    def test_records_naming_the_model_follow_it(self):
        item = self.items[0]
        partner = self.env["res.partner"].create({"name": "Item Follower"})
        # Scheduled on the task and moved: core subscribes the assignee to the record, and an item has no followers.
        activity = self.task.activity_schedule(summary="Check the item")
        activity.write({"res_model_id": self.env["ir.model"]._get_id(CHECKLIST_MODEL), "res_id": item.id})
        records = [
            self.env["mail.message"].create(
                {"model": CHECKLIST_MODEL, "res_id": item.id, "body": "Item note", "message_type": "comment"},
            ),
            self.env["mail.followers"].create(
                {"res_model": CHECKLIST_MODEL, "res_id": item.id, "partner_id": partner.id}
            ),
            activity,
            self.env["ir.attachment"].create({"name": "item.txt", "res_model": CHECKLIST_MODEL, "res_id": item.id}),
            self.env["ir.filters"].create({"name": "My items", "model_id": CHECKLIST_MODEL, "user_id": self.env.uid}),
            self.env["ir.actions.act_window"].create({"name": "Items", "res_model": CHECKLIST_MODEL}),
        ]
        self.assertEqual({record._name for record in records}, set(MODEL_NAME_RECORDS))
        self._put_back_former_checklist_names()
        self.assertEqual(self._model_names(records), dict.fromkeys(MODEL_NAME_RECORDS, FORMER_CHECKLIST_MODEL))

        rename_checklist(self.env.cr)

        self.assertEqual(self._model_names(records), dict.fromkeys(MODEL_NAME_RECORDS, CHECKLIST_MODEL))

    def test_the_columns_renamed_include_those_known_to_name_a_model(self):
        self.assertLessEqual(set(REQUIRED_MODEL_NAME_COLUMNS), set(_model_name_columns(self.env)))
        self.assertIn(REFERENCE_COLUMN, _reference_columns(self.env))

    def test_a_reference_to_an_item_names_the_model_and_keeps_the_id(self):
        item_id = self.items[0].id
        menu = self.env["ir.ui.menu"].create({"name": "Item Menu"})
        self._put_back_former_checklist_names()
        self.env.cr.execute(
            "UPDATE ir_ui_menu SET action = %s WHERE id = %s", ("%s,%d" % (FORMER_CHECKLIST_MODEL, item_id), menu.id)
        )

        rename_checklist(self.env.cr)

        self.env.cr.execute("SELECT action FROM ir_ui_menu WHERE id = %s", (menu.id,))
        self.assertEqual(self.env.cr.fetchone()[0], "%s,%d" % (CHECKLIST_MODEL, item_id))

    def test_a_pending_write_naming_the_former_names_is_renamed_too(self):
        favorite = self.env["ir.filters"].create(
            {"name": "Checked tasks", "model_id": "project.task", "user_id": self.env.uid},
        )
        self._put_back_former_checklist_names()
        favorite.domain = "[('subtask_ids.state', '=', 'todo')]"

        rename_checklist(self.env.cr)

        self.env.cr.execute("SELECT domain FROM ir_filters WHERE id = %s", (favorite.id,))
        self.assertEqual(self.env.cr.fetchone()[0], "[('checklist_item_ids.state', '=', 'todo')]")

    def test_a_record_read_before_the_rename_reads_the_new_names(self):
        model = self.env["ir.model"].browse(self.env["ir.model"]._get_id(CHECKLIST_MODEL))
        self._put_back_former_checklist_names()
        self.assertEqual(model.model, FORMER_CHECKLIST_MODEL)

        rename_checklist(self.env.cr)

        self.assertEqual(model.model, CHECKLIST_MODEL)

    def test_rewriting_views_starts_from_a_pending_write_and_keeps_its_rewrite(self):
        stored_arch = self.form_view.arch_db
        self.assertIn('string="Checklist"', stored_arch)
        self.form_view.arch_db = stored_arch.replace('string="Checklist"', 'string="Checklist (pending)"')

        _rewrite_module_views(
            self.env.cr, ("project.task",), lambda _model, arch: arch.replace("(pending)", "(rewritten)")
        )

        self.assertIn('string="Checklist (rewritten)"', self._stored_archs(self.form_view)["en_US"])
        self.assertIn('string="Checklist (rewritten)"', self.form_view.arch_db)

    def test_copies_of_the_fields_follow_them(self):
        """A model that delegates to the task holds a copy of each field, with
        its external id in another module; a field of the same name on a model
        that does not stays.
        """
        self._put_back_former_checklist_names()
        copy_id = self._insert_field(
            DELEGATING_MODEL,
            "subtask_ids",
            "one2many",
            relation=FORMER_CHECKLIST_MODEL,
            related="task_id.subtask_ids",
        )
        dependent_id = self._insert_field(
            DELEGATING_MODEL, "x_checklist_size", "integer", depends="task_id.subtask_ids,task_id.subtask_ids.state"
        )
        bystander_id = self._insert_field("res.users", "subtask_ids", "char")

        rename_checklist(self.env.cr)

        self.env.cr.execute(
            "SELECT id, name, relation, related, depends FROM ir_model_fields WHERE id IN %s ORDER BY id",
            ((copy_id, dependent_id, bystander_id),),
        )
        self.assertEqual(
            self.env.cr.fetchall(),
            [
                (copy_id, "checklist_item_ids", CHECKLIST_MODEL, "task_id.checklist_item_ids", None),
                (
                    dependent_id,
                    "x_checklist_size",
                    None,
                    None,
                    "task_id.checklist_item_ids,task_id.checklist_item_ids.state",
                ),
                (bystander_id, "subtask_ids", None, None, None),
            ],
        )
        self.assertEqual(
            self._external_ids(DELEGATING_MODULE),
            {
                ("ir.model.fields", "field_res_partner__checklist_item_ids"): copy_id,
                ("ir.model.fields", "field_res_partner__x_checklist_size"): dependent_id,
                ("ir.model.fields", "field_res_users__subtask_ids"): bystander_id,
            },
        )

    def test_external_ids_keep_their_records(self):
        records = {name: self.env.ref("%s.%s" % (MODULE, name)) for name in RENAMED_CHECKLIST_EXTERNAL_IDS.values()}
        self.env["ir.model.data"].create(
            {
                "module": OTHER_MODULE,
                "name": "field_%s__name" % CHECKLIST_TABLE,
                "model": "ir.model.fields",
                "res_id": self.env["ir.model.fields"]._get(CHECKLIST_MODEL, "name").id,
            },
        )
        checklist_external_ids = self._checklist_external_ids()
        # a module extending the model holds its own external id of the model
        self.assertEqual(
            {module for module, name in checklist_external_ids if name != "model_%s" % CHECKLIST_TABLE},
            {MODULE, OTHER_MODULE},
            "without the activity fields",
        )
        self._put_back_former_checklist_names()
        self.assertFalse(self._checklist_external_ids())
        for former_name, name in RENAMED_CHECKLIST_EXTERNAL_IDS.items():
            # looked up, and cached, under the former name, as the update does
            self.assertEqual(self.env.ref("%s.%s" % (MODULE, former_name)), records[name])

        rename_checklist(self.env.cr)

        self.assertEqual(self._checklist_external_ids(), checklist_external_ids, "those of other modules included")
        for former_name, name in RENAMED_CHECKLIST_EXTERNAL_IDS.items():
            self.assertEqual(self.env.ref("%s.%s" % (MODULE, name)), records[name])
            self.assertFalse(self.env.ref("%s.%s" % (MODULE, former_name), raise_if_not_found=False))

    def test_followers_of_the_checklist_subtype_stay_subscribed_to_the_subtype_the_update_loads(self):
        subtype = self.env.ref("%s.checklist_subtype" % MODULE)
        partner = self.env["res.partner"].create({"name": "Checklist Follower"})
        self.task.message_subscribe(partner_ids=partner.ids, subtype_ids=subtype.ids)
        self._put_back_former_checklist_names()

        rename_checklist(self.env.cr)
        convert_file(self.env.cr, MODULE, SUBTYPE_DATA_FILE, {}, mode="update")

        self.env.invalidate_all()
        loaded_subtype = self.env.ref("%s.checklist_subtype" % MODULE)
        follower = self.task.message_follower_ids.filtered(lambda follower: follower.partner_id == partner)
        self.assertEqual(follower.subtype_ids, loaded_subtype)
        self.assertEqual(loaded_subtype, subtype)

    def test_the_task_form_forgets_the_default_assignee_in_every_translation(self):
        self.env.cr.execute(
            """
            UPDATE ir_ui_view
               SET arch_db = arch_db || jsonb_build_object(
                       'fr_FR', replace(arch_db->>'en_US', 'string="Checklist"', 'string="Liste de contrôle"')
                   )
             WHERE id = %s
            """,
            (self.form_view.id,),
        )
        archs = self._stored_archs(self.form_view)
        self.assertIn('string="Liste de contrôle"', archs["fr_FR"])
        self._put_back_former_checklist_names()
        for lang, arch in self._stored_archs(self.form_view).items():
            self.assertIn(FORMER_CHECKLIST_NODES, arch, lang)

        rename_checklist(self.env.cr)

        self.assertEqual(self._stored_archs(self.form_view), archs)
        for lang, arch in archs.items():
            self.assertIn(CHECKLIST_LIST_NODE, arch, lang)
            self.assertNotIn(DROPPED_CHECKLIST_FIELD, arch, lang)
        self.form_view._check_xml()

    def test_renames_the_fields_and_the_model_in_rules_filters_and_window_actions(self):
        texts = {
            "domain": "[('%(field)s.state', '=', 'todo'), ('old_subtask_ids', '!=', False)]",
            "context": "{'group_by': '%(other_field)s', 'res_model': '%(model)s'}",
            "sort": '["%(field)s desc"]',
        }
        former_texts = {
            key: text % {"field": "subtask_ids", "other_field": "kanban_subtasks", "model": FORMER_CHECKLIST_MODEL}
            for key, text in texts.items()
        }
        new_texts = {
            key: text % {"field": "checklist_item_ids", "other_field": "kanban_checklist", "model": CHECKLIST_MODEL}
            for key, text in texts.items()
        }
        rule = self.env["ir.rule"].create(
            {
                "name": "Checked tasks",
                "model_id": self.env["ir.model"]._get_id("project.task"),
                "groups": [Command.set(self.env.ref("base.group_system").ids)],
            },
        )
        favorite = self.env["ir.filters"].create(
            {"name": "Checked tasks", "model_id": "project.task", "user_id": self.env.uid},
        )
        action = self.env["ir.actions.act_window"].create({"name": "Checked tasks", "res_model": "project.task"})
        self._put_back_former_checklist_names()
        cr = self.env.cr
        cr.execute("UPDATE ir_rule SET domain_force = %s WHERE id = %s", (former_texts["domain"], rule.id))
        cr.execute(
            "UPDATE ir_filters SET domain = %(domain)s, context = %(context)s, sort = %(sort)s WHERE id = %(id)s",
            {**former_texts, "id": favorite.id},
        )
        cr.execute(
            "UPDATE ir_act_window SET domain = %(domain)s, context = %(context)s WHERE id = %(id)s",
            {**former_texts, "id": action.id},
        )

        rename_checklist(self.env.cr)

        self.env.invalidate_all()
        self.assertRecordValues(rule, [{"domain_force": new_texts["domain"]}])
        self.assertRecordValues(favorite, [new_texts])
        self.assertRecordValues(action, [{"domain": new_texts["domain"], "context": new_texts["context"]}])

    def test_logs_one_line_with_the_counts(self):
        self._put_back_former_checklist_names()

        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            counts = rename_checklist(self.env.cr)

        self.assertEqual(len(logs.records), 1)
        self.assertTrue(
            logs.records[0].getMessage().startswith("Checklist renamed: 1 tables, 1 sequences, "),
            logs.records[0].getMessage(),
        )
        self.assertEqual(counts["models"], 1)

    def test_second_run_changes_nothing(self):
        self._put_back_former_checklist_names()
        rename_checklist(self.env.cr)
        state_after_first_run = self._database_state()

        with self.assertNoLogs(HOOKS_LOGGER, level=logging.INFO):
            counts = rename_checklist(self.env.cr)

        self.assertEqual(counts, dict.fromkeys(counts, 0))
        self.assertDatabaseState(state_after_first_run)

    def test_migration_script_renames_on_update_only(self):
        spec = importlib.util.spec_from_file_location("pre_migration", CHECKLIST_MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        self._put_back_former_checklist_names()

        script.migrate(self.env.cr, None)
        self.assertTrue(table_exists(self.env.cr, FORMER_CHECKLIST_TABLE))

        script.migrate(self.env.cr, "16.0.2.5.0")
        self.assertTrue(table_exists(self.env.cr, CHECKLIST_TABLE))
        self.assertFalse(table_exists(self.env.cr, FORMER_CHECKLIST_TABLE))


@tagged("post_install", "-at_install")
class TestForgetChecklistActivities(PreMigrationCase):
    """forget_checklist_activities on a database whose checklist model still has
    the activity fields of mail.activity.mixin, an activity on an item, and the
    external ids of their selection values in mail.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.env["project.project"].create({"name": "Activity Project"})
        cls.task = cls.env["project.task"].create({"name": "Activity Task", "project_id": project.id})
        item = (
            cls.env[CHECKLIST_MODEL]
            .with_context(mail_auto_subscribe_no_notify=True)
            .create(
                {"name": "Item with an activity", "task_id": cls.task.id},
            )
        )
        cls.task_activity = cls.task.activity_schedule(summary="Stays on the task")
        # Scheduled on the task and moved: an item no longer has the fields an activity needs.
        cls.item_activity = cls.task.activity_schedule(summary="Check the item")
        cls.item_activity.write({"res_model_id": cls.env["ir.model"]._get_id(CHECKLIST_MODEL), "res_id": item.id})
        cls.field_ids = {name: cls._insert_activity_field(name, *spec) for name, spec in ACTIVITY_FIELDS.items()}
        cls.env.flush_all()

    @classmethod
    def _insert_activity_field(cls, name, ttype, modules):
        cr = cls.env.cr
        cr.execute(
            """
            INSERT INTO ir_model_fields (model, model_id, name, field_description, ttype, state, store)
            SELECT model, id, %(name)s, jsonb_build_object('en_US', %(name)s), %(ttype)s, 'base', false
              FROM ir_model
             WHERE model = %(model)s
         RETURNING id
            """,
            {"model": CHECKLIST_MODEL, "name": name, "ttype": ttype},
        )
        (field_id,) = cr.fetchone()
        external_ids = [
            {"module": module, "name": "field_%s__%s" % (CHECKLIST_TABLE, name), "model": "ir.model.fields"}
            for module in modules
        ]
        for record_vals in external_ids:
            record_vals["res_id"] = field_id
        if ttype == "selection":
            for value in ACTIVITY_STATES:
                cr.execute(
                    """
                    INSERT INTO ir_model_fields_selection (field_id, value, name, sequence)
                    VALUES (%s, %s, jsonb_build_object('en_US', %s), 1)
                 RETURNING id
                    """,
                    (field_id, value, value),
                )
                external_ids.append(
                    {
                        "module": "mail",
                        "name": selection_xmlid("mail", CHECKLIST_MODEL, name, value).split(".", 1)[1],
                        "model": "ir.model.fields.selection",
                        "res_id": cr.fetchone()[0],
                    },
                )
        cls.env["ir.model.data"].create(external_ids)
        return field_id

    def _activity_rows(self):
        """The rows the update deletes at its end, and those it would leave."""
        cr = self.env.cr
        field_ids = tuple(self.field_ids.values())
        cr.execute("SELECT id FROM ir_model_fields WHERE id IN %s", (field_ids,))
        fields_left = {field_id for (field_id,) in cr.fetchall()}
        cr.execute("SELECT count(*) FROM ir_model_fields_selection WHERE field_id IN %s", (field_ids,))
        (selection_count,) = cr.fetchone()
        cr.execute(
            """
            SELECT imd.module, imd.name
              FROM ir_model_data imd
              LEFT JOIN ir_model_fields_selection selection
                ON imd.model = 'ir.model.fields.selection' AND selection.id = imd.res_id
             WHERE (imd.model = 'ir.model.fields' AND imd.res_id IN %(fields)s) OR selection.field_id IN %(fields)s
            """,
            {"fields": field_ids},
        )
        external_ids = set(cr.fetchall())
        cr.execute("SELECT id FROM mail_activity WHERE id IN %s", ((self.task_activity.id, self.item_activity.id),))
        activity_ids = {activity_id for (activity_id,) in cr.fetchall()}
        return fields_left, selection_count, external_ids, activity_ids

    def _task_activity_external_ids(self):
        self.env.cr.execute(
            "SELECT id FROM ir_model_data WHERE name ~ '^(field_|selection__)project_task__(my_)?activity_' ORDER BY id"
        )
        return self.env.cr.fetchall()

    def assertForgotten(self):
        self.assertEqual(
            self._activity_rows(),
            (
                set(self.field_ids.values()),
                len(ACTIVITY_STATES),
                {
                    (MODULE, "field_%s__%s" % (CHECKLIST_TABLE, name))
                    for name, (_ttype, modules) in ACTIVITY_FIELDS.items()
                    if MODULE in modules
                },
                {self.task_activity.id},
            ),
            "the fields, their values and the external ids of this module stay for the end of the update",
        )

    def test_forgets_the_activities_and_the_external_ids_the_update_would_leave(self):
        fields_left, selection_count, external_ids, activity_ids = self._activity_rows()
        self.assertEqual(
            {module for module, _name in external_ids} | activity_ids,
            {MODULE, OTHER_MODULE, "mail", self.task_activity.id, self.item_activity.id},
        )
        task_external_ids = self._task_activity_external_ids()
        self.assertTrue(task_external_ids)

        counts = forget_checklist_activities(self.env.cr)

        self.assertForgotten()
        self.assertEqual(counts, {"activities": 1, "external ids": len(ACTIVITY_STATES) + 2})
        self.assertEqual(self._task_activity_external_ids(), task_external_ids, "the task keeps its activity fields")
        self.assertFalse(self.item_activity.exists())

    def test_second_run_changes_nothing(self):
        forget_checklist_activities(self.env.cr)
        rows_after_first_run = self._activity_rows()

        with self.assertNoLogs(HOOKS_LOGGER, level=logging.INFO):
            counts = forget_checklist_activities(self.env.cr)

        self.assertEqual(counts, dict.fromkeys(counts, 0))
        self.assertEqual(self._activity_rows(), rows_after_first_run)

    def test_migration_script_forgets_them_on_update_only(self):
        spec = importlib.util.spec_from_file_location("pre_migration", ACTIVITIES_MIGRATION_SCRIPT)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        rows_before = self._activity_rows()

        script.migrate(self.env.cr, None)
        self.assertEqual(self._activity_rows(), rows_before)

        script.migrate(self.env.cr, "16.0.2.6.0")
        self.assertForgotten()


@tagged("post_install", "-at_install")
class TestRenameExternalIds(TransactionCase):
    """rename_external_ids on a module that is not this one, next to a second
    one that holds an external id of the same name.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
        cls.env.flush_all()

    @classmethod
    def _create_view(cls, module_name):
        return cls.env["ir.ui.view"].create(
            {"name": "%s.view" % module_name, "model": "project.task", "arch": "<tree><field name='name'/></tree>"},
        )

    def _rows(self, record_ids):
        self.env.cr.execute(
            "SELECT id, module, name FROM ir_model_data WHERE id IN %s ORDER BY id", (tuple(record_ids),)
        )
        return self.env.cr.fetchall()

    def test_renames_the_external_ids_of_one_module(self):
        rename_external_ids(self.env.cr, REPLACED_MODULE, {"replaced_view": "renamed_view", "unknown": "renamed"})

        self.assertEqual(
            self._rows((self.external_ids | self.bystander_external_id).ids),
            [
                (self.external_ids[0].id, REPLACED_MODULE, "renamed_view"),
                (self.external_ids[1].id, REPLACED_MODULE, "replaced_menu"),
                (self.bystander_external_id.id, BYSTANDER_MODULE, "replaced_view"),
            ],
        )

    def test_a_new_name_already_taken_leaves_the_former_one(self):
        rename_external_ids(self.env.cr, REPLACED_MODULE, {"replaced_view": "replaced_menu"})

        self.assertEqual(
            self._rows(self.external_ids.ids),
            [
                (self.external_ids[0].id, REPLACED_MODULE, "replaced_view"),
                (self.external_ids[1].id, REPLACED_MODULE, "replaced_menu"),
            ],
        )
