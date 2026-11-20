import copy
import html
import logging
import re
from collections import defaultdict

from lxml import etree
from lxml import html as lxml_html
from psycopg2 import sql
from psycopg2.extras import Json

from odoo import SUPERUSER_ID, api
from odoo.tools.sql import column_exists, create_column, create_index, table_exists

from .models.mail_message import NOTE_CATEGORIES, NOTE_CATEGORY_PATTERNS, NOTE_MODEL, NOTE_SUBTYPE, SPAN_OPENING
from .models.project_pull_request import parse_pull_request_links
from .models.project_task_question import get_visible_text, has_content, has_media, is_blank_html, parse_html

_logger = logging.getLogger(__name__)

MODULE = "project_ai_driven"

FIELD_NAMING_COLUMNS = (
    ("ir_rule", "domain_force"),
    ("ir_filters", "domain"),
    ("ir_filters", "context"),
    ("ir_filters", "sort"),
    ("ir_act_window", "domain"),
    ("ir_act_window", "context"),
)

# Deleted from this module since: the update deletes them at its end, as records this module no
# longer has.
DROPPED_FIELDS = (
    ("project.task", "difficulty"),
    ("project.task", "knowledge_area_level"),
    ("project.task", "tester_id"),
    ("project.task.testcase", "passed"),
)
DROPPED_FOREIGN_KEYS = ("project_task_tester_id_fkey",)
# The relations of the task whose sub-views, in the views of the task, show a model of DROPPED_FIELDS.
TASK_SUB_VIEW_MODELS = {"testcase_ids": "project.task.testcase"}
# Deleted from this module since and left to any module that still declares them: only their
# nodes go from the views of this module.
MOVED_FIELDS = (("project.task", "questions"), ("project.task", "answers"))
# Deleted from the test case model since; the checklist model still has methods of these names.
DROPPED_TEST_CASE_BUTTONS = ("change_state_done", "change_state_todo", "change_state_waiting", "change_state_cancelled")

CHECKLIST_MODEL = "project.task.checklist.item"
FORMER_CHECKLIST_MODEL = "project.task.subtask"
CHECKLIST_TABLE = "project_task_checklist_item"
FORMER_CHECKLIST_TABLE = "project_task_subtask"
# Fields of the task, and the copies of models that delegate to it: {former name: new name}.
RENAMED_CHECKLIST_FIELDS = {"subtask_ids": "checklist_item_ids", "kanban_subtasks": "kanban_checklist"}
DROPPED_CHECKLIST_FIELD = "default_user"
RENAMED_CHECKLIST_EXTERNAL_IDS = {
    "access_task_subtask": "access_task_checklist_item",
    "subtasks_subtype": "checklist_subtype",
    "all_subtask_subtype": "project_checklist_subtype",
    "view_task_kanban_subtasks": "view_task_kanban_checklist",
    "project_task_subtask_view_form": "project_task_checklist_item_view_form",
    "project_task_subtask_view_search": "project_task_checklist_item_view_search",
    "subtask_list_view": "project_task_checklist_item_view_tree",
    "action_view_subtask": "action_view_checklist_item",
    "menu_action_view_subtask": "menu_action_view_checklist_item",
}
# The fields mail.activity.mixin, and the modules extending it, gave the checklist model.
CHECKLIST_ACTIVITY_FIELD_PATTERN = r"^(my_)?activity_"
CHECKLIST_VIEW_FILE = MODULE + "/views/project_task_checklist_item_views.xml"
FORMER_CHECKLIST_VIEW_FILE = MODULE + "/views/project_task_subtask_views.xml"
# Besides the model columns of the Many2oneReference fields the registry knows.
MODEL_NAME_COLUMNS = (
    ("ir_model_data", "model"),
    ("ir_ui_view", "model"),
    ("ir_act_window", "res_model"),
    ("ir_act_client", "res_model"),
    ("ir_act_report_xml", "model"),
    ("ir_act_server", "model_name"),
    ("ir_filters", "model_id"),
    ("ir_exports", "resource"),
    ("mail_template", "model"),
    ("mail_message", "model"),
    ("mail_followers", "res_model"),
    ("mail_activity", "res_model"),
    ("mail_activity_type", "res_model"),
    ("mail_message_subtype", "res_model"),
    ("ir_attachment", "res_model"),
    ("rating_rating", "res_model"),
    ("rating_rating", "parent_res_model"),
)

RENAME_RELATED_PATHS = """
    UPDATE ir_model_fields
       SET related = array_to_string(
               array_replace(string_to_array(related, '.'), %(former_field)s::text, %(field)s::text), '.'
           )
     WHERE %(former_field)s::text = ANY(string_to_array(related, '.'))
"""
RENAME_FIELD_EXTERNAL_IDS = """
    UPDATE ir_model_data imd
       SET name = 'field_' || replace(field.model, '.', '_') || '__' || %(field)s
      FROM ir_model_fields field
     WHERE imd.model = 'ir.model.fields'
       AND imd.res_id = field.id
       AND field.name = %(field)s
       AND imd.name = 'field_' || replace(field.model, '.', '_') || '__' || %(former_field)s
       AND NOT EXISTS (
           SELECT 1
             FROM ir_model_data taken
            WHERE taken.module = imd.module
              AND taken.name = 'field_' || replace(field.model, '.', '_') || '__' || %(field)s
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

FORMER_CONSTRAINT_NAMES = """
    SELECT conname
      FROM pg_constraint
     WHERE conrelid = to_regclass(%(table)s)
       AND left(conname, length(%(former_prefix)s)) = %(former_prefix)s
  ORDER BY conname
"""
# Run after the constraints: renaming the constraint of a primary key renames its index too.
FORMER_INDEX_NAMES = """
    SELECT indexname
      FROM pg_indexes
     WHERE schemaname = current_schema()
       AND tablename = %(table)s
       AND left(indexname, length(%(former_prefix)s)) = %(former_prefix)s
  ORDER BY indexname
