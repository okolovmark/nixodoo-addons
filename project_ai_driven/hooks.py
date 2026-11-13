import logging
import re
from collections import defaultdict

from lxml import etree
from psycopg2 import sql
from psycopg2.extras import Json

from odoo import SUPERUSER_ID, api
from odoo.tools.sql import column_exists, rename_column

from .models.project_pull_request import parse_pull_request_links

_logger = logging.getLogger(__name__)

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
    ("project.task", "testcase_general_comments"),
)
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
# Deleted from this module since. A database from before still holds them: taken over like the
# rest, they go at the end of the update, as records this module no longer has.
DROPPED_FIELDS = (
    ("project.task", "difficulty"),
    ("project.task", "knowledge_area_level"),
    ("project.task", "tester_id"),
)
DROPPED_FOREIGN_KEYS = ("project_task_tester_id_fkey",)

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

MODULE_VIEWS = """
    SELECT view.id, view.model, view.arch_db
      FROM ir_ui_view view
      JOIN ir_model_data imd ON imd.model = 'ir.ui.view' AND imd.res_id = view.id
     WHERE imd.module = %(module)s
       AND view.model IN %(models)s
       AND view.arch_db IS NOT NULL
"""
FORGET_FOREIGN_KEYS = """
    DELETE FROM ir_model_constraint con
     USING ir_module_module holder
     WHERE holder.id = con.module
       AND holder.name = %(module)s
       AND con.type = 'f'
       AND con.name IN %(foreign_keys)s
"""

ONE_LINK = "one pull request link"
SEVERAL_LINKS = "several pull request links"
LINK_WITHOUT_SCHEME = "pull request link without a scheme"
LINK_IN_TEXT = "pull request link with something before or after it"
OTHER_LINK = "link that is not a pull request"
MEMORY_REFERENCE = "memory:// reference"
OTHER_TEXT = "other text"
OLD_VALUE_KINDS = (ONE_LINK, SEVERAL_LINKS, LINK_WITHOUT_SCHEME, LINK_IN_TEXT, OTHER_LINK, MEMORY_REFERENCE, OTHER_TEXT)
# A value the ORM wrote reads the task's first pull request: only text from before does not.
OLD_PR_URI_VALUES = """
    SELECT task.id, task.pr_uri
      FROM project_task task
     WHERE task.pr_uri != ''
       AND task.pr_uri IS DISTINCT FROM (
               SELECT pull_request.url
                 FROM project_pull_request_task_rel rel
                 JOIN project_pull_request pull_request ON pull_request.id = rel.pull_request_id
                WHERE rel.task_id = task.id
             ORDER BY pull_request.id
                LIMIT 1
           )
  ORDER BY task.id
"""
LINK_PULL_REQUESTS = """
    INSERT INTO project_pull_request_task_rel (task_id, pull_request_id)
    SELECT * FROM unnest(%s::integer[], %s::integer[])
        ON CONFLICT DO NOTHING
"""
CONVERT_TASKS = """
    UPDATE project_task task
       SET pr_uri = (
               SELECT pull_request.url
                 FROM project_pull_request_task_rel rel
                 JOIN project_pull_request pull_request ON pull_request.id = rel.pull_request_id
                WHERE rel.task_id = task.id
             ORDER BY pull_request.id
                LIMIT 1
           ),
           pr_legacy = NULLIF(concat_ws(E'\\n', NULLIF(task.pr_legacy, ''), old_value.kept), ''),
           resolution = CASE WHEN old_value.has_link THEN COALESCE(task.resolution, 'code') ELSE task.resolution END
      FROM unnest(%s::integer[], %s::varchar[], %s::boolean[]) AS old_value(task_id, kept, has_link)
     WHERE task.id = old_value.task_id
"""

