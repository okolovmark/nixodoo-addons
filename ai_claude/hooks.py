import logging

from psycopg2 import sql

from odoo import SUPERUSER_ID, api, models

_logger = logging.getLogger(__name__)

MODULE = "ai_claude"

ADOPTED_MODELS = ("ai.conversation", "ai.exchange.mixin")
MODEL_RECORDS = tuple(("ir.model", "model_" + model.replace(".", "_")) for model in ADOPTED_MODELS)
# Before the move only the module it is cut from has the mixin: the conversation is also held by every
# module that adds an assistant to it.
HOLDER_RECORDS = (("ir.model", "model_ai_exchange_mixin"),)
# Of the Many2one fields this module declares: a foreign key of a field another module adds to
# the conversation stays with that module.
ADOPTED_FOREIGN_KEYS = ("ai_conversation_create_uid_fkey", "ai_conversation_write_uid_fkey")
ADOPTED_RECORDS = (
    ("ir.model.access", "access_ai_conversation_system"),
    ("ir.ui.view", "ai_conversation_view_form"),
    ("ir.ui.view", "ai_conversation_view_tree"),
    ("ir.ui.view", "ai_conversation_view_search"),
    ("ir.actions.act_window", "ai_conversation_action"),
)
OWNERSHIP_TABLES = ("ir_model_constraint", "ir_model_relation")

# Once the module is installed, a module that depends on it holds the external id of its model because it
# extends that model; before, the dependency is the one the update that moves the records added.
FIND_HOLDERS = """
    WITH RECURSIVE extender(id) AS (
        SELECT dependency.module_id
          FROM ir_module_module_dependency dependency
          JOIN ir_module_module adopter ON adopter.name = dependency.name
         WHERE adopter.name = %(module)s
           AND adopter.state NOT IN ('uninstalled', 'to install')
        UNION
        SELECT dependency.module_id
          FROM ir_module_module_dependency dependency
          JOIN ir_module_module dependee ON dependee.name = dependency.name
          JOIN extender ON extender.id = dependee.id
    )
    SELECT DISTINCT imd.module
      FROM ir_model_data imd
     WHERE (imd.model, imd.name) IN %(records)s
       AND imd.module != %(module)s
       AND imd.module NOT IN (SELECT module.name FROM ir_module_module module JOIN extender ON extender.id = module.id)
     ORDER BY imd.module
"""
# An external id is unique per module: a name the adopting module already holds is left alone.
NAME_IS_FREE = """
    NOT EXISTS (
        SELECT 1 FROM ir_model_data taken WHERE taken.module = %(module)s AND taken.name = imd.name
    )
"""
ADOPT_RECORDS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
     WHERE imd.module = %(holder)s
       AND (imd.model, imd.name) IN %(records)s
       AND
    """
    + NAME_IS_FREE
    + """
 RETURNING imd.model, imd.name, imd.res_id
    """
)
ADOPT_FIELDS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
      FROM ir_model_fields field
     WHERE imd.module = %(holder)s
       AND imd.model = 'ir.model.fields'
       AND imd.res_id = field.id
       AND (field.model, field.name) IN %(fields)s
       AND
    """
    + NAME_IS_FREE
)
ADOPT_SELECTIONS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
      FROM ir_model_fields_selection selection, ir_model_fields field
     WHERE imd.module = %(holder)s
       AND imd.model = 'ir.model.fields.selection'
       AND imd.res_id = selection.id
       AND selection.field_id = field.id
       AND (field.model, field.name, selection.value) IN %(selections)s
       AND
    """
    + NAME_IS_FREE
)
ADOPT_OWNERSHIP = sql.SQL(
    """
    UPDATE {table} owned
       SET module = target.id
      FROM ir_module_module source, ir_module_module target, ir_model
     WHERE source.name = %(holder)s
       AND target.name = %(module)s
       AND owned.module = source.id
       AND owned.model = ir_model.id
       AND (ir_model.model IN %(models)s OR owned.name IN %(foreign_keys)s)
       AND NOT EXISTS (
           SELECT 1 FROM {table} taken WHERE taken.module = target.id AND taken.name = owned.name
       )
    """
)
ADOPT_CONSTRAINT_IDS = (
    """
    UPDATE ir_model_data imd
       SET module = %(module)s
      FROM ir_model_constraint owned, ir_model
     WHERE imd.module = %(holder)s
       AND imd.model = 'ir.model.constraint'
       AND imd.res_id = owned.id
       AND owned.model = ir_model.id
       AND (ir_model.model IN %(models)s OR owned.name IN %(foreign_keys)s)
       AND
    """
    + NAME_IS_FREE
)
ADOPT_VIEW_FILE = """
    UPDATE ir_ui_view
       SET arch_fs = %(module)s || substr(arch_fs, length(%(holder)s) + 1)
     WHERE id IN %(view_ids)s
       AND left(arch_fs, length(%(holder)s) + 1) = %(holder)s || '/'