"""
RENAME_CHECKLIST_CONSTRAINT_ROWS = """
    UPDATE ir_model_constraint con
       SET name = %(prefix)s || substr(con.name, length(%(former_prefix)s) + 1)
      FROM ir_model
     WHERE ir_model.id = con.model
       AND ir_model.model IN (%(model)s, %(former_model)s)
       AND left(con.name, length(%(former_prefix)s)) = %(former_prefix)s
       AND NOT EXISTS (
           SELECT 1
             FROM ir_model_constraint taken
            WHERE taken.module = con.module
              AND taken.name = %(prefix)s || substr(con.name, length(%(former_prefix)s) + 1)
       )
"""
RENAME_CHECKLIST_MODEL = """
    UPDATE ir_model
       SET model = %(model)s
     WHERE model = %(former_model)s
       AND NOT EXISTS (SELECT 1 FROM ir_model taken WHERE taken.model = %(model)s)
"""
MOVE_CHECKLIST_FIELDS = """
    UPDATE ir_model_fields field
       SET model = %(model)s
     WHERE field.model = %(former_model)s
       AND NOT EXISTS (
           SELECT 1 FROM ir_model_fields taken WHERE taken.model = %(model)s AND taken.name = field.name
       )
"""
RENAME_CHECKLIST_RELATIONS = """
    UPDATE ir_model_fields SET relation = %(model)s WHERE relation = %(former_model)s
"""
# The field of the task, and its copies: a copy is related to the field of the same name.
RENAME_CHECKLIST_FIELD = """
    UPDATE ir_model_fields field
       SET name = %(field)s
     WHERE field.name = %(former_field)s
       AND (
           field.model = 'project.task'
           OR right(field.related, length(%(former_field)s) + 1) = '.' || %(former_field)s
       )
       AND NOT EXISTS (
           SELECT 1 FROM ir_model_fields taken WHERE taken.model = field.model AND taken.name = %(field)s
       )
"""
RENAME_WORD_IN_DEPENDENCIES = """
    UPDATE ir_model_fields
       SET depends = regexp_replace(depends, %(pattern)s, %(field)s, 'g')
     WHERE depends ~ %(pattern)s
"""
RENAME_CHECKLIST_MODEL_IN_COLUMN = sql.SQL(
    """
    UPDATE {table} SET {column} = %(model)s WHERE {column} = %(former_model)s
    """
)
RENAME_CHECKLIST_MODEL_IN_REFERENCES = sql.SQL(
    """
    UPDATE {table}
       SET {column} = %(model)s || substr({column}, length(%(former_model)s) + 1)
     WHERE left({column}, length(%(former_model)s) + 1) = %(former_model)s || ','
    """
)
# The external ids core gives the model, its fields and their selection values, in any module.
RENAME_CHECKLIST_RECORD_EXTERNAL_IDS = """
    WITH checklist_record AS (
        SELECT 'ir.model' AS model, id AS res_id FROM ir_model WHERE model = %(model)s
         UNION ALL
        SELECT 'ir.model.fields', id FROM ir_model_fields WHERE model = %(model)s
         UNION ALL
        SELECT 'ir.model.fields.selection', selection.id
          FROM ir_model_fields_selection selection
          JOIN ir_model_fields field ON field.id = selection.field_id
         WHERE field.model = %(model)s
    ),
    renamed AS (
        SELECT imd.id, imd.module, regexp_replace(imd.name, %(pattern)s, %(replacement)s) AS name
          FROM ir_model_data imd
          JOIN checklist_record record ON record.model = imd.model AND record.res_id = imd.res_id
         WHERE imd.name ~ %(pattern)s
    )
    UPDATE ir_model_data imd
       SET name = renamed.name
      FROM renamed
     WHERE imd.id = renamed.id
       AND NOT EXISTS (
           SELECT 1 FROM ir_model_data taken WHERE taken.module = renamed.module AND taken.name = renamed.name
       )
"""
CHECKLIST_VIEWS = """
    SELECT view.id, view.model, view.arch_db
      FROM ir_ui_view view
     WHERE view.arch_db IS NOT NULL
       AND (
           view.model = %(model)s
           OR view.model IN (SELECT field.model FROM ir_model_fields field WHERE field.name IN %(fields)s)
       )
"""
RENAME_WORD_IN_TEXT = sql.SQL(
    """
    UPDATE {table}
       SET {column} = regexp_replace({column}, %(pattern)s, %(name)s, 'g')
     WHERE {column} ~ %(pattern)s
    """
)
FORGET_CHECKLIST_ACTIVITIES = "DELETE FROM mail_activity WHERE res_model = %(model)s"
FORGET_CHECKLIST_ACTIVITY_SELECTION_EXTERNAL_IDS = """
    DELETE FROM ir_model_data imd
     USING ir_model_fields_selection selection, ir_model_fields field
     WHERE imd.model = 'ir.model.fields.selection'
       AND imd.res_id = selection.id
       AND field.id = selection.field_id
       AND field.model = %(model)s
       AND field.name ~ %(pattern)s
"""
FORGET_CHECKLIST_ACTIVITY_FIELD_EXTERNAL_IDS = """
    DELETE FROM ir_model_data imd
     USING ir_model_fields field
     WHERE imd.model = 'ir.model.fields'
       AND imd.res_id = field.id
       AND field.model = %(model)s
       AND field.name ~ %(pattern)s
       AND imd.module != %(module)s
"""
RENAME_CHECKLIST_VIEW_FILES = """
    UPDATE ir_ui_view SET arch_fs = %(file)s WHERE arch_fs = %(former_file)s
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

# PostgreSQL holds a NaN equal to itself, so IN finds it.
CLEAR_ESTIMATES_THAT_ARE_NOT_NUMBERS = """
    UPDATE project_task
       SET planned_hours = 0
     WHERE planned_hours IN ('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)
 RETURNING id
"""

# The CASE states SIZE_BANDS of the task model.
RECOMPUTE_TASK_SIZES = """
    UPDATE project_task
       SET tshirt_estimate = sized.size
      FROM (
            SELECT id,
                   CASE
                       WHEN COALESCE(planned_hours, 0) <= 0 THEN NULL
                       WHEN planned_hours <= 2 THEN 'xs'
                       WHEN planned_hours <= 6 THEN 's'
                       WHEN planned_hours <= 12 THEN 'm'
                       WHEN planned_hours <= 32 THEN 'l'
                       WHEN planned_hours <= 60 THEN 'xl'
                       ELSE 'xxl'
                   END AS size
              FROM project_task
           ) AS sized
     WHERE sized.id = project_task.id
       AND project_task.tshirt_estimate IS DISTINCT FROM sized.size
"""

