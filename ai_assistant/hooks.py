import logging

from lxml import etree
from psycopg2 import sql
from psycopg2.extras import Json

from odoo import SUPERUSER_ID, api
from odoo.tools.convert import nodeattr2bool
from odoo.tools.misc import file_open

from odoo.addons.ai_claude.hooks import adopt_records, declared_fields, declared_selections, find_holders

_logger = logging.getLogger(__name__)

MODULE = "ai_assistant"

OWN_MODELS = (
    "ai.assistant.engine",
    "ai.assistant.exchange",
    "ai.assistant.tool.call",
    "ai.assistant.tools",
    "ai.report",
    "ai.report.share",
    "ai.report.share.log",
)
MODEL_RECORDS = tuple(("ir.model", "model_" + model.replace(".", "_")) for model in OWN_MODELS)
# The models of other modules this one extends, but res.config.settings, which a holder may extend still.
EXTENDED_MODEL_RECORDS = tuple(
    ("ir.model", "model_" + model.replace(".", "_"))
    for model in ("ai.conversation", "ir.http", "mail.message", "mail.thread", "queue.job")
)
# Of the Many2one fields this module adds to models of other modules: Odoo registers a foreign key only when
# it creates it.
ADOPTED_FOREIGN_KEYS = ("ai_conversation_user_id_fkey",)
ADOPTED_RECORDS = (
    ("res.groups", "group_ai_assistant"),
    ("ir.model.access", "access_ai_assistant_tool_call_system"),
    ("ir.model.access", "access_ai_assistant_exchange_system"),
    ("ir.model.access", "access_ai_conversation_ai_assistant"),
    ("ir.model.access", "access_ai_report_ai_assistant"),
    ("ir.model.access", "access_ai_report_system"),
    ("ir.model.access", "access_ai_report_share_log_ai_assistant"),
    ("ir.model.access", "access_ai_report_share_log_system"),
    ("ir.model.access", "access_ai_report_share_ai_assistant"),
    ("ir.rule", "ai_conversation_rule_user"),
    ("ir.rule", "ai_conversation_rule_system"),
    ("ir.rule", "ai_report_rule_user"),
    ("ir.rule", "ai_report_rule_owner"),
    ("ir.rule", "ai_report_rule_system"),
    ("ir.rule", "ai_report_share_log_rule_user"),
    ("ir.rule", "ai_report_share_log_rule_system"),
    ("ir.rule", "ai_report_share_rule_user"),
    ("ir.config_parameter", "config_claude_models"),
    ("ir.config_parameter", "config_claude_effort"),
    ("ir.config_parameter", "config_claude_max_tokens"),
    ("ir.config_parameter", "config_claude_timeout"),
    ("ir.config_parameter", "config_claude_key_env"),
    ("ir.config_parameter", "config_claude_max_tool_rounds"),
    ("ir.config_parameter", "config_claude_tool_budget"),
    ("ir.config_parameter", "config_max_rows"),
    ("ir.config_parameter", "config_tool_seconds"),
    ("ir.config_parameter", "config_deny_models_extra"),
    ("ir.config_parameter", "config_deny_fields_extra"),
    ("ir.config_parameter", "config_report_max_tokens"),
    ("ir.config_parameter", "config_incident_emails"),
    ("ir.config_parameter", "config_digest_recipients"),
    ("ir.config_parameter", "config_pending_stale"),
    ("ir.config_parameter", "config_claude_consecutive_failures"),
    ("ir.config_parameter", "config_claude_circuit_open_until"),
    ("mail.message.subtype", "mt_ask_ai"),
    ("res.partner", "partner_ai_assistant"),
    ("queue.job.channel", "queue_job_channel_ai_assistant"),
    ("queue.job.function", "queue_job_function_ask_ai_answer"),
    ("queue.job.function", "queue_job_function_app_answer"),
    ("ir.cron", "cron_close_stale_answers"),
    ("ir.cron", "cron_weekly_digest"),
    # Odoo's own external ids of the crons' server actions, which ir.cron inherits.
    ("ir.actions.server", "cron_close_stale_answers_ir_actions_server"),
    ("ir.actions.server", "cron_weekly_digest_ir_actions_server"),
    ("ir.actions.report", "ai_report_action_xlsx"),
    ("ir.ui.view", "ai_assistant_tool_call_view_form"),
    ("ir.ui.view", "ai_assistant_tool_call_view_kanban"),
    ("ir.ui.view", "ai_assistant_tool_call_view_search"),
    ("ir.ui.view", "ai_assistant_tool_call_view_tree"),
    ("ir.ui.view", "ai_assistant_exchange_view_form"),
    ("ir.ui.view", "ai_assistant_exchange_view_kanban"),
    ("ir.ui.view", "ai_assistant_exchange_view_search"),
    ("ir.ui.view", "ai_assistant_exchange_view_tree"),
    ("ir.ui.view", "ai_conversation_view_form"),
    ("ir.ui.view", "ai_conversation_view_tree"),
    ("ir.ui.view", "ai_conversation_view_search"),
    ("ir.ui.view", "ai_report_view_form"),
    ("ir.ui.view", "ai_report_view_kanban"),
    ("ir.ui.view", "ai_report_view_search"),
    ("ir.ui.view", "ai_report_view_tree"),
    ("ir.ui.view", "ai_report_share_log_view_tree"),
    ("ir.ui.view", "ai_report_share_view_form"),
    ("ir.ui.view", "res_config_settings_view_form"),
    ("ir.actions.act_window", "ai_assistant_tool_call_action"),
    ("ir.actions.act_window", "ai_assistant_exchange_action"),
    ("ir.actions.act_window", "ai_assistant_exchange_action_to_review"),
    ("ir.actions.act_window", "ai_report_action"),
    ("ir.actions.act_window", "res_config_settings_action_ai_assistant"),
    ("ir.actions.client", "ai_assistant_app_action"),
    ("ir.ui.menu", "ai_assistant_app_menu"),
    ("ir.ui.menu", "ai_assistant_app_menu_assistant"),
    ("ir.ui.menu", "ai_assistant_app_menu_review"),
    ("ir.ui.menu", "ai_assistant_app_menu_to_review"),
    ("ir.ui.menu", "ai_assistant_app_menu_conversations"),
    ("ir.ui.menu", "ai_assistant_app_menu_assistant_answers"),
    ("ir.ui.menu", "ai_assistant_app_menu_tool_calls"),
    ("ir.ui.menu", "ai_assistant_app_menu_reports"),
    ("ir.ui.menu", "ai_assistant_app_menu_configuration"),
    ("ir.ui.menu", "ai_assistant_app_menu_settings"),
)

