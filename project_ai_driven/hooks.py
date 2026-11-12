from psycopg2 import sql

from odoo import SUPERUSER_ID, api
from odoo.tools.sql import column_exists, rename_column

MODULE = "project_ai_driven"
FORMER_MODULE = "project_software"
LEGACY_MODULE = "kaertech_project_software"

FLAG = "is_ai_driven"
FORMER_FLAG = "is_software_project"
FLAG_TABLE = "project_project"
RENAMED_EXTERNAL_IDS = {
    "project_project_view_form_inherit_software": "project_project_view_form_inherit_ai_driven",
}
OWNERSHIP_TABLES = ("ir_model_constraint", "ir_model_relation")
FIELD_NAMING_COLUMNS = (
    ("ir_rule", "domain_force"),
    ("ir_filters", "domain"),
    ("ir_filters", "context"),
    ("ir_filters", "sort"),
    ("ir_act_window", "domain"),
    ("ir_act_window", "context"),
)

# What the legacy module holds, under the names it knows.
ADOPTED_MODELS = ("project.task.subtask", "project.task.testcase")
ADOPTED_FIELDS = (
    ("project.project", "is_software_project"),
    ("project.task", "is_software_project"),
    ("project.task", "questions"),
    ("project.task", "answers"),
    ("project.task", "solution"),
    ("project.task", "difficulty"),
    ("project.task", "knowledge_area_level"),
    ("project.task", "priority_level"),
    ("project.task", "task_type"),
    ("project.task", "tshirt_estimate"),
    ("project.task", "subtask_ids"),
    ("project.task", "default_user"),
    ("project.task", "completion"),
    ("project.task", "completion_xml"),
    ("project.task", "kanban_subtasks"),
    ("project.task", "testcase_ids"),
    ("project.task", "testcase_completion"),
    ("project.task", "testcase_completion_xml"),
    ("project.task", "kanban_testcases"),
    ("project.task", "tester_id"),
    ("project.task", "testcase_general_comments"),
)
ADOPTED_FOREIGN_KEYS = ("project_task_tester_id_fkey",)
ADOPTED_RECORDS = (
    ("ir.model.access", "access_task_subtask"),
    ("ir.model.access", "access_task_testcase"),
    ("mail.message.subtype", "subtasks_subtype"),
    ("mail.message.subtype", "all_subtask_subtype"),
    ("ir.ui.view", "view_task_form2"),
    ("ir.ui.view", "view_task_kanban_subtasks"),
    ("ir.ui.view", "project_task_subtask_view_form"),
    ("ir.ui.view", "project_task_subtask_view_search"),
    ("ir.ui.view", "subtask_list_view"),
    ("ir.ui.view", "view_task_form2_testcase"),
    ("ir.ui.view", "view_task_kanban_testcase"),
    ("ir.ui.view", "project_task_testcase_view_form"),
    ("ir.ui.view", "project_task_testcase_view_search"),
    ("ir.ui.view", "testcase_list_view"),
    ("ir.ui.view", "view_task_form2_common"),
    ("ir.ui.view", "view_task_tree2"),
    ("ir.ui.view", "project_project_view_form_inherit_software"),
    ("ir.actions.act_window", "action_view_subtask"),
    ("ir.actions.act_window", "action_view_testcase"),
    ("ir.ui.menu", "menu_action_view_subtask"),
    ("ir.ui.menu", "menu_action_view_testcase"),
)
# These two also carried nodes that stayed in the legacy module.
SPLIT_VIEWS = ("view_task_form2_common", "view_task_tree2")

# An external id is unique per module: a name this module already holds is left alone.
NAME_IS_FREE = """
    NOT EXISTS (
        SELECT 1 FROM ir_model_data taken WHERE taken.module = %(module)s AND taken.name = imd.name
    )
"""
REHOME_MODELS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
      FROM ir_model
     WHERE imd.module = %(legacy)s
       AND imd.model = 'ir.model'
       AND imd.res_id = ir_model.id
       AND ir_model.model IN %(models)s
       AND
    """
    + NAME_IS_FREE
)
REHOME_FIELDS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
      FROM ir_model_fields field
     WHERE imd.module = %(legacy)s
       AND imd.model = 'ir.model.fields'
       AND imd.res_id = field.id
       AND (field.model IN %(models)s OR (field.model, field.name) IN %(fields)s)
       AND
    """
    + NAME_IS_FREE
)
REHOME_SELECTIONS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
      FROM ir_model_fields_selection selection
      JOIN ir_model_fields field ON field.id = selection.field_id
     WHERE imd.module = %(legacy)s
       AND imd.model = 'ir.model.fields.selection'
       AND imd.res_id = selection.id
       AND (field.model IN %(models)s OR (field.model, field.name) IN %(fields)s)
       AND
    """
    + NAME_IS_FREE
)
REHOME_RECORDS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
     WHERE imd.module = %(legacy)s
       AND (imd.model, imd.name) IN %(records)s
       AND
    """
    + NAME_IS_FREE
    + """
 RETURNING imd.model, imd.name, imd.res_id
    """
)
REHOME_CONSTRAINTS = """
    UPDATE ir_model_constraint con
       SET module = target.id
      FROM ir_module_module source, ir_module_module target, ir_model
     WHERE source.name = %(legacy)s
       AND target.name = %(module)s
       AND con.module = source.id
       AND con.model = ir_model.id
       AND (ir_model.model IN %(models)s OR con.name IN %(foreign_keys)s)
       AND NOT EXISTS (
           SELECT 1 FROM ir_model_constraint taken WHERE taken.module = target.id AND taken.name = con.name
       )
"""