OLD_TEST_CASE_STATES = {"todo": "not_run", "done": "passed", "cancelled": "not_applicable"}
# Takes the translation as a JSON object of old value to new value: OLD_TEST_CASE_STATES.
CONVERT_TEST_CASE_STATES = """
    WITH converted AS (
        UPDATE project_task_testcase testcase
           SET state = translation.new_state
          FROM jsonb_each_text(%s::jsonb) AS translation(old_state, new_state)
         WHERE testcase.state = translation.old_state
     RETURNING translation.old_state
    )
    SELECT old_state, count(*) FROM converted GROUP BY old_state
"""

QUESTION_COLUMNS = ("questions", "answers")
LIST_TAGS = ("ol", "ul")
# The cases of convert_questions_to_rows, in the words of its log line.
QUESTION_CASES = {
    "paired_lists": "tasks with lists paired item by item",
    "kept_whole": "tasks with questions and answers kept whole",
    "open_questions": "tasks with questions and no answers",
    "answers_only": "tasks with answers and no questions",
    "blank": "tasks with blank questions and answers",
    "has_rows": "tasks skipped for the rows they have",
}
CONVERTED_STATES = ("answered", "open")
TASK_QUESTIONS_AND_ANSWERS = """
    SELECT task.id, task.questions, task.answers,
           EXISTS (SELECT 1 FROM project_task_question question WHERE question.task_id = task.id)
      FROM project_task task
     WHERE task.questions IS NOT NULL OR task.answers IS NOT NULL
  ORDER BY task.id
"""
# Every row of a task under the default sequence: the id keeps their order, and a row added later comes last.
INSERT_QUESTION_ROWS = """
    INSERT INTO project_task_question
           (task_id, project_id, sequence, round, question, answer, state,
            create_uid, create_date, write_uid, write_date)
    SELECT question_row.task_id, task.project_id, %(sequence)s, question_row.round, question_row.question,
           question_row.answer, question_row.state, %(uid)s, %(now)s, %(uid)s, %(now)s
      FROM unnest(%(task_ids)s::integer[], %(rounds)s::varchar[], %(questions)s::text[], %(answers)s::text[],
                  %(states)s::varchar[])
           WITH ORDINALITY AS question_row(task_id, round, question, answer, state, position)
      JOIN project_task task ON task.id = question_row.task_id
  ORDER BY question_row.position
"""

TASK_MESSAGE_TABLE = "mail_message"
# column: its type, its comment and the name Odoo gives the index of the field, so that it finds it made
TASK_MESSAGE_COLUMNS = {
    "task_id": ("int4", "Task", "mail_message_task_id_index"),
    "note_category": ("varchar", "Category", "mail_message_note_category_index"),
}
# The messages with a value now, by their partial index, and those that are to have one.
FILL_TASK_IDS = """
    WITH computed AS (
        SELECT message.id, task.id AS task_id
          FROM mail_message message
          LEFT JOIN project_task task ON message.model = %(model)s AND task.id = message.res_id
         WHERE message.model = %(model)s OR message.task_id IS NOT NULL
    ), filled AS (
        UPDATE mail_message message
           SET task_id = computed.task_id
          FROM computed
         WHERE message.id = computed.id AND message.task_id IS DISTINCT FROM computed.task_id
     RETURNING computed.task_id IS NOT NULL AS is_given
    )
    SELECT is_given, count(*) FROM filled GROUP BY is_given
"""
TASK_NOTES_AND_CATEGORIZED_MESSAGES = sql.SQL(
    "(message.model = %(model)s AND message.subtype_id = %(subtype_id)s) OR message.note_category IS NOT NULL"
)
FILL_NOTE_CATEGORIES = sql.SQL(
    """
    WITH computed AS (
        SELECT message.id,
               CASE WHEN message.model = %(model)s AND message.subtype_id = %(subtype_id)s
                         AND NOT EXISTS (SELECT FROM mail_tracking_value WHERE mail_message_id = message.id)
                    THEN (SELECT rule.category
                            FROM unnest(%(categories)s::varchar[], %(patterns)s::varchar[]) WITH ORDINALITY
                                 AS rule(category, pattern, position)
                           WHERE message.body ~ rule.pattern
                           ORDER BY rule.position
                           LIMIT 1)
               END AS category
          FROM mail_message message
         WHERE {messages}
    ), filled AS (
        UPDATE mail_message message
           SET note_category = computed.category
          FROM computed
         WHERE message.id = computed.id AND message.note_category IS DISTINCT FROM computed.category
     RETURNING computed.category
    )
    SELECT category, count(*) FROM filled GROUP BY category
    """
)
TASK_NOTES_OPENING_WITH_A_SPAN_WITHOUT_CATEGORY = sql.SQL(
    "message.model = %(model)s AND message.subtype_id = %(subtype_id)s AND message.note_category IS NULL"
    " AND message.body ~ %(span_opening)s"
)


def pre_init_hook(cr):
    """Give the messages of tasks their task and the internal notes their
    category, see fill_task_messages.
    """
    fill_task_messages(cr)