# The settings of the AI Assistant are named by the module that holds it: those of a holder take this
# module's name, unless the name is taken already.
RENAME_PARAMETERS = """
    UPDATE ir_config_parameter param
       SET key = %(module)s || substr(param.key, length(%(holder)s) + 1)
     WHERE left(param.key, length(%(holder)s) + 1) = %(holder)s || '.'
       AND NOT EXISTS (
           SELECT 1 FROM ir_config_parameter taken
            WHERE taken.key = %(module)s || substr(param.key, length(%(holder)s) + 1)
       )
"""
SETTINGS_FILE = "data/ir_config_parameter_data.xml"
# The data file loaded at install creates the key of a setting that has no external id here: a row that has
# the key already takes the external id, which the holder's row may have lost when it was emptied.
ATTACH_SETTING = """
    INSERT INTO ir_model_data (module, name, model, res_id, noupdate, create_uid, write_uid, create_date, write_date)
    SELECT %(module)s, %(name)s, 'ir.config_parameter', param.id, true, 1, 1,
           now() at time zone 'UTC', now() at time zone 'UTC'
      FROM ir_config_parameter param
     WHERE param.key = %(key)s
    ON CONFLICT (module, name) DO UPDATE SET res_id = EXCLUDED.res_id, noupdate = true
"""
REPORT_MODEL = "report.%s.ai_report_xlsx"
RENAME_REPORT_MODEL = """
    UPDATE ir_model SET model = %(new_model)s
     WHERE model = %(old_model)s
       AND NOT EXISTS (SELECT 1 FROM ir_model taken WHERE taken.model = %(new_model)s)
 RETURNING id
"""
RENAME_REPORT_MODEL_FIELDS = "UPDATE ir_model_fields SET model = %(new_model)s WHERE model_id = %(model_id)s"
RENAME_REPORT_MODEL_ID = """
    UPDATE ir_model_data SET module = %(module)s, name = 'model_' || %(new_ref)s
     WHERE module = %(holder)s AND model = 'ir.model' AND res_id = %(model_id)s
"""
RENAME_REPORT_FIELD_IDS = """
    UPDATE ir_model_data imd SET module = %(module)s, name = 'field_' || %(new_ref)s || '__' || field.name
      FROM ir_model_fields field
     WHERE imd.module = %(holder)s
       AND imd.model = 'ir.model.fields'
       AND imd.res_id = field.id
       AND field.model_id = %(model_id)s
"""
KEPT_ROWS_TABLE = "ai_assistant_kept_rows"
CREATE_KEPT_ROWS = (
    "CREATE TEMP TABLE IF NOT EXISTS %s (model varchar, res_id integer, row_values jsonb, PRIMARY KEY (model, res_id))"
    " ON COMMIT DROP" % KEPT_ROWS_TABLE
)
# The forcecreate="0" settings the holder no longer has, which the data file creates at install though not at -u.
ABSENT_PARAMETERS_TABLE = "ai_assistant_absent_parameters"
CREATE_ABSENT_PARAMETERS = (
    "CREATE TEMP TABLE IF NOT EXISTS %s (name varchar PRIMARY KEY) ON COMMIT DROP" % ABSENT_PARAMETERS_TABLE
)
KEEP_ABSENT_PARAMETERS = """
    INSERT INTO {absent}
    SELECT adopted.name FROM unnest(%(names)s) adopted(name)
     WHERE NOT EXISTS (
           SELECT 1 FROM ir_model_data imd
            WHERE imd.module = %(module)s AND imd.model = 'ir.config_parameter' AND imd.name = adopted.name
     )
    ON CONFLICT DO NOTHING
"""
# The fields of its models, those of the abstract models they inherit included.
OWN_MODEL_FIELDS = "SELECT model, name FROM ir_model_fields WHERE model IN %(models)s"
ADOPTED_NOUPDATE_RECORDS = """
    SELECT model, res_id FROM ir_model_data WHERE module = %(module)s AND noupdate AND (model, name) IN %(records)s
"""