# The cases of fill_stage_log, in the words of its log line.
PAST_CASES = {
    "tracked_task": "tasks with stage changes in the chatter",
    "tracked_start": "stages they were created in",
    "tracked_move": "stage changes",
    "untracked_end": "current stages the chatter does not end at",
    "undated_end": "such stages left out for want of a later stage date",
    "stage_date": "stages of the other tasks from their last stage update",
    "creation": "stages of the other tasks from their creation",
    "undated": "rows left out for want of a date",
}
# One statement: every part reads the log as it was before the first row went in.
FILL_STAGE_LOG = """
    WITH unlogged_task AS (
        SELECT task.id, task.stage_id, task.create_date, task.create_uid, task.date_last_stage_update
          FROM project_task task
         WHERE NOT EXISTS (SELECT 1 FROM project_task_stage_log log WHERE log.task_id = task.id)
    ),
    move AS (
        SELECT task.id AS task_id,
               task.stage_id AS current_stage_id,
               task.create_date,
               task.create_uid,
               task.date_last_stage_update,
               message.date,
               message.author_id,
               NULLIF(tracking.old_value_integer, 0) AS old_stage_id,
               NULLIF(tracking.new_value_integer, 0) AS new_stage_id,
               NULLIF(tracking.old_value_char, '') AS old_stage_name,
               NULLIF(tracking.new_value_char, '') AS new_stage_name,
               row_number() OVER task_moves AS position,
               count(*) OVER (PARTITION BY task.id) AS move_count
          FROM mail_tracking_value tracking
          JOIN mail_message message ON message.id = tracking.mail_message_id
          JOIN unlogged_task task ON task.id = message.res_id
         WHERE tracking.field = %(stage_field_id)s
           AND message.model = 'project.task'
           AND message.subtype_id IS DISTINCT FROM %(dependency_subtype_id)s
        WINDOW task_moves AS (PARTITION BY task.id ORDER BY message.date, message.id, tracking.id)
    ),
    untracked_task AS (
        SELECT task.*,
               stage.name->>'en_US' AS stage_name,
               COALESCE(
                   task.date_last_stage_update >= task.create_date + interval '2 seconds', FALSE
               ) AS has_stage_date
          FROM unlogged_task task
          JOIN project_task_type stage ON stage.id = task.stage_id
         WHERE NOT EXISTS (SELECT 1 FROM move WHERE move.task_id = task.id)
    ),
    past AS (
        -- a message is dated to the second: a change in the second of the creation would come before it.
        -- LEAST skips a NULL, and a task without a creation date gives its first stage none
        SELECT 'tracked_start' AS past_case, move.task_id, 0 AS position, NULL::integer AS previous_stage_id,
               stage.id AS stage_id, move.old_stage_name AS stage_name,
               CASE WHEN move.create_date IS NOT NULL THEN LEAST(move.create_date, move.date) END AS date,
               move.create_uid AS user_id, 'tracking' AS source
          FROM move
     LEFT JOIN project_task_type stage ON stage.id = move.old_stage_id
         WHERE move.position = 1
           AND move.old_stage_id IS NOT NULL
     UNION ALL
        SELECT 'tracked_move', move.task_id, move.position, previous_stage.id,
               stage.id, move.new_stage_name, move.date,
               (SELECT min(author.id) FROM res_users author WHERE author.partner_id = move.author_id),
               'tracking'
          FROM move
     LEFT JOIN project_task_type stage ON stage.id = move.new_stage_id
     LEFT JOIN project_task_type previous_stage ON previous_stage.id = move.old_stage_id
     UNION ALL
        SELECT 'untracked_end', move.task_id, move.position + 1, NULL,
               stage.id, stage.name->>'en_US', move.date_last_stage_update, NULL, 'stage_date'
          FROM move
     LEFT JOIN project_task_type stage ON stage.id = move.current_stage_id
         WHERE move.position = move.move_count
           AND move.current_stage_id IS DISTINCT FROM move.new_stage_id
           AND move.date_last_stage_update >= move.date
     UNION ALL
        SELECT 'stage_date', task.id, 0, NULL,
               task.stage_id, task.stage_name, task.date_last_stage_update, NULL, 'stage_date'
          FROM untracked_task task
         WHERE task.has_stage_date
     UNION ALL
        SELECT 'creation', task.id, 0, NULL,
               task.stage_id, task.stage_name, task.create_date, task.create_uid, 'creation'
          FROM untracked_task task
         WHERE NOT task.has_stage_date
    ),
    -- a row needs a date: a task or a message without one, as an import by SQL can leave, gives none
    dated_past AS (
        SELECT * FROM past WHERE date IS NOT NULL
    ),
    inserted AS (
        INSERT INTO project_task_stage_log
                    (task_id, previous_stage_id, stage_id, stage_name, date, user_id, source,
                     create_uid, create_date, write_uid, write_date)
             SELECT task_id, previous_stage_id, stage_id, stage_name, date, user_id, source,
                    %(uid)s, %(now)s, %(uid)s, %(now)s
               FROM dated_past
           ORDER BY task_id, position
    )
    SELECT past_case, count(*) FROM dated_past GROUP BY past_case
     UNION ALL
    SELECT 'tracked_task', count(DISTINCT task_id) FROM dated_past WHERE past_case = 'tracked_move'
     UNION ALL
    SELECT 'undated_end', count(*)
      FROM move
     WHERE position = move_count
       AND current_stage_id IS DISTINCT FROM new_stage_id
       AND (date_last_stage_update >= date) IS NOT TRUE
     UNION ALL
    SELECT 'undated', count(*) FROM past WHERE date IS NULL
"""


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

    The database is then where an update of this module finds it, the fields
    deleted since included: see forget_dropped_fields.
    """
    if is_installed(cr, FORMER_MODULE):
        take_over_module(cr, FORMER_MODULE, MODULE)
    elif is_installed(cr, LEGACY_MODULE):
        _take_over_legacy_records(cr)
    else:
        return
    _rename_flag(cr)
    rename_external_ids(cr, MODULE, RENAMED_EXTERNAL_IDS)
    forget_dropped_fields(cr)
    # external ids are cached per registry, and these changed behind the ORM
    api.Environment(cr, SUPERUSER_ID, {}).registry.clear_caches()


def post_init_hook(cr, registry):
    """Migration scripts do not run on an install, and the column of pr_uri
    may hold the values project_task_pull_request stored in it. The tasks
    that exist already have a past to log, too.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    convert_old_pr_uri_values(env)
    fill_stage_log(env)