def post_init_hook(cr, registry):
    """Migration scripts do not run on an install, and the column of pr_uri
    may hold the values project_task_pull_request stored in it. The tasks
    that exist already have a past to log, too, and hours that are not a
    number. Projects may have no key, where OCA project_key did not run.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["project.project"]._set_missing_keys()
    convert_old_pr_uri_values(env)
    fill_stage_log(env)
    clear_estimates_that_are_not_numbers(cr)


def fill_task_messages(cr):
    """Give every message of a task its task, and every internal note of a
    task the category of its body, by SQL. Return the messages given a task
    and those whose task was cleared, and the notes given each category, the
    category False for those whose category was cleared. The columns and
    their indexes are created first where they are missing.

    mail_message holds every message of the database: on a column it creates,
    an install or an update computes the field over all of them. Made before
    the fields are set up, the columns are not computed. The SQL gives every
    message what the computes give, whatever its columns held, the patterns
    of NOTE_CATEGORY_PATTERNS included; a second run changes nothing. The
    columns are analyzed last.
    """
    for column, (column_type, comment, index_name) in TASK_MESSAGE_COLUMNS.items():
        if not column_exists(cr, TASK_MESSAGE_TABLE, column):
            create_column(cr, TASK_MESSAGE_TABLE, column, column_type, comment)
        column_expression = '"%s"' % column
        create_index(
            cr, index_name, TASK_MESSAGE_TABLE, [column_expression], where="%s IS NOT NULL" % column_expression
        )
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    params = _get_note_category_params(env)
    cr.execute(FILL_TASK_IDS, params)
    task_counts = dict.fromkeys(("given", "cleared"), 0)
    task_counts.update(("given" if is_given else "cleared", count) for is_given, count in cr.fetchall())
    category_counts = _fill_note_categories(cr, TASK_NOTES_AND_CATEGORIZED_MESSAGES, params)
    # autovacuum analyzes after a tenth of the table changed: until then the planner, blind to the
    # new columns, reads every note to list those of a task
    cr.execute("ANALYZE %s (%s)" % (TASK_MESSAGE_TABLE, ", ".join(TASK_MESSAGE_COLUMNS)))
    env.invalidate_all()
    _logger.info(
        "Task messages given their task: %d, their task cleared: %d; task notes given a category: %s,"
        " their category cleared: %d",
        task_counts["given"],
        task_counts["cleared"],
        ", ".join("%d %s" % (category_counts[category], label) for category, label in NOTE_CATEGORIES.items()),
        category_counts[False],
    )
    return task_counts, category_counts


def categorize_span_notes(cr):
    """Give their category, by SQL, the internal notes of tasks without one
    whose body opens with a <span>, and return how many were given one.

    The Html sanitizer stores inside a <span> a body of more than one node
    that opens with an inline tag and holds no block tag. Only these notes
    are read and written: every other message is taken to hold its
    category already.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    params = {**_get_note_category_params(env), "span_opening": SPAN_OPENING}
    category_counts = _fill_note_categories(cr, TASK_NOTES_OPENING_WITH_A_SPAN_WITHOUT_CATEGORY, params)
    env.invalidate_all()
    categorized_count = sum(category_counts.values())
    _logger.info("Task notes opening with a span given a category: %d", categorized_count)
    return categorized_count


def _get_note_category_params(env):
    return {
        "model": NOTE_MODEL,
        "subtype_id": env["ir.model.data"]._xmlid_to_res_id(NOTE_SUBTYPE),
        "categories": list(NOTE_CATEGORY_PATTERNS),
        "patterns": list(NOTE_CATEGORY_PATTERNS.values()),
    }


def _fill_note_categories(cr, messages, params):
    """Give the messages that the SQL condition `messages` selects the
    category of their body; return the notes given each category, False for
    those cleared.
    """
    cr.execute(FILL_NOTE_CATEGORIES.format(messages=messages), params)
    category_counts = dict.fromkeys((*NOTE_CATEGORIES, False), 0)
    category_counts.update((category or False, count) for category, count in cr.fetchall())
    return category_counts


def clear_estimates_that_are_not_numbers(cr):
    """Set to zero the hours of every task that holds a NaN or an infinity,
    and return how many tasks changed.

    A write refuses such hours since 16.0.2.4.1, so a task holding them could
    no longer be copied or recur; the web client cannot load it either. The
    hours are set by SQL, without tracking; the stored fields computed from
    them (the size, and the remaining hours of hr_timesheet) are recomputed
    through the ORM, which stamps the last update of those tasks.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(CLEAR_ESTIMATES_THAT_ARE_NOT_NUMBERS)
    tasks = env["project.task"].browse(row[0] for row in cr.fetchall())
    # the whole record: a value in the cache equal to the recomputed one would not be written
    tasks.invalidate_recordset()
    tasks.modified(["planned_hours"])
    env.flush_all()
    _logger.info("Estimates that were not a number set to zero: %d tasks", len(tasks))
    return len(tasks)


def recompute_task_sizes(cr):
    """Give every task whose stored size is not the size of its hours that
    size, and return how many tasks changed.

    The column is older than the compute, which an update does not run on the
    rows that exist. The rows are written by SQL: they are not edits of the
    tasks, so no write date. A second run finds nothing left to change.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(RECOMPUTE_TASK_SIZES)
    changed_count = cr.rowcount
    env["project.task"].invalidate_model(["tshirt_estimate"])
    _logger.info("Task sizes recomputed from the hours: %d tasks changed", changed_count)
    return changed_count


