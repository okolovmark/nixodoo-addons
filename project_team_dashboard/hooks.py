import logging
from collections import defaultdict

from psycopg2 import sql

from odoo import SUPERUSER_ID, api, models

from .models.project_task import local_date

_logger = logging.getLogger(__name__)

MODULE = "project_team_dashboard"
GLUE_MODULE = "project_team_dashboard_timeline"

ADOPTED_MODELS = ("project.baseline", "project.baseline.line", "project.task.dependency.lag")
MODEL_RECORDS = tuple(("ir.model", "model_" + model.replace(".", "_")) for model in ADOPTED_MODELS)
# Of the Many2one fields this module adds to models of other modules: Odoo registers a foreign key
# only when it creates it.
ADOPTED_FOREIGN_KEYS = ("project_project_baseline_id_fkey", "res_users_dashboard_default_project_id_fkey")
ADOPTED_RECORDS = (
    ("ir.model.constraint", "constraint_project_task_dependency_lag_task_depends_on_uniq"),
    ("ir.model.access", "access_project_baseline_manager"),
    ("ir.model.access", "access_project_baseline_line_manager"),
    ("ir.model.access", "access_project_task_dependency_lag_manager"),
    ("ir.rule", "project_task_dependency_lag_rule_company"),
    ("ir.rule", "project_baseline_rule_company"),
    ("ir.rule", "project_baseline_line_rule_company"),
    ("ir.ui.view", "project_task_type_view_form_inherit"),
    ("ir.ui.view", "project_task_backlog_view_tree"),
    ("ir.ui.view", "project_task_backlog_view_search"),
    ("ir.ui.view", "res_users_view_form_inherit_dashboard"),
    ("ir.ui.view", "res_users_preferences_form_inherit_dashboard"),
    ("ir.actions.act_window", "action_project_task_backlog"),
    ("ir.actions.act_window.view", "action_project_task_backlog_tree"),
    ("ir.actions.server", "action_open_backlog_server"),
    ("ir.actions.client", "action_team_dashboard"),
    ("ir.ui.menu", "menu_ai_driven_project_backlog"),
    ("ir.ui.menu", "menu_ai_driven_project_dashboard"),
)
# The views of GLUE_MODULE: its own hook would run once this one has taken the models, and could
# no longer tell their holder from a module that has views of the same names.
GLUE_RECORDS = (
    ("ir.ui.view", "project_task_view_form_planned_dates"),
    ("ir.ui.view", "project_task_view_tree_planned_dates"),
)
OWNERSHIP_TABLES = ("ir_model_constraint", "ir_model_relation")

FIND_HOLDERS = """
    SELECT DISTINCT module FROM ir_model_data WHERE (model, name) IN %(records)s AND module != %(module)s
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
 RETURNING imd.model, imd.res_id
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
ADOPT_VIEW_FILE = """
    UPDATE ir_ui_view
       SET arch_fs = %(module)s || substr(arch_fs, length(%(holder)s) + 1)
     WHERE id IN %(view_ids)s
       AND left(arch_fs, length(%(holder)s) + 1) = %(holder)s || '/'
"""
OPEN_AI_DRIVEN_TASKS_WITH_A_PLANNED_END = """
    SELECT task.id, task.planned_date_end, task.date_deadline, author.tz
      FROM project_task task
      JOIN project_project project ON project.id = task.project_id
      LEFT JOIN project_task_type stage ON stage.id = task.stage_id
      LEFT JOIN res_users writer ON writer.id = task.write_uid
      LEFT JOIN res_partner author ON author.id = writer.partner_id
     WHERE project.is_ai_driven
       AND task.planned_date_end IS NOT NULL
       AND stage.fold IS NOT TRUE