TAKE_OVER_EXTERNAL_IDS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
     WHERE imd.module = %(former)s
       AND
    """
    + NAME_IS_FREE
)
TAKE_OVER_OWNERSHIP = sql.SQL(
    """
    UPDATE {table} owned
       SET module = target.id
      FROM ir_module_module source, ir_module_module target
     WHERE source.name = %(former)s
       AND target.name = %(module)s
       AND owned.module = source.id
       AND NOT EXISTS (
           SELECT 1 FROM {table} taken WHERE taken.module = target.id AND taken.name = owned.name
       )
    """
)
DROP_OWNERSHIP = sql.SQL(
    """
    DELETE FROM {table} owned
     USING ir_module_module source, ir_module_module target
     WHERE source.name = %(former)s
       AND target.name = %(module)s
       AND owned.module = source.id
    """
)
TAKE_OVER_VIEW_FILES = """
    UPDATE ir_ui_view
       SET arch_fs = %(module)s || substr(arch_fs, length(%(former)s) + 1)
     WHERE left(arch_fs, length(%(former)s) + 1) = %(former)s || '/'
"""

RENAME_FIELDS = """
    UPDATE ir_model_fields field
       SET name = %(flag)s
     WHERE field.name = %(former_flag)s
       AND NOT EXISTS (
           SELECT 1 FROM ir_model_fields taken WHERE taken.model = field.model AND taken.name = %(flag)s
       )
"""
RENAME_RELATED_PATHS = """
    UPDATE ir_model_fields
       SET related = array_to_string(
               array_replace(string_to_array(related, '.'), %(former_flag)s::text, %(flag)s::text), '.'
           )
     WHERE %(former_flag)s::text = ANY(string_to_array(related, '.'))
"""
RENAME_FIELD_EXTERNAL_IDS = """
    UPDATE ir_model_data imd
       SET name = 'field_' || replace(field.model, '.', '_') || '__' || %(flag)s
      FROM ir_model_fields field
     WHERE imd.model = 'ir.model.fields'
       AND imd.res_id = field.id
       AND field.name = %(flag)s
       AND imd.name = 'field_' || replace(field.model, '.', '_') || '__' || %(former_flag)s
       AND NOT EXISTS (
           SELECT 1
             FROM ir_model_data taken
            WHERE taken.module = imd.module
              AND taken.name = 'field_' || replace(field.model, '.', '_') || '__' || %(flag)s
       )
"""
RENAME_EXTERNAL_ID = """
    UPDATE ir_model_data imd
       SET name = %(new_name)s
     WHERE imd.module = %(module)s
       AND imd.name = %(former_name)s
       AND NOT EXISTS (
           SELECT 1 FROM ir_model_data taken WHERE taken.module = %(module)s AND taken.name = %(new_name)s
       )
"""
RENAME_FLAG_IN_ARCH = """
    UPDATE ir_ui_view
       SET arch_db = replace(arch_db::text, %(former_flag)s, %(flag)s)::jsonb
     WHERE strpos(arch_db::text, %(former_flag)s) > 0