def pre_init_hook(cr):
    """Hand to this module, before its data files are loaded, the records of the module it was cut from,
    so that they keep their database id (as ADR 0015 of this repository does for the Team Dashboard).

    A holder is a module that has the external id of a model in OWN_MODELS. From it this module takes the
    external ids in ADOPTED_RECORDS, those of its models and of the models it extends but
    res.config.settings, of the fields its classes declare and of their selection values (the Ask AI and
    app values of ai.conversation.assistant, whose conversations go with them), the constraints and
    many2many tables of its models and ADOPTED_FOREIGN_KEYS, and points the adopted views at its own files.
    The holder that had records of ADOPTED_RECORDS also hands over its settings, holder.<suffix> becoming
    ai_assistant.<suffix> with their value, and its xlsx report model, under this module's name.

    A data file loaded at install writes its records even where they are noupdate, and creates a setting
    that has no external id: every setting whose key has a row takes its external id on that row, the values
    of the adopted noupdate records are kept here and written back by `post_init_hook`, so the settings,
    crons and job channels stay as they were, and a forcecreate="0" setting the holder had removed is
    removed again.

    A database where no other module holds the models is left untouched, and a second run finds nothing
    to move.
    """
    holders = find_holders(cr, MODULE, MODEL_RECORDS)
    if not holders:
        return
    cr.execute(OWN_MODEL_FIELDS, {"models": OWN_MODELS})
    fields = tuple({*declared_fields(MODULE), *cr.fetchall()})
    selections = declared_selections(MODULE)
    owners = []
    for holder in holders:
        adopted = adopt_records(
            cr,
            holder,
            MODULE,
            ADOPTED_RECORDS + MODEL_RECORDS + EXTENDED_MODEL_RECORDS,
            fields=fields,
            selections=selections,
            model_names=OWN_MODELS,
            foreign_keys=ADOPTED_FOREIGN_KEYS,
        )
        if adopted & set(ADOPTED_RECORDS):
            owners.append(holder)
            cr.execute(RENAME_PARAMETERS, {"module": MODULE, "holder": holder})
            _rename_report_model(cr, holder)
    if owners:
        settings = data_settings()
        for name, (key, _is_forcecreate) in settings.items():
            cr.execute(ATTACH_SETTING, {"module": MODULE, "name": name, "key": key})
        _keep_noupdate_rows(cr, [name for name, (_key, is_forcecreate) in settings.items() if not is_forcecreate])
    _logger.info("%s adopted the records of %s", MODULE, ", ".join(holders))
    # external ids and parameters are cached per registry, and these changed behind the ORM
    api.Environment(cr, SUPERUSER_ID, {}).registry.clear_caches()


