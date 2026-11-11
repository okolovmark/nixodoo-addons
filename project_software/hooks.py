from odoo import SUPERUSER_ID, api

MODULE = "project_software"
LEGACY_MODULE = "kaertech_project_software"

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
 RETURNING imd.name, imd.res_id
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


def pre_init_hook(cr):
    """Take over the records of kaertech_project_software, which this module was cut from.

    The checklist, the test cases and the software task fields lived in that
    module first. On a database that has it, its update installs this module
    before updating itself. Left alone, the data files would create a second
    copy of each view, access right, action, menu and message subtype, and the
    update would then delete the originals, taking the follower subscriptions
    to the subtypes with them.

    So the external ids of everything that moved change module, under the same
    name: models, fields, selection values, foreign keys and data records keep
    their database id, and the data files update them in place.

    The views in SPLIT_VIEWS are adopted with an empty arch. Their old arch also
    names fields of the legacy module, and it is validated as soon as any other
    view of the same form is loaded, before the data file writes the part that
    belongs here. The legacy module adds its own part back when it is updated.

    A database without kaertech_project_software is left untouched, and a
    second run finds nothing left to move.
    """
    cr.execute(
        "SELECT 1 FROM ir_module_module WHERE name = %s AND state IN ('installed', 'to upgrade')",
        (LEGACY_MODULE,),
    )
    if not cr.fetchone():
        return
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
    split_view_ids = tuple(res_id for name, res_id in cr.fetchall() if name in SPLIT_VIEWS)
    if split_view_ids:
        cr.execute(
            "UPDATE ir_ui_view SET arch_db = jsonb_build_object('en_US', '<data/>') WHERE id IN %s",
            (split_view_ids,),
        )
    # external ids are cached per registry, and these changed behind the ORM
    api.Environment(cr, SUPERUSER_ID, {}).registry.clear_caches()