def fill_stage_log(env):
    """Log the past of the tasks that have no row in the stage log, archived
    ones included, and return the figure of each case of PAST_CASES: the rows
    inserted, the tasks with tracked changes they belong to, and what was left
    out.

    A task whose chatter tracked its stage gets the stage it was created in,
    which is where its first tracked change starts from, and one row per
    tracked change, as tracked: a change that does not start where the one
    before ended stays so. The first stage is dated at the creation of the
    task, or at the first tracked change when that is dated earlier. When the
    last change does not end at the stage the task is in now, the changes
    after it were not tracked, and that stage is logged at the task's last
    stage update, unless that date is missing or earlier than the last tracked
    change.

    A stage change a predecessor posted on the task, with the subtype of a
    dependency change, is not a change of the task.

    Any other task that is in a stage gets that stage alone: at its last stage
    update when that is 2 seconds or more after its creation, else at its
    creation and by its creator. The creation date is the start of the
    transaction that created the task, and the last stage update the clock
    when its stage was last written, its creation included. A task created 2
    seconds or more into a long transaction is thus logged at its last stage
    update and without a user, though its stage never changed.

    A row needs a date: one that has none is left out and counted.

    The rows are inserted by SQL and nothing is written on the tasks. A second
    run inserts nothing: the tasks it still finds without a row are those that
    have no stage and no tracked change, and those whose rows all lacked a
    date, which it counts again.
    """
    cr = env.cr
    env.flush_all()
    dependency_subtype_id = env["ir.model.data"]._xmlid_to_res_id(
        "project.mt_task_dependency_change", raise_if_not_found=False
    )
    cr.execute(
        FILL_STAGE_LOG,
        {
            "stage_field_id": env["ir.model.fields"]._get("project.task", "stage_id").id,
            # 0 names no subtype: compared with NULL, the messages that have none would drop out
            "dependency_subtype_id": dependency_subtype_id or 0,
            "uid": env.uid,
            "now": cr.now(),
        },
    )
    counts = dict.fromkeys(PAST_CASES, 0)
    counts.update(cr.fetchall())
    _logger.info(
        "Stage log filled from the past: %s",
        ", ".join("%d %s" % (counts[past_case], label) for past_case, label in PAST_CASES.items()),
    )
    return counts


def convert_old_pr_uri_values(env):
    """Turn the free text project_task_pull_request stored in pr_uri into pull
    request records, and return the number of values of each kind.

    Every pull request link in a value becomes a record linked to the task,
    created without a state unless one with that link exists, and the task
    gets the Resolution Code unless it has one.
    A value of any kind but ONE_LINK is kept whole in pr_legacy; one that holds
    no pull request link creates no record and leaves the Resolution alone.

    The task rows are written by SQL: they are not edits of the tasks, so no
    write date, no tracking message and no constraint of another module. A
    second run finds nothing left to convert.
    """
    cr = env.cr
    env.flush_all()
    cr.execute(OLD_PR_URI_VALUES)
    old_values = []
    counts = dict.fromkeys(OLD_VALUE_KINDS, 0)
    for task_id, value in cr.fetchall():
        links = parse_pull_request_links(value)
        kind = _get_old_value_kind(value, links)
        old_values.append((task_id, None if kind == ONE_LINK else value, links))
        counts[kind] += 1

    urls = list(dict.fromkeys(link.url for _task_id, _kept_value, links in old_values for link in links))
    pull_requests = env["project.pull.request"].search([("url", "in", urls)])
    known_urls = set(pull_requests.mapped("url"))
    # the default state would say Open, which the old text does not
    created_pull_requests = pull_requests.create(
        [{"url": url, "state": False} for url in urls if url not in known_urls]
    )
    env.flush_all()
    pull_request_ids = {record.url: record.id for record in pull_requests | created_pull_requests}

    task_links = [(task_id, pull_request_ids[link.url]) for task_id, _kept_value, links in old_values for link in links]
    if task_links:
        cr.execute(LINK_PULL_REQUESTS, [list(column) for column in zip(*task_links)])
    if old_values:
        cr.execute(
            CONVERT_TASKS,
            (
                [task_id for task_id, _kept_value, _links in old_values],
                [kept_value for _task_id, kept_value, _links in old_values],
                [bool(links) for _task_id, _kept_value, links in old_values],
            ),
        )
    env.invalidate_all()
    _logger.info(
        "Old pr_uri values converted: %s; %d pull requests created, %d links to tasks",
        ", ".join("%d %s" % (count, kind) for kind, count in counts.items()),
        len(created_pull_requests),
        len(task_links),
    )
    return counts