def convert_test_case_states(cr):
    """Give every test case stored with a state of the old selection, a key
    of OLD_TEST_CASE_STATES, its new value; return the cases converted per old
    value.

    An update only reflects the new selection and deletes the rows of the old
    values at its end: the cases keep the values they hold. The cases are
    written by SQL: they are not results given anew, so no write date and no
    verification stamp. A second run finds nothing left to change.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(CONVERT_TEST_CASE_STATES, [Json(OLD_TEST_CASE_STATES)])
    converted_counts = dict.fromkeys(OLD_TEST_CASE_STATES, 0)
    converted_counts.update(cr.fetchall())
    env["project.task.testcase"].invalidate_model(["state"])
    _logger.info(
        "Test case states converted: %s",
        ", ".join("%d %s to %s" % (count, old, OLD_TEST_CASE_STATES[old]) for old, count in converted_counts.items()),
    )
    return converted_counts


def convert_questions_to_rows(cr, is_update=False):
    """Turn the questions and answers of every task, archived ones included,
    from the two Html columns of the task into question rows, and return the
    tasks of each case of QUESTION_CASES and the rows inserted per state.

    Lists are the top-level <ol> and <ul> of the Html; the one block of text
    right before a list, if any, is its heading and names its round. A list
    nested in a list, as the editor puts it in an item of its own, belongs to
    the item before it; nested before any item, the list is not split. Html
    showing neither text, media nor an icon is blank.

    - Questions and answers are both lists alone, list by list of the same
      length: a row per pair of items, Answered, or Open where the answer item
      is blank; a pair of two blank items gives no row. The heading of an
      answer list other than the round of its questions opens each of its
      answers: it says who answered and when.
    - Both hold something else: one row of each whole, Answered.
    - Questions alone: a row per item that is not blank, Open, or one row of
      the whole when they are not lists alone.
    - Answers alone: one row of the whole without a question, Answered.

    Blank items give no row, so the heading of a list of blank items alone
    would be in none: where a pair of lists, or a list of questions alone,
    has a heading and blank items only, or an answer list a heading other than
    its round over blank answers only, the Html is kept whole as when it is
    not lists alone. Lists whose pairs are all blank, or questions whose items
    are all blank, are such Html: what they show is in their headings.

    Names and dates in the answers are not read: no row says who answered or
    when. The rows are inserted by SQL as the superuser. Without the two
    columns, as on a fresh database, nothing is done; an update from a version
    that declared them always has them, so there their absence is a warning.
    A task that has rows is skipped, so a second run inserts nothing.
    """
    case_counts = dict.fromkeys(QUESTION_CASES, 0)
    state_counts = dict.fromkeys(CONVERTED_STATES, 0)
    missing_columns = [column for column in QUESTION_COLUMNS if not column_exists(cr, "project_task", column)]
    if missing_columns:
        if is_update:
            _logger.warning(
                "Questions and answers not converted to question rows: project_task has no column %s",
                ", ".join(missing_columns),
            )
        return case_counts, state_counts
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    cr.execute(TASK_QUESTIONS_AND_ANSWERS)
    question_rows = []
    for task_id, questions, answers, has_rows in cr.fetchall():
        case, task_rows = ("has_rows", []) if has_rows else _get_question_rows(questions, answers)
        case_counts[case] += 1
        question_rows += [(task_id, *row) for row in task_rows]
    if question_rows:
        task_ids, rounds, question_texts, answer_texts, states = (list(column) for column in zip(*question_rows))
        cr.execute(
            INSERT_QUESTION_ROWS,
            {
                "task_ids": task_ids,
                "rounds": rounds,
                "questions": question_texts,
                "answers": answer_texts,
                "states": states,
                "sequence": env["project.task.question"].default_get(["sequence"])["sequence"],
                "uid": SUPERUSER_ID,
                "now": cr.now(),
            },
        )
        for state in states:
            state_counts[state] += 1
    env.invalidate_all()
    _logger.info(
        "Questions and answers converted to question rows: %s; rows inserted: %s",
        ", ".join("%d %s" % (case_counts[case], label) for case, label in QUESTION_CASES.items()),
        ", ".join("%d %s" % (state_counts[state], state) for state in CONVERTED_STATES),
    )
    return case_counts, state_counts


def _get_question_rows(questions, answers):
    """Return the case of QUESTION_CASES of one task and its rows as (round, question, answer, state)."""
    has_questions = not is_blank_html(questions)
    has_answers = not is_blank_html(answers)
    if not has_questions:
        return ("answers_only", [(None, None, answers, "answered")]) if has_answers else ("blank", [])
    question_rounds = _split_into_rounds(questions)
    if not has_answers:
        open_rows = _list_open_questions(question_rounds)
        return "open_questions", [(None, questions, None, "open")] if open_rows is None else open_rows
    paired_rows = _pair_rounds(question_rounds, _split_into_rounds(answers))
    if paired_rows is None:
        return "kept_whole", [(None, questions, answers, "answered")]
    return "paired_lists", paired_rows


def _list_open_questions(question_rounds):
    """Return a row per item that is not blank, or None when the questions are
    not lists alone or a heading would be in no row.
    """
    if question_rounds is None:
        return None
    open_rows = []
    for round_name, heading, questions in question_rounds:
        round_rows = [(round_name, question, None, "open") for question in questions if question]
        if heading is not None and not round_rows:
            return None
        open_rows += round_rows
    return open_rows


def _pair_rounds(question_rounds, answer_rounds):
    """Return the rows of the items paired list by list, or None when the lists
    do not pair or a heading would be in no row.
    """
    if question_rounds is None or answer_rounds is None or len(question_rounds) != len(answer_rounds):
        return None
    paired_rows = []
    for (round_name, heading, questions), (answer_round_name, answer_heading, answers) in zip(
        question_rounds, answer_rounds
    ):
        if len(questions) != len(answers):
            return None
        answer_opening = answer_heading if answer_round_name not in (None, round_name) else ""
        if answer_opening and not any(answers):
            return None
        round_rows = [
            (round_name, question, answer_opening + answer if answer else None, "answered" if answer else "open")
            for question, answer in zip(questions, answers)
            if question or answer
        ]
        if (heading is not None or answer_heading is not None) and not round_rows:
            return None
        paired_rows += round_rows
    return paired_rows


def _split_into_rounds(html_content):
    """Return [(round name or None, its heading Html or None, [inner Html of
    each item, None where blank])] when html_content holds lists alone, each
    after at most one block of text, its heading; else None.
    """
    root = parse_html(html_content)
    if root is None or _plain_text(root.text):
        return None
    rounds = []
    heading = None
    for element in root:
        if _plain_text(element.tail):
            return None
        if not isinstance(element.tag, str):
            continue
        if element.tag in LIST_TAGS:
            items = _get_list_items(element)
            if items is None:
                return None
            rounds.append((*_get_round(heading), items))
            heading = None
        elif has_content(element):
            # a second block, media or a list would be lost with the name of the round
            if heading is not None or has_media(element) or element.xpath(".//ol | .//ul"):
                return None
            heading = element
    return rounds if rounds and heading is None else None


def _get_round(heading):
    if heading is None:
        return None, None
    return get_visible_text(heading), lxml_html.tostring(heading, encoding="unicode", with_tail=False)


def _get_list_items(list_element):
    if _plain_text(list_element.text):
        return None
    items = []
    for child in list_element:
        if _plain_text(child.tail):
            return None
        if not isinstance(child.tag, str):
            continue
        if _is_nested_list(child):
            if not items:
                return None
            if has_content(child):
                items[-1] = (items[-1] or "") + _nested_list_html(child)
        elif child.tag == "li":
            items.append(_inner_html(child) if has_content(child) else None)
        elif has_content(child):
            return None
    return items


def _is_nested_list(element):
    """Whether a child of a list is a list put right in it, or an item holding
    lists and nothing else to see: the editor nests a list so, class
    oe-nested.
    """
    if element.tag in LIST_TAGS:
        return True
    if element.tag != "li" or not element.xpath("./ol | ./ul"):
        return False
    own_content = copy.deepcopy(element)
    for nested_list in own_content.xpath("./ol | ./ul"):
        nested_list.drop_tree()
    return not has_content(own_content)


def _nested_list_html(element):
    if element.tag == "li":
        return _inner_html(element)
    return lxml_html.tostring(element, encoding="unicode", with_tail=False)


def _inner_html(element):
    children_html = "".join(lxml_html.tostring(child, encoding="unicode") for child in element)
    return (html.escape(element.text or "", quote=False) + children_html).strip()


def _plain_text(text):
    return " ".join((text or "").split())


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


def forget_dropped_fields(cr):
    """Clear what the database stores about the fields in DROPPED_FIELDS and
    MOVED_FIELDS and an update of this module trips on or leaves behind,
    before its data files are loaded.

    The update deletes the fields in DROPPED_FIELDS themselves, with their
    columns, at its end: they are records of this module that it no longer
    has. A module declaring a field in MOVED_FIELDS loads after this one, so
    the field is not one of the model while this module loads either. Until
    then the stored arch of a view of this module still names them, and it is
    validated against the fields of the model as soon as another view of the
    same form is loaded, before its own data file rewrites it. Their nodes are
    taken out of every translation of the arch, those in the sub-views of the
    relations in TASK_SUB_VIEW_MODELS included; emptying the arch would lose
    the translated terms the database alone holds.

    A foreign key goes with its column, but the row that registers it to this
    module would stay.

    A second run finds nothing left.
    """
    field_names = defaultdict(set)
    for model, name in DROPPED_FIELDS + MOVED_FIELDS:
        field_names[model].add(name)
    cleared_view_ids = _rewrite_module_views(
        cr, field_names, lambda model, arch: _remove_dropped_field_nodes(model, arch, field_names)
    )
    constraints = api.Environment(cr, SUPERUSER_ID, {})["ir.model.constraint"]
    constraints.flush_model()
    cr.execute(FORGET_FOREIGN_KEYS, {"module": MODULE, "foreign_keys": DROPPED_FOREIGN_KEYS})
    forgotten_key_count = cr.rowcount
    constraints.invalidate_model()
    if cleared_view_ids or forgotten_key_count:
        _logger.info(
            "Dropped fields forgotten: taken out of %d views, %d foreign key registrations deleted",
            len(cleared_view_ids),
            forgotten_key_count,
        )


def _remove_dropped_field_nodes(model, arch, field_names):
    """Return arch, of a view of model, without the nodes of the fields in
    field_names[model] and, in a view of the task, without the nodes of the
    fields in field_names[sub-view model] in the sub-views of each relation in
    TASK_SUB_VIEW_MODELS.
    """
    arch = _remove_field_nodes(arch, field_names[model])
    if model == "project.task":
        for relation, sub_view_model in TASK_SUB_VIEW_MODELS.items():
            arch = _remove_sub_view_nodes(arch, "field", relation, field_names[sub_view_model])
    return arch


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


def forget_dropped_test_case_buttons(cr):
    """Take the buttons of the methods in DROPPED_TEST_CASE_BUTTONS out of the
    sub-view of the task's test cases, in every translation of the stored arch
    of this module's views, before its data files are loaded.

    Such a button fails the validation of the task form as soon as another
    view of it is loaded, as a field in DROPPED_FIELDS does: see
    forget_dropped_fields. The buttons of the same names in the checklist
    sub-view stay: the checklist model still has their methods.

    A second run finds nothing left.
    """
    cleared_view_ids = _rewrite_module_views(
        cr,
        ("project.task",),
        lambda _model, arch: _remove_sub_view_nodes(arch, "button", "testcase_ids", DROPPED_TEST_CASE_BUTTONS),
    )
    if cleared_view_ids:
        _logger.info("Dropped test case buttons forgotten: taken out of %d views", len(cleared_view_ids))


def _remove_sub_view_nodes(arch, tag, field_name, names):
    """Return arch without the tag nodes named in names whose closest
    enclosing field is field_name: the buttons or fields of its sub-views.

    An extension written as <field name="..." position="after"> counts as that
    field, so a node it places next to the field goes too. A node added
    through <xpath expr="//field[@name='...']/tree" position="inside"> has no
    field ancestor and stays.
    """
    root = etree.fromstring(arch)
    nodes = [
        node
        for node in root.iterdescendants(tag)
        if node.get("name") in names and node.xpath("ancestor::field[1]/@name") == [field_name]
    ]
    for node in nodes:
        node.getparent().remove(node)
    return etree.tostring(root, encoding="unicode") if nodes else arch


def _rewrite_module_views(cr, models, rewrite_arch):
    """Pass every translation of the stored arch of this module's views of
    models through rewrite_arch(model, arch); return the ids of the views it
    changed.
    """
    return _rewrite_views(cr, MODULE_VIEWS, {"module": MODULE, "models": tuple(models)}, rewrite_arch)


def _rewrite_views(cr, query, params, rewrite_arch):
    """Pass every translation of the stored arch of the views query selects, as
    (id, model, arch_db), through rewrite_arch(model, arch); return the ids of
    the views it changed. What the ORM holds pending goes to the database
    first, and the ORM reads the archs written here from it.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["ir.model.data"].flush_model()
    env["ir.ui.view"].flush_model()
    cr.execute(query, params)
    rewritten_view_ids = []
    for view_id, model, archs in cr.fetchall():
        rewritten_archs = {lang: rewrite_arch(model, arch) for lang, arch in archs.items()}
        if rewritten_archs != archs:
            cr.execute("UPDATE ir_ui_view SET arch_db = %s WHERE id = %s", (Json(rewritten_archs), view_id))
            rewritten_view_ids.append(view_id)
    env["ir.ui.view"].invalidate_model()
    return rewritten_view_ids