def data_settings():
    """Return {external id name: (key, forcecreate)} of the settings the data file declares."""
    with file_open(f"{MODULE}/{SETTINGS_FILE}", "rb") as settings_file:
        root = etree.parse(settings_file).getroot()
    return {
        record.get("id"): (record.findtext("field[@name='key']"), nodeattr2bool(record, "forcecreate", True))
        for record in root.iter("record")
    }


def post_init_hook(cr, registry):
    """Write back the values `pre_init_hook` kept of the adopted noupdate records, and remove the settings the
    holder did not have."""
    cr.execute("SELECT to_regclass(%s)", ("pg_temp.%s" % KEPT_ROWS_TABLE,))
    if not cr.fetchone()[0]:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("SELECT name FROM %s ORDER BY name" % ABSENT_PARAMETERS_TABLE)
    for (name,) in cr.fetchall():
        parameter = env.ref(f"{MODULE}.{name}", raise_if_not_found=False)
        if parameter:
            parameter.unlink()
    cr.execute("DELETE FROM %s" % ABSENT_PARAMETERS_TABLE)
    env.flush_all()
    cr.execute("SELECT model, res_id, row_values FROM %s ORDER BY model, res_id" % KEPT_ROWS_TABLE)
    for model_name, res_id, row_values in cr.fetchall():
        table = env[model_name]._table
        columns = sql.SQL(", ").join(sql.Identifier(column) for column in row_values if column != "id")
        cr.execute(
            sql.SQL(
                "UPDATE {table} SET ({columns}) = (SELECT {columns} FROM jsonb_populate_record(NULL::{table}, %s))"
                " WHERE id = %s"
            ).format(table=sql.Identifier(table), columns=columns),
            (Json(row_values), res_id),
        )
    cr.execute("DELETE FROM %s" % KEPT_ROWS_TABLE)
    env.invalidate_all()
    env.registry.clear_caches()


def _rename_report_model(cr, holder):
    """Give the xlsx report model of `holder`, its fields and their external ids this module's name, unless
    the model of that name exists."""
    new_model = REPORT_MODEL % MODULE
    cr.execute(RENAME_REPORT_MODEL, {"old_model": REPORT_MODEL % holder, "new_model": new_model})
    renamed = cr.fetchone()
    if not renamed:
        return
    params = {
        "module": MODULE,
        "holder": holder,
        "model_id": renamed[0],
        "new_model": new_model,
        "new_ref": new_model.replace(".", "_"),
    }
    cr.execute(RENAME_REPORT_MODEL_FIELDS, params)
    cr.execute(RENAME_REPORT_MODEL_ID, params)
    cr.execute(RENAME_REPORT_FIELD_IDS, params)


def _keep_noupdate_rows(cr, forcecreate_off_names):
    """Keep in KEPT_ROWS_TABLE, once, the rows of the adopted records that their external id marks noupdate, and
    in ABSENT_PARAMETERS_TABLE the settings of `forcecreate_off_names` that have no external id here."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute(CREATE_ABSENT_PARAMETERS)
    cr.execute(
        sql.SQL(KEEP_ABSENT_PARAMETERS).format(absent=sql.Identifier(ABSENT_PARAMETERS_TABLE)),
        {"names": list(forcecreate_off_names), "module": MODULE},
    )
    cr.execute(CREATE_KEPT_ROWS)
    cr.execute(ADOPTED_NOUPDATE_RECORDS, {"module": MODULE, "records": ADOPTED_RECORDS})
    res_ids_by_model = {}
    for model_name, res_id in cr.fetchall():
        res_ids_by_model.setdefault(model_name, []).append(res_id)
    for model_name, res_ids in res_ids_by_model.items():
        cr.execute(
            sql.SQL(
                "INSERT INTO {kept} SELECT %s, source.id, to_jsonb(source) FROM {table} source WHERE source.id IN %s"
                " ON CONFLICT DO NOTHING"
            ).format(kept=sql.Identifier(KEPT_ROWS_TABLE), table=sql.Identifier(env[model_name]._table)),
            (model_name, tuple(res_ids)),
        )