"""
RENAME_FLAG_IN_TEXT = sql.SQL(
    """
    UPDATE {table}
       SET {column} = replace({column}, %(former_flag)s, %(flag)s)
     WHERE strpos({column}, %(former_flag)s) > 0
    """
)


def pre_init_hook(cr):
    """Take over what this module held under its former names.

    A database can come here in three states.

    project_software is installed: this module under its former name. Its
    update installs this module first. Everything it owns changes module:
    every external id, and every constraint and many2many table registered
    to it. Anything left behind would be deleted with the record or table it
    stands for, at the end of the update or when the emptied module is
    uninstalled.

    project_software was never installed but kaertech_project_software is: the
    module this one was cut from, on a database from before the cut. Only what
    moved here is taken over, see _take_over_legacy_records.

    Neither is installed: the database is left untouched.

    In the first two states the flag and the external ids that changed name
    are then renamed where the database stores them, see _rename_flag. Models,
    fields, selection values, foreign keys and data records thus keep their
    database id, and the data files update them in place. A second run finds
    nothing left to move or rename.
    """
    if is_installed(cr, FORMER_MODULE):
        take_over_module(cr, FORMER_MODULE, MODULE)
    elif is_installed(cr, LEGACY_MODULE):
        _take_over_legacy_records(cr)
    else:
        return
    _rename_flag(cr)
    rename_external_ids(cr, MODULE, RENAMED_EXTERNAL_IDS)
    # external ids are cached per registry, and these changed behind the ORM
    api.Environment(cr, SUPERUSER_ID, {}).registry.clear_caches()


def is_installed(cr, module_name):
    cr.execute(
        "SELECT 1 FROM ir_module_module WHERE name = %s AND state IN ('installed', 'to upgrade')",
        (module_name,),
    )
    return bool(cr.fetchone())


def take_over_module(cr, former, module):
    """Hand to module everything the module former owns: its external ids, the
    constraints and many2many tables registered to it, and the views loaded
    from its files. For the pre_init_hook of a module that replaces another
    one; the caller clears the registry caches.

    A row whose name module already holds cannot move: it is dropped, what it
    stands for stays.

    A view remembers the file it was loaded from, and a hard reset of the view
    or a server in dev mode reads its arch from that file again.
    """
    params = {"module": module, "former": former}
    cr.execute(TAKE_OVER_EXTERNAL_IDS, params)
    cr.execute("DELETE FROM ir_model_data WHERE module = %(former)s", params)
    for table in OWNERSHIP_TABLES:
        cr.execute(TAKE_OVER_OWNERSHIP.format(table=sql.Identifier(table)), params)
        cr.execute(DROP_OWNERSHIP.format(table=sql.Identifier(table)), params)
    cr.execute(TAKE_OVER_VIEW_FILES, params)


def _take_over_legacy_records(cr):
    """Left alone, the data files would create a second copy of each view,
    access right, action, menu and message subtype, and the update of the
    legacy module would then delete the originals, taking the follower
    subscriptions to the subtypes with them.

    An adopted view forgets the file it was loaded from: that file was the
    legacy module's, and its own data file names the new one when it is loaded.

    The views in SPLIT_VIEWS are adopted with an empty arch. Their old arch also
    names fields of the legacy module, and it is validated as soon as any other
    view of the same form is loaded, before the data file writes the part that
    belongs here. The legacy module, or the module that replaces it, adds its
    own part back when its data files are loaded.
    """
    params = {
        "module": MODULE,
        "legacy": LEGACY_MODULE,
        "models": ADOPTED_MODELS,
        "fields": ADOPTED_FIELDS,
        "records": ADOPTED_RECORDS,
        "foreign_keys": ADOPTED_FOREIGN_KEYS,
    }
    for query in (REHOME_MODELS, REHOME_FIELDS, REHOME_SELECTIONS, REHOME_CONSTRAINTS):
        cr.execute(query, params)
    cr.execute(REHOME_RECORDS, params)
    adopted_views = {name: res_id for model, name, res_id in cr.fetchall() if model == "ir.ui.view"}
    if adopted_views:
        cr.execute("UPDATE ir_ui_view SET arch_fs = NULL WHERE id IN %s", (tuple(adopted_views.values()),))
    split_view_ids = tuple(res_id for name, res_id in adopted_views.items() if name in SPLIT_VIEWS)
    if split_view_ids:
        cr.execute(
            "UPDATE ir_ui_view SET arch_db = jsonb_build_object('en_US', '<data/>') WHERE id IN %s",
            (split_view_ids,),
        )


def _rename_flag(cr):
    """Rename is_software_project to is_ai_driven: the column, the field on
    every model that has it (models delegating to the project or the task
    carry a copy) with its external id in whichever module holds one, and the
    stored text that names it.

    The views cannot wait for the data files: a view taken over is validated as
    soon as another view of the same model is loaded. The text is replaced as a
    substring, so that a default_<field> context key follows.
    """
    params = {"flag": FLAG, "former_flag": FORMER_FLAG}
    if column_exists(cr, FLAG_TABLE, FORMER_FLAG) and not column_exists(cr, FLAG_TABLE, FLAG):
        rename_column(cr, FLAG_TABLE, FORMER_FLAG, FLAG)
    for query in (RENAME_FIELDS, RENAME_RELATED_PATHS, RENAME_FIELD_EXTERNAL_IDS, RENAME_FLAG_IN_ARCH):
        cr.execute(query, params)
    for table, column in FIELD_NAMING_COLUMNS:
        cr.execute(
            RENAME_FLAG_IN_TEXT.format(table=sql.Identifier(table), column=sql.Identifier(column)),
            params,
        )


def rename_external_ids(cr, module, renamed_names):
    """Rename external ids of module, {former name: new name}. A new name that
    is already taken leaves the former one in place.
    """
    for former_name, new_name in renamed_names.items():
        cr.execute(RENAME_EXTERNAL_ID, {"module": module, "former_name": former_name, "new_name": new_name})