def rename_external_ids(cr, module, renamed_names):
    """Rename external ids of module, {former name: new name}, and return how
    many changed. A new name that is already taken leaves the former one in
    place.
    """
    renamed_count = 0
    for former_name, new_name in renamed_names.items():
        cr.execute(RENAME_EXTERNAL_ID, {"module": module, "former_name": former_name, "new_name": new_name})
        renamed_count += cr.rowcount
    return renamed_count


def rename_checklist(cr):
    """Rename the checklist model project.task.subtask to project.task.checklist.item,
    and the fields of the task in RENAMED_CHECKLIST_FIELDS, before the data files
    are loaded; return the count of each kind of rename.

    Renamed: the table with its sequence, constraints and indexes; the model
    and its fields; the relations, related paths and dependencies of fields;
    the external ids of the model, its fields and their selection values, and
    those in RENAMED_CHECKLIST_EXTERNAL_IDS; the columns in MODEL_NAME_COLUMNS,
    the model column of every stored Many2oneReference and the value of every
    stored Reference; the stored arch of the views of the checklist and of the
    models holding the fields, and the file they were loaded from; the domains,
    contexts and orders in FIELD_NAMING_COLUMNS. Left as they are: the code of
    server actions and scheduled actions, the bodies of mail templates, the
    field paths of export lines, and the views of other models. Names are
    matched as whole words, so a name that only contains one, such as the
    context key default_subtask_ids, stays: a looser match would rename names
    that are not the checklist's.

    The table keeps its rows, and the model, the fields, their selection values,
    the constraints and the records in RENAMED_CHECKLIST_EXTERNAL_IDS keep their
    database id. Left under the former names they would be deleted at the end
    of the update, the table and the columns with them. The indexes and
    constraints of the table take its new name: core looks an index up by name
    and would add a second one. A model that delegates to the task carries a
    copy of each field, with an external id in whichever module holds it: the
    copy follows too.

    The stored arch of a view is validated as soon as another view of its model
    is loaded, before its own data file rewrites it: the views stop naming the
    former fields, and this module's views stop naming DROPPED_CHECKLIST_FIELD,
    which the update deletes at its end. A server in dev mode reads a view from
    the file it was loaded from: the views of the renamed file name the new one.

    A second run finds nothing left.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    model_params = {"model": CHECKLIST_MODEL, "former_model": FORMER_CHECKLIST_MODEL}
    table_params = {"prefix": CHECKLIST_TABLE + "_", "former_prefix": FORMER_CHECKLIST_TABLE + "_"}
    counts = _rename_checklist_table(cr)
    counts["constraint registrations"] = _rowcount(
        cr, RENAME_CHECKLIST_CONSTRAINT_ROWS, {**model_params, **table_params}
    )
    counts["models"] = _rowcount(cr, RENAME_CHECKLIST_MODEL, model_params)
    counts["fields of the model"] = _rowcount(cr, MOVE_CHECKLIST_FIELDS, model_params)
    counts["relations"] = _rowcount(cr, RENAME_CHECKLIST_RELATIONS, model_params)
    counts.update(_rename_checklist_fields(cr))
    counts["values naming the model"] = _rename_checklist_model_in_records(env)
    counts["external ids"] = _rename_checklist_external_ids(cr)
    renamed_view_ids = _rewrite_views(
        cr,
        CHECKLIST_VIEWS,
        {"model": CHECKLIST_MODEL, "fields": tuple(RENAMED_CHECKLIST_FIELDS.values())},
        lambda _model, arch: _rename_checklist_fields_in_arch(arch),
    )
    cleared_view_ids = _rewrite_module_views(
        cr, ("project.task",), lambda _model, arch: _remove_field_and_its_context(arch, DROPPED_CHECKLIST_FIELD)
    )
    counts["views"] = len(set(renamed_view_ids) | set(cleared_view_ids))
    counts["filters, window actions and rules"] = _rename_checklist_in_texts(cr)
    counts["view files"] = _rowcount(
        cr, RENAME_CHECKLIST_VIEW_FILES, {"file": CHECKLIST_VIEW_FILE, "former_file": FORMER_CHECKLIST_VIEW_FILE}
    )
    env.invalidate_all()
    # external ids are cached per registry, and these changed behind the ORM
    env.registry.clear_caches()
    if any(counts.values()):
        _logger.info("Checklist renamed: %s", ", ".join("%d %s" % (count, label) for label, count in counts.items()))
    return counts


def _rowcount(cr, query, params):
    cr.execute(query, params)
    return cr.rowcount


def _rename_checklist_table(cr):
    counts = dict.fromkeys(("tables", "sequences", "constraints", "indexes"), 0)
    if table_exists(cr, FORMER_CHECKLIST_TABLE) and not table_exists(cr, CHECKLIST_TABLE):
        cr.execute(
            sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                sql.Identifier(FORMER_CHECKLIST_TABLE), sql.Identifier(CHECKLIST_TABLE)
            )
        )
        counts["tables"] = 1
    former_sequence, sequence = FORMER_CHECKLIST_TABLE + "_id_seq", CHECKLIST_TABLE + "_id_seq"
    if _relation_exists(cr, former_sequence) and not _relation_exists(cr, sequence):
        cr.execute(
            sql.SQL("ALTER SEQUENCE {} RENAME TO {}").format(sql.Identifier(former_sequence), sql.Identifier(sequence))
        )
        counts["sequences"] = 1
    names_params = {"table": CHECKLIST_TABLE, "former_prefix": FORMER_CHECKLIST_TABLE + "_"}
    cr.execute(FORMER_CONSTRAINT_NAMES, names_params)
    for (former_name,) in cr.fetchall():
        cr.execute(
            sql.SQL("ALTER TABLE {} RENAME CONSTRAINT {} TO {}").format(
                sql.Identifier(CHECKLIST_TABLE),
                sql.Identifier(former_name),
                sql.Identifier(_checklist_table_object_name(former_name)),
            )
        )
        counts["constraints"] += 1
    cr.execute(FORMER_INDEX_NAMES, names_params)
    for (former_name,) in cr.fetchall():
        cr.execute(
            sql.SQL("ALTER INDEX {} RENAME TO {}").format(
                sql.Identifier(former_name), sql.Identifier(_checklist_table_object_name(former_name))
            )
        )
        counts["indexes"] += 1
    return counts


def _relation_exists(cr, name):
    cr.execute("SELECT to_regclass(%s)", (name,))
    return cr.fetchone()[0] is not None


def _checklist_table_object_name(former_name):
    return CHECKLIST_TABLE + former_name[len(FORMER_CHECKLIST_TABLE) :]


def _rename_checklist_fields(cr):
    counts = dict.fromkeys(("fields renamed", "related paths", "dependencies"), 0)
    for former_field, field in RENAMED_CHECKLIST_FIELDS.items():
        params = {"field": field, "former_field": former_field, "pattern": _word_pattern(former_field)}
        counts["fields renamed"] += _rowcount(cr, RENAME_CHECKLIST_FIELD, params)
        counts["related paths"] += _rowcount(cr, RENAME_RELATED_PATHS, params)
        counts["dependencies"] += _rowcount(cr, RENAME_WORD_IN_DEPENDENCIES, params)
    return counts


def _rename_checklist_model_in_records(env):
    params = {"model": CHECKLIST_MODEL, "former_model": FORMER_CHECKLIST_MODEL}
    renamed_count = 0
    for query, columns in (
        (RENAME_CHECKLIST_MODEL_IN_COLUMN, _model_name_columns(env)),
        (RENAME_CHECKLIST_MODEL_IN_REFERENCES, _reference_columns(env)),
    ):
        for table, column in columns:
            renamed_count += _rowcount(
                env.cr, query.format(table=sql.Identifier(table), column=sql.Identifier(column)), params
            )
    return renamed_count


def _model_name_columns(env):
    """Return the existing columns of MODEL_NAME_COLUMNS, and the model column
    of every stored Many2oneReference field the registry knows, as (table,
    column).
    """
    columns = set(MODEL_NAME_COLUMNS)
    for model in _models_with_table(env):
        for field in model._fields.values():
            model_field = model._fields.get(field.model_field) if field.type == "many2one_reference" else None
            if field.store and model_field and model_field.store:
                columns.add((model._table, model_field.name))
    return sorted(column for column in columns if column_exists(env.cr, *column))


def _reference_columns(env):
    """Return the column of every stored Reference field the registry knows,
    which holds 'model,id', as (table, column).
    """
    columns = {
        (model._table, field.name)
        for model in _models_with_table(env)
        for field in model._fields.values()
        if field.type == "reference" and field.store
    }
    return sorted(column for column in columns if column_exists(env.cr, *column))


def _models_with_table(env):
    return [model for model in env.registry.values() if model._auto and not model._abstract and not model._table_query]


def _rename_checklist_external_ids(cr):
    renamed_count = _rowcount(
        cr,
        RENAME_CHECKLIST_RECORD_EXTERNAL_IDS,
        {
            "model": CHECKLIST_MODEL,
            "pattern": r"^(model_|field_|selection__)%s(__|$)" % FORMER_CHECKLIST_TABLE,
            "replacement": r"\1%s\2" % CHECKLIST_TABLE,
        },
    )
    for former_field, field in RENAMED_CHECKLIST_FIELDS.items():
        renamed_count += _rowcount(cr, RENAME_FIELD_EXTERNAL_IDS, {"field": field, "former_field": former_field})
    return renamed_count + rename_external_ids(cr, MODULE, RENAMED_CHECKLIST_EXTERNAL_IDS)


def _rename_checklist_fields_in_arch(arch):
    return re.sub(
        r"\b(%s)\b" % "|".join(RENAMED_CHECKLIST_FIELDS),
        lambda match: RENAMED_CHECKLIST_FIELDS[match.group(1)],
        arch,
    )


def _remove_field_and_its_context(arch, field_name):
    """Return arch without the nodes of field_name, see _remove_field_nodes,
    and without the context attributes that name it.
    """
    arch = _remove_field_nodes(arch, {field_name})
    root = etree.fromstring(arch)
    nodes = [node for node in root.xpath("//*[@context]") if re.search(r"\b%s\b" % field_name, node.get("context"))]
    for node in nodes:
        del node.attrib["context"]
    return etree.tostring(root, encoding="unicode") if nodes else arch


def _rename_checklist_in_texts(cr):
    """Rename the fields and the model in the domains, contexts and orders of
    FIELD_NAMING_COLUMNS, as whole words; return how many values changed.
    """
    renames = {_word_pattern(former): name for former, name in RENAMED_CHECKLIST_FIELDS.items()}
    renames[_word_pattern(FORMER_CHECKLIST_MODEL)] = CHECKLIST_MODEL
    renamed_count = 0
    for table, column in FIELD_NAMING_COLUMNS:
        query = RENAME_WORD_IN_TEXT.format(table=sql.Identifier(table), column=sql.Identifier(column))
        for pattern, name in renames.items():
            renamed_count += _rowcount(cr, query, {"pattern": pattern, "name": name})
    return renamed_count


def _word_pattern(word):
    """Return the PostgreSQL regular expression of word as a whole word."""
    return r"\y%s\y" % re.escape(word)


def forget_checklist_activities(cr):
    """Delete the activities of checklist items, and the external ids that
    would outlive the activity fields of the checklist model, before the data
    files are loaded; return the count of each.

    The model no longer inherits mail.activity.mixin. The update deletes those
    fields at its end, as records of this module it no longer has, and their
    selection values go with them in the database. The external ids of the
    values belong to mail, which the update does not process; an external id
    another module holds on a field would keep the field. An activity left on
    an item could no longer be opened from the activity menu.

    A second run finds nothing left.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    env.flush_all()
    params = {"model": CHECKLIST_MODEL, "module": MODULE, "pattern": CHECKLIST_ACTIVITY_FIELD_PATTERN}
    counts = {
        "activities": _rowcount(cr, FORGET_CHECKLIST_ACTIVITIES, params),
        "external ids": _rowcount(cr, FORGET_CHECKLIST_ACTIVITY_SELECTION_EXTERNAL_IDS, params)
        + _rowcount(cr, FORGET_CHECKLIST_ACTIVITY_FIELD_EXTERNAL_IDS, params),
    }
    env.invalidate_all()
    # external ids are cached per registry, and these changed behind the ORM
    env.registry.clear_caches()
    if any(counts.values()):
        _logger.info(
            "Checklist activities forgotten: %s", ", ".join("%d %s" % (count, label) for label, count in counts.items())
        )
    return counts