def _get_old_value_kind(value, links):
    value_text = value.strip()
    if len(links) > 1:
        return SEVERAL_LINKS
    if links:
        has_scheme = "://" in links[0].source
        # as written: an upper-case scheme or host and a closing slash say nothing more than the link
        if has_scheme and value_text.rstrip("/") == links[0].source:
            return ONE_LINK
        return LINK_IN_TEXT if has_scheme else LINK_WITHOUT_SCHEME
    if value_text.startswith("memory://"):
        return MEMORY_REFERENCE
    return OTHER_LINK if re.fullmatch(r"\w[\w+.-]*://\S+", value_text) else OTHER_TEXT


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

    The fields in DROPPED_FIELDS and their foreign keys are adopted too. Left
    with the legacy module, whether they go would depend on what replaces it.
    """
    params = {
        "module": MODULE,
        "legacy": LEGACY_MODULE,
        "models": ADOPTED_MODELS,
        "fields": ADOPTED_FIELDS + DROPPED_FIELDS,
        "records": ADOPTED_RECORDS,
        "foreign_keys": DROPPED_FOREIGN_KEYS,
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


def forget_dropped_fields(cr):
    """Clear what the database stores about the fields in DROPPED_FIELDS and
    an update of this module trips on or leaves behind, before its data files
    are loaded.

    The update deletes the fields themselves, with their columns, at its end:
    they are records of this module that it no longer has. Until then the
    stored arch of a view of this module still names them, and it is validated
    against the fields of the model as soon as another view of the same form is
    loaded, before its own data file rewrites it. Their nodes are taken out of
    every translation of the arch; emptying the arch, as for the views in
    SPLIT_VIEWS, would lose the translated terms the database alone holds.

    A foreign key goes with its column, but the row that registers it to this
    module would stay.

    A second run finds nothing left.
    """
    field_names = defaultdict(set)
    for model, name in DROPPED_FIELDS:
        field_names[model].add(name)
    cr.execute(MODULE_VIEWS, {"module": MODULE, "models": tuple(field_names)})
    cleared_view_ids = []
    for view_id, model, archs in cr.fetchall():
        cleared_archs = {lang: _remove_field_nodes(arch, field_names[model]) for lang, arch in archs.items()}
        if cleared_archs != archs:
            cr.execute("UPDATE ir_ui_view SET arch_db = %s WHERE id = %s", (Json(cleared_archs), view_id))
            cleared_view_ids.append(view_id)
    cr.execute(FORGET_FOREIGN_KEYS, {"module": MODULE, "foreign_keys": DROPPED_FOREIGN_KEYS})
    forgotten_key_count = cr.rowcount
    if cleared_view_ids or forgotten_key_count:
        _logger.info(
            "Dropped fields forgotten: taken out of %d views, %d foreign key registrations deleted",
            len(cleared_view_ids),
            forgotten_key_count,
        )


def _remove_field_nodes(arch, field_names):
    """Return arch without the nodes of the fields in field_names.

    A field inside a field is left alone: in the arches this module ships it
    stands in the sub-view of a relation, a field of another model. An
    extension written as <field name="..." position="..."> is not told apart
    from that.
    """
    root = etree.fromstring(arch)
    nodes = [
        node
        for node in root.iterdescendants("field")
        if node.get("name") in field_names and next(node.iterancestors("field"), None) is None
    ]
    for node in nodes:
        node.getparent().remove(node)
    return etree.tostring(root, encoding="unicode") if nodes else arch


def rename_external_ids(cr, module, renamed_names):
    """Rename external ids of module, {former name: new name}. A new name that
    is already taken leaves the former one in place.
    """
    for former_name, new_name in renamed_names.items():
        cr.execute(RENAME_EXTERNAL_ID, {"module": module, "former_name": former_name, "new_name": new_name})