"""


def pre_init_hook(cr):
    """Hand to this module, before its data files are loaded, the records another module created
    under the same external ids, so that they keep their database id: left with that module,
    they would be created a second time here and the originals deleted when it is updated.

    The holder is whichever module has the external id of a model in ADOPTED_MODELS: a name in
    ADOPTED_RECORDS alone may be another module's own. From the holder this module takes the
    external ids in ADOPTED_RECORDS, those of its models, of the fields its classes declare,
    the constraints and many2many tables registered for its models, and ADOPTED_FOREIGN_KEYS;
    GLUE_MODULE takes GLUE_RECORDS. A view moves to the file of the same name in its new module:
    a hard reset of the view, or a server in dev mode, reads its arch from that file. Another
    module that holds the same field, as its own definition of it, keeps its external id.

    A database where no other module holds the models is left untouched, and a second run finds
    nothing to move.
    """
    cr.execute(FIND_HOLDERS, {"records": MODEL_RECORDS, "module": MODULE})
    holders = [holder for (holder,) in cr.fetchall()]
    if not holders:
        return
    fields = declared_fields(MODULE)
    for holder in holders:
        _adopt_records(
            cr, holder, MODULE, ADOPTED_RECORDS + MODEL_RECORDS, fields, ADOPTED_MODELS, ADOPTED_FOREIGN_KEYS
        )
        _adopt_records(cr, holder, GLUE_MODULE, GLUE_RECORDS)
    _logger.info("%s adopted the records of %s", MODULE, ", ".join(holders))
    # external ids are cached per registry, and these changed behind the ORM
    api.Environment(cr, SUPERUSER_ID, {}).registry.clear_caches()


def post_init_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    roll_up_planned_dates(env)
    align_deadlines_with_planned_ends(env)


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


def _adopt_records(cr, holder, module, records, fields=(), model_names=(), foreign_keys=()):
    params = {
        "module": module,
        "holder": holder,
        "records": records,
        "fields": fields,
        "models": model_names or ("",),
        "foreign_keys": foreign_keys or ("",),
    }
    cr.execute(ADOPT_RECORDS, params)
    view_ids = tuple(res_id for model, res_id in cr.fetchall() if model == "ir.ui.view")
    if fields:
        cr.execute(ADOPT_FIELDS, params)
    if model_names or foreign_keys:
        for table in OWNERSHIP_TABLES:
            cr.execute(ADOPT_OWNERSHIP.format(table=sql.Identifier(table)), params)
    if view_ids:
        cr.execute(ADOPT_VIEW_FILE, dict(params, view_ids=view_ids))


def roll_up_planned_dates(env):
    """Roll the planned dates of every task with sub-tasks up from them.

    The roll-up only runs when a sub-task changes, so the tasks that have sub-tasks when the
    module is installed keep the dates they were given by hand until it is recomputed here.
    Parents are recomputed deepest first, so a milestone reads tasks already rolled up.
    """
    Task = env["project.task"]
    candidate_ids = set()
    for subtask in Task.search([("parent_id", "!=", False)]):
        ancestor = subtask.parent_id
        while ancestor and ancestor.id not in candidate_ids:
            candidate_ids.add(ancestor.id)
            ancestor = ancestor.parent_id
    candidate_ids_by_depth = defaultdict(list)
    for task in Task.browse(candidate_ids):
        candidate_ids_by_depth[_depth(task)].append(task.id)
    rolled_up_count = 0
    for depth in sorted(candidate_ids_by_depth, reverse=True):
        parents = Task.browse(candidate_ids_by_depth[depth]).filtered("child_ids")
        for field_name in ("planned_date_start", "planned_date_end"):
            env.add_to_compute(Task._fields[field_name], parents)
        env.flush_all()
        rolled_up_count += len(parents)
    _logger.info("Planned dates rolled up from sub-tasks on %s tasks", rolled_up_count)


def align_deadlines_with_planned_ends(env):
    """Set the Deadline of the open tasks of AI-driven projects to the date of their planned end,
    in the time zone of the user who last wrote each task. In SQL, so their write dates stay."""
    env.flush_all()
    env.cr.execute(OPEN_AI_DRIVEN_TASKS_WITH_A_PLANNED_END)
    task_ids_by_deadline = defaultdict(list)
    for task_id, planned_date_end, date_deadline, tz_name in env.cr.fetchall():
        deadline = local_date(planned_date_end, tz_name)
        if deadline != date_deadline:
            task_ids_by_deadline[deadline].append(task_id)
    for deadline, task_ids in task_ids_by_deadline.items():
        env.cr.execute("UPDATE project_task SET date_deadline = %s WHERE id IN %s", (deadline, tuple(task_ids)))
    env["project.task"].invalidate_model(["date_deadline"])
    _logger.info(
        "Deadline aligned with the planned end on %s open tasks of AI-driven projects",
        sum(len(task_ids) for task_ids in task_ids_by_deadline.values()),
    )


def _depth(task):
    depth = 0
    while task.parent_id:
        task = task.parent_id
        depth += 1
    return depth