"""


def pre_init_hook(cr):
    """Hand to this module, before its data files are loaded, the records another module created
    under the same external ids, so that they keep their database id: left with that module,
    they would be created a second time here and the originals deleted when it is updated.

    The holder is a module that has the external id of the exchange mixin (`find_holders`). From it this module
    takes the external ids in ADOPTED_RECORDS, those of its models, of the fields and selection
    values its classes declare, and ADOPTED_FOREIGN_KEYS; a view moves to the file of the same
    name here. What the holder declares itself on the conversation (an assistant value, its own
    fields and their foreign keys) stays with it.

    A database where no other module holds the mixin is left untouched, and a second run finds
    nothing to move.
    """
    holders = find_holders(cr, MODULE, HOLDER_RECORDS)
    if not holders:
        return
    fields = declared_fields(MODULE)
    selections = declared_selections(MODULE)
    for holder in holders:
        adopt_records(
            cr,
            holder,
            MODULE,
            ADOPTED_RECORDS + MODEL_RECORDS,
            fields=fields,
            selections=selections,
            foreign_keys=ADOPTED_FOREIGN_KEYS,
        )
    _logger.info("%s adopted the records of %s", MODULE, ", ".join(holders))
    # external ids are cached per registry, and these changed behind the ORM
    api.Environment(cr, SUPERUSER_ID, {}).registry.clear_caches()


def find_holders(cr, module, model_records):
    """Return the modules other than `module` that hold one of the external ids `model_records`,
    (model, name) pairs, but those that extend these models of an installed `module`: the external id
    of a model only `module` defines tells its holder apart."""
    cr.execute(FIND_HOLDERS, {"records": tuple(model_records), "module": module})
    return [holder for (holder,) in cr.fetchall()]


def declared_fields(module):
    """Return the (model, field name) pairs the classes of module declare, the magic fields of
    the models it defines included."""
    return tuple(
        sorted(
            {
                (model_class._name, field.name)
                for model_class in models.MetaModel.module_to_models[module]
                for field in model_class._field_definitions
            }
        )
    )


def declared_selections(module):
    """Return the (model, field name, value) triples of the selection values the classes of module
    declare, as Field._selection_modules credits them to a module: every value of a `selection`
    list, and each value of a `selection_add` that carries its label."""
    declared = set()
    for model_class in models.MetaModel.module_to_models[module]:
        for field in model_class._field_definitions:
            if field.type != "selection":
                continue
            selection = field._args__.get("selection")
            values = [value for value, _label in selection] if isinstance(selection, list) else []
            values += [value_label[0] for value_label in field._args__.get("selection_add", ()) if len(value_label) > 1]
            declared.update((model_class._name, field.name, value) for value in values)
    return tuple(sorted(declared))


def adopt_records(cr, holder, module, records, fields=(), selections=(), model_names=(), foreign_keys=()):
    """Move to `module` the external ids `holder` has of `records` ((model, name) pairs), of the
    ir.model.fields of `fields` ((model, name) pairs) and of the selection values `selections`
    ((model, field name, value) triples), and the SQL constraints (with their external ids) and
    many2many tables registered for `model_names` and the constraints `foreign_keys`; an adopted
    view is read from the file of the same name in `module`. A name `module` already holds stays
    with the holder.

    Return the (model, name) pairs of `records` adopted."""
    params = {
        "module": module,
        "holder": holder,
        "records": tuple(records),
        "fields": tuple(fields),
        "selections": tuple(selections),
        "models": tuple(model_names) or ("",),
        "foreign_keys": tuple(foreign_keys) or ("",),
    }
    adopted_rows = []
    if records:
        cr.execute(ADOPT_RECORDS, params)
        adopted_rows = cr.fetchall()
    if fields:
        cr.execute(ADOPT_FIELDS, params)
    if selections:
        cr.execute(ADOPT_SELECTIONS, params)
    if model_names or foreign_keys:
        cr.execute(ADOPT_CONSTRAINT_IDS, params)
        for table in OWNERSHIP_TABLES:
            cr.execute(ADOPT_OWNERSHIP.format(table=sql.Identifier(table)), params)
    view_ids = tuple(res_id for model, _name, res_id in adopted_rows if model == "ir.ui.view")
    if view_ids:
        cr.execute(ADOPT_VIEW_FILE, dict(params, view_ids=view_ids))
    return {(model, name) for model, name, _res_id in adopted_rows}
