import importlib.util
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from freezegun import freeze_time

from odoo import SUPERUSER_ID
from odoo.exceptions import AccessError
from odoo.tests.common import Form, tagged
from odoo.tools.sql import column_exists, rename_column

from ..hooks import MODULE, QUESTION_CASES, QUESTION_COLUMNS, convert_questions_to_rows, post_init_hook
from ..models.project_task_question import is_blank_html
from .common import ProjectAiDrivenCommon

EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
QUESTION = "<p>Who reviews it?</p>"
ANSWER = "<p>The lead reviews it.</p>"
BLANK_HTML = "<p>&nbsp;<br></p>"
MIGRATION_SCRIPT = Path(__file__).parents[1] / "migrations" / "16.0.2.7.0" / "post-migration.py"
HOOKS_LOGGER = "odoo.addons.%s.hooks" % MODULE
QUESTION_LIST_COLUMNS = [
    "sequence",
    "round",
    "question",
    "answer",
    "addressed_to_id",
    "state",
    "answered_by_id",
    "answered_on",
]
# The cases a run converts; blank tasks and tasks with rows are counted again on every run.
CONVERTED_CASES = ("paired_lists", "kept_whole", "open_questions", "answers_only")

ONE_LIST = (
    "<ol><li>Which stage?</li><li>Who reviews &amp; merges?</li></ol>",
    "<ol><li>Done</li><li>The <b>lead</b>, on 12 Nov</li></ol>",
)
TWO_ROUNDS = (
    "<p><b>Alignment round, 12 Nov 2026</b></p><ol><li>Scope?</li></ol><p>&nbsp;</p>"
    "<p><b>Second round</b></p><ul><li>Data?</li><li>Users?</li></ul>",
    "<ol><li>As agreed</li></ol><ol><li>Production</li><li>Everyone</li></ol>",
)
DIFFERENT_LENGTHS = (
    "<ol><li>Scope?</li><li>Data?</li></ol>",
    "<ol><li>As agreed</li></ol>",
)
FREE_TEXT = ("<p>Is it needed?</p>", "<p>Yes, since March.</p>")
OPEN_LIST = ("<p>Round one</p><ul><li>Scope?</li><li><br></li><li>Data?</li></ul>", None)
OPEN_TEXT = ("<p>Free question?</p><ol><li>With a list</li></ol><p>and text after it</p>", "<p><br></p>")
BLANK = (BLANK_HTML, "<p><br></p>")
ANSWERS_ONLY_TEXT = (None, "<p>Answered in the call.</p>")
ANSWERS_ONLY_LIST = ("<p><br></p>", "<ol><li>As agreed</li><li>Production</li></ol>")
# A sub-list as the editor nests it: an item of its own holding the list.
NESTED_LISTS = (
    '<ol><li>Which stage?</li><li class="oe-nested"><ol><li>draft</li><li>done</li></ol></li><li>Who?</li></ol>',
    '<ol><li>Done</li><li>The lead</li><li class="oe-nested"><ol><li>and tester</li></ol></li></ol>',
)
# Html lxml reads as a whole document, without a body or with two, and the state it gives a row as its answer.
WHOLE_DOCUMENT_STATES = {
    "<html></html>": "open",
    "<html><body>a</body><body>b</body></html>": "answered",
    "<!DOCTYPE html>": "open",
}


def _load_script(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


@tagged("post_install", "-at_install")
class TestQuestions(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Question = cls.env["project.task.question"]

    def _new_question(self, **vals):
        return self.Question.create({"question": QUESTION, "task_id": self.task.id, **vals})

    def _answer_stamps(self, questions):
        return [(question.state, question.answered_by_id.id, question.answered_on) for question in questions]

    def test_a_row_is_open_until_it_is_given_an_answer(self):
        questions = self._new_question() | self._new_question(answer=BLANK_HTML)
        self.assertEqual(self._answer_stamps(questions), [("open", False, False)] * 2)

    def test_a_row_created_with_an_answer_is_answered_by_its_author(self):
        with freeze_time(EARLIER):
            question = self.Question.with_user(self.user_dev).create(
                {"question": QUESTION, "answer": ANSWER, "task_id": self.task.id},
            )
        self.assertEqual(self._answer_stamps(question), [("answered", self.user_dev.id, EARLIER)])

    def test_an_answer_given_to_an_open_row_answers_it_once(self):
        question = self._new_question()
        with freeze_time(EARLIER):
            question.with_user(self.user_dev).write({"answer": ANSWER})
        self.assertEqual(self._answer_stamps(question), [("answered", self.user_dev.id, EARLIER)])
        with freeze_time(LATER):
            question.with_user(self.user_lead).write({"answer": "<p>The lead and the tester.</p>"})
        self.assertEqual(
            self._answer_stamps(question),
            [("answered", self.user_dev.id, EARLIER)],
            "a changed answer is not a new one",
        )

    def test_a_blank_answer_leaves_the_row_open(self):
        question = self._new_question()
        question.write({"answer": BLANK_HTML})
        self.assertEqual(self._answer_stamps(question), [("open", False, False)])

    def test_a_screenshot_alone_is_an_answer(self):
        question = self._new_question()
        question.with_user(self.user_dev).write({"answer": '<p><img src="/web/image/1"></p>'})
        self.assertEqual(question.state, "answered")

    def test_answered_is_stamped_with_the_user_who_sets_it(self):
        question = self._new_question()
        with freeze_time(LATER):
            question.with_user(self.user_lead).write({"state": "answered"})
        self.assertEqual(self._answer_stamps(question), [("answered", self.user_lead.id, LATER)])

    def test_sudo_stamps_the_user_making_the_change(self):
        with freeze_time(EARLIER):
            question = (
                self.Question.with_user(self.user_lead)
                .sudo()
                .create(
                    {"question": QUESTION, "answer": ANSWER, "task_id": self.task.id},
                )
            )
        self.assertEqual(self._answer_stamps(question), [("answered", self.user_lead.id, EARLIER)])

    def test_re_opening_takes_the_stamp_back_and_keeps_the_answer(self):
        question = self._new_question(answer=ANSWER)
        question.write({"state": "open"})
        self.assertEqual(self._answer_stamps(question), [("open", False, False)])
        self.assertEqual(question.answer, ANSWER)
        with freeze_time(LATER):
            question.with_user(self.user_dev).write({"answer": "<p>Changed after all.</p>"})
        self.assertEqual(self._answer_stamps(question), [("answered", self.user_dev.id, LATER)])

    def test_dropping_leaves_the_stamp(self):
        with freeze_time(EARLIER):
            answered_question = self.Question.with_user(self.user_dev).create(
                {"question": QUESTION, "answer": ANSWER, "task_id": self.task.id},
            )
        open_question = self._new_question()
        (answered_question | open_question).write({"state": "dropped"})
        open_question.write({"answer": ANSWER})
        self.assertEqual(
            self._answer_stamps(answered_question | open_question),
            [("dropped", self.user_dev.id, EARLIER), ("dropped", False, False)],
        )

    def test_values_given_with_the_change_win(self):
        with freeze_time(LATER):
            given_question = self.Question.with_user(self.user_dev).create(
                {
                    "question": QUESTION,
                    "answer": ANSWER,
                    "answered_by_id": self.user_lead.id,
                    "answered_on": EARLIER,
                    "task_id": self.task.id,
                },
            )
            cleared_question = self._new_question()
            cleared_question.with_user(self.user_dev).write({"state": "answered", "answered_by_id": False})
            kept_open_question = self._new_question(answer=ANSWER, state="open")
        self.assertEqual(
            self._answer_stamps(given_question | cleared_question | kept_open_question),
            [("answered", self.user_lead.id, EARLIER), ("answered", False, LATER), ("open", False, False)],
        )

    def test_an_answer_written_to_several_rows_answers_the_open_ones(self):
        with freeze_time(EARLIER):
            answered_question = self.Question.with_user(self.user_dev).create(
                {"question": QUESTION, "answer": ANSWER, "task_id": self.task.id},
            )
        open_question = self._new_question()
        dropped_question = self._new_question(state="dropped")
        questions = answered_question | open_question | dropped_question
        with freeze_time(LATER):
            questions.with_user(self.user_lead).write({"answer": "<p>One answer for all.</p>"})
        self.assertEqual(
            self._answer_stamps(questions),
            [
                ("answered", self.user_dev.id, EARLIER),
                ("answered", self.user_lead.id, LATER),
                ("dropped", False, False),
            ],
        )
        self.assertEqual(set(questions.mapped("answer")), {"<p>One answer for all.</p>"})

    def test_setting_answered_stamps_only_the_rows_it_answers(self):
        with freeze_time(EARLIER):
            answered_question = self.Question.with_user(self.user_dev).create(
                {"question": QUESTION, "answer": ANSWER, "task_id": self.task.id},
            )
        questions = answered_question | self._new_question()
        with freeze_time(LATER):
            questions.with_user(self.user_lead).write({"state": "answered"})
        expected_stamps = [("answered", self.user_dev.id, EARLIER), ("answered", self.user_lead.id, LATER)]
        self.assertEqual(self._answer_stamps(questions), expected_stamps)

        with freeze_time(datetime(2026, 3, 4, 8, 0)):
            questions.with_user(self.user_dev).write({"state": "answered", "answer": "<p>Settled.</p>"})

        self.assertEqual(self._answer_stamps(questions), expected_stamps, "a row answered already keeps its stamp")

    def test_html_read_as_a_whole_document_answers_a_row_only_as_stored_with_content(self):
        for answer, state in WHOLE_DOCUMENT_STATES.items():
            with self.subTest(answer=answer):
                self.assertFalse(is_blank_html(answer))
                self.assertEqual(self._new_question(answer=answer).state, state)

    def test_an_answer_the_field_stores_blank_leaves_the_row_open(self):
        for answer in ("<html></html>", '<p><iframe src="https://example.com/embed"></iframe></p>'):
            with self.subTest(answer=answer):
                created_question = self._new_question(answer=answer)
                written_question = self._new_question()
                written_question.write({"answer": answer})
                self.assertEqual(self._answer_stamps(created_question | written_question), [("open", False, False)] * 2)

    def test_media_and_icons_are_content_blank_spaces_and_items_are_not(self):
        contents = (
            '<p><span class="fa fa-check"></span></p>',
            '<p><i class="fa fa-star"></i></p>',
            '<p><iframe src="https://example.com/embed"></iframe></p>',
            '<video src="/web/content/1"></video>',
            '<p><img src="/web/image/1"></p>',
        )
        blanks = ("<p>&nbsp;</p>", "<ol><li><br></li><li></li></ol>", '<p><span class="text-muted"> </span></p>')
        self.assertEqual([is_blank_html(content) for content in contents], [False] * len(contents))
        self.assertEqual([is_blank_html(blank) for blank in blanks], [True] * len(blanks))

    def test_a_row_is_named_by_its_question(self):
        long_question = "<p>%s</p>" % " ".join(["Which"] * 30)
        long_url = "https://example.com/web#id=42&model=project.task&view_type=form&action=project.action_view_task"
        hyphenated_url = "https://my-team.example.com/web#id=42&model=project.task&view_type=form&action=project.action"
        named_questions = (
            self._new_question(question="<p>Who <b>reviews</b><br>it?</p>")
            | self._new_question(question=long_question)
            | self._new_question(question="<p>%s is it the right page?</p>" % long_url)
            | self._new_question(question="<p>%s is it the right page?</p>" % hyphenated_url)
            | self._new_question(question=BLANK_HTML, answer=ANSWER)
            | self._new_question(question=False)
        )
        self.assertEqual(
            named_questions.mapped("display_name"),
            [
                "Who reviews it?",
                " ".join(["Which"] * 12) + " ...",
                long_url[:76] + " ...",
                hyphenated_url[:76] + " ...",
                "The lead reviews it.",
                self.task.display_name,
            ],
        )

    def test_copying_a_task_copies_its_rows_with_their_values(self):
        with freeze_time(EARLIER):
            self.Question.with_user(self.user_dev).create(
                {
                    "round": "First round",
                    "question": QUESTION,
                    "answer": ANSWER,
                    "addressed_to_id": self.user_lead.id,
                    "task_id": self.task.id,
                },
            )
        self._new_question(round="First round", state="answered", answered_by_id=False, answered_on=False)
        self._new_question(answer=ANSWER).write({"state": "open"})
        self._new_question(state="dropped")
        fields_compared = ["sequence", "round", "question", "answer", "addressed_to_id", "state"]
        fields_compared += ["answered_by_id", "answered_on"]
        values = self.task.question_ids.read(fields_compared, load=None)
        for row_values in values:
            del row_values["id"]

        with freeze_time(LATER):
            task_copy = self.task.copy()

        copied_values = task_copy.question_ids.read(fields_compared, load=None)
        for row_values in copied_values:
            del row_values["id"]
        self.assertEqual(copied_values, values)
        self.assertEqual(len(self.task.question_ids), len(values), "the original keeps its own rows")
        self.assertFalse(task_copy.question_ids & self.task.question_ids)

    def test_rows_need_project_user(self):
        self._new_question()
        internal_user = self._create_user("Internal Only", "project_ai_driven_internal", "internal@example.com")
        self.assertEqual(self.Question.with_user(self.user_dev).search_count([("task_id", "=", self.task.id)]), 1)
        with self.assertRaises(AccessError):
            self.Question.with_user(internal_user).search_count([])

    def test_the_task_form_adds_a_row_answered_once_it_has_an_answer(self):
        with freeze_time(EARLIER), Form(self.task.with_user(self.user_dev)) as task_form:
            with task_form.question_ids.new() as question_form:
                question_form.question = QUESTION
                question_form.answer = ANSWER
                for field_name, value in (
                    ("state", "dropped"),
                    ("answered_by_id", self.user_lead),
                    ("answered_on", LATER),
                ):
                    with self.assertRaisesRegex(AssertionError, "readonly field %s$" % field_name):
                        setattr(question_form, field_name, value)
        self.assertEqual(self._answer_stamps(self.task.question_ids), [("answered", self.user_dev.id, EARLIER)])

    def test_the_technical_solution_page_lists_the_questions_then_the_solution(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (page,) = form_arch.xpath("//page[@name='technical_solution']")
        self.assertEqual(page.xpath("./field/@name | ./group/field/@name"), ["question_ids", "solution"])
        (question_list,) = page.xpath("./field[@name='question_ids']")
        self.assertEqual(question_list.get("mode"), "tree,kanban", "a phone gets cards")
        self.assertEqual(question_list.xpath("./tree/field/@name"), QUESTION_LIST_COLUMNS)
        self.assertLessEqual({"question", "answer"}, set(question_list.xpath("./form//field/@name")))
        self.assertEqual(
            question_list.xpath("./form//field[@readonly='1']/@name"),
            ["answered_by_id", "answered_on"],
            "the rule stamps the answer",
        )
        self.assertFalse(form_arch.xpath("//field[@name='questions' or @name='answers']"))


@tagged("post_install", "-at_install")
class TestQuestionAccess(ProjectAiDrivenCommon):
    """A global rule hides one task from everyone, whichever modules' task rules are installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.rule"].create(
            {
                "name": "Hide the hidden task",
                "model_id": cls.env["ir.model"]._get_id("project.task"),
                "domain_force": "[('name', '!=', 'Hidden Task')]",
            },
        )
        cls.hidden_task = cls.env["project.task"].create({"name": "Hidden Task", "project_id": cls.project.id})
        cls.both_tasks = cls.task | cls.hidden_task
        Question = cls.env["project.task.question"]
        cls.hidden_question = Question.create({"question": "<p>Hidden?</p>", "task_id": cls.hidden_task.id})
        cls.visible_question = Question.create({"question": "<p>Visible?</p>", "task_id": cls.task.id})
        cls.as_developer = Question.with_user(cls.user_dev)

    def test_rows_of_a_task_the_user_cannot_read_are_not_found(self):
        domain = [("task_id", "in", self.both_tasks.ids)]
        self.assertEqual(self.as_developer.search(domain), self.visible_question)
        self.assertEqual(self.as_developer.search_count(domain), 1)
        groups = self.as_developer.read_group(domain, ["task_id"], ["task_id"])
        self.assertEqual([(group["task_id"][0], group["task_id_count"]) for group in groups], [(self.task.id, 1)])

    def test_rows_of_a_task_the_user_cannot_read_are_not_read_written_or_deleted(self):
        hidden_question = self.hidden_question.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_question.read(["question"])
        with self.assertRaises(AccessError):
            hidden_question.write({"answer": ANSWER})
        with self.assertRaises(AccessError):
            hidden_question.unlink()
        self.assertRecordValues(self.hidden_question, [{"state": "open", "answer": False}])
        visible_question = self.visible_question.with_user(self.user_dev)
        visible_question.write({"answer": ANSWER})
        self.assertEqual(visible_question.state, "answered")
        visible_question.unlink()
        self.assertFalse(self.visible_question.exists())

    def test_rows_cannot_be_added_to_a_task_the_user_cannot_read(self):
        with self.assertRaises(AccessError):
            self.as_developer.create({"question": QUESTION, "task_id": self.hidden_task.id})
        self.assertEqual(self.as_developer.create({"question": QUESTION, "task_id": self.task.id}).task_id, self.task)

    def test_sudo_reaches_every_row(self):
        domain = [("task_id", "in", self.both_tasks.ids)]
        self.assertEqual(
            self.as_developer.sudo().search(domain),
            self.hidden_question | self.visible_question,
        )


@tagged("post_install", "-at_install")
class TestQuestionConversion(ProjectAiDrivenCommon):
    """The two Html columns are added where the database has none: with this
    module alone, nothing else declares them. What the database holds besides
    the tasks of a test is converted first, so a run converts those alone.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for column in QUESTION_COLUMNS:
            cls.env.cr.execute("ALTER TABLE project_task ADD COLUMN IF NOT EXISTS %s text" % column)
        convert_questions_to_rows(cls.env.cr)

    def _store(self, questions_and_answers, **task_vals):
        """A task holding questions and answers as the two columns do, written behind the ORM."""
        task = self.env["project.task"].create({"name": "Asked Task", "project_id": self.project.id, **task_vals})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE project_task SET questions = %s, answers = %s WHERE id = %s",
            (*questions_and_answers, task.id),
        )
        self.env.invalidate_all()
        return task

    def _rows(self, task):
        return [
            (row.round, str(row.question or "") or False, str(row.answer or "") or False, row.state)
            for row in task.question_ids
        ]

    def _convert(self):
        """Return the cases converted and the rows inserted by a run, and the line it logs."""
        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            case_counts, state_counts = convert_questions_to_rows(self.env.cr)
        converted_counts = {case: case_counts[case] for case in CONVERTED_CASES}
        return converted_counts, state_counts, logs.records[0].getMessage()

    @contextmanager
    def _without_question_columns(self):
        self.env.flush_all()
        for column in QUESTION_COLUMNS:
            rename_column(self.env.cr, "project_task", column, "hidden_%s" % column)
        try:
            yield
        finally:
            for column in QUESTION_COLUMNS:
                rename_column(self.env.cr, "project_task", "hidden_%s" % column, column)

    def test_paired_lists_give_a_row_per_pair(self):
        one_list_task = self._store(ONE_LIST, active=False)
        two_rounds_task = self._store(TWO_ROUNDS)

        converted_counts, state_counts, _logged_line = self._convert()

        self.assertEqual(
            self._rows(one_list_task),
            [
                (False, "Which stage?", "Done", "answered"),
                (False, "Who reviews &amp; merges?", "The <b>lead</b>, on 12 Nov", "answered"),
            ],
            "an archived task too",
        )
        self.assertEqual(
            self._rows(two_rounds_task),
            [
                ("Alignment round, 12 Nov 2026", "Scope?", "As agreed", "answered"),
                ("Second round", "Data?", "Production", "answered"),
                ("Second round", "Users?", "Everyone", "answered"),
            ],
        )
        self.assertEqual(converted_counts, {"paired_lists": 2, "kept_whole": 0, "open_questions": 0, "answers_only": 0})
        self.assertEqual(state_counts, {"answered": 5, "open": 0})

    def test_a_pair_with_a_blank_answer_stays_open(self):
        task = self._store(("<ol><li>Scope?</li><li>Data?</li></ol>", "<ol><li>As agreed</li><li><br></li></ol>"))
        self._convert()
        self.assertEqual(
            self._rows(task),
            [(False, "Scope?", "As agreed", "answered"), (False, "Data?", False, "open")],
        )

    def test_a_pair_of_two_blank_items_gives_no_row(self):
        task = self._store(("<ol><li>Scope?</li><li><br></li></ol>", "<ol><li>As agreed</li><li></li></ol>"))
        self._convert()
        self.assertEqual(self._rows(task), [(False, "Scope?", "As agreed", "answered")])

    def test_pairs_all_blank_keep_the_task_whole(self):
        blank_pairs = ("<p>Round one</p><ol><li><br></li></ol>", "<p>Round one</p><ol><li></li></ol>")
        task = self._store(blank_pairs)
        converted_counts, _state_counts, _logged_line = self._convert()
        self.assertEqual(self._rows(task), [(False, *blank_pairs, "answered")])
        self.assertEqual(converted_counts, {"paired_lists": 0, "kept_whole": 1, "open_questions": 0, "answers_only": 0})

    def test_questions_alone_all_blank_give_one_row_of_the_whole(self):
        blank_questions = "<p>Round one</p><ul><li><br></li><li></li></ul>"
        task = self._store((blank_questions, None))
        self._convert()
        self.assertEqual(self._rows(task), [(False, blank_questions, False, "open")])

    def test_a_heading_over_blank_items_alone_keeps_the_task_whole(self):
        questions = "<p>R1</p><ul><li>Q1</li></ul><p>R2</p><ul><li></li></ul>"
        answers = "<p>R1</p><ul><li>A1</li></ul><p>R2</p><ul><li></li></ul>"
        answer_heading = (
            "<ol><li>Q1</li></ol><ol><li><br></li></ol>",
            "<ol><li>A1</li></ol><p>Later</p><ol><li></li></ol>",
        )
        headless_questions = "<ol><li>Q1</li></ol><ol><li><br></li></ol>"
        paired_task = self._store((questions, answers))
        answer_heading_task = self._store(answer_heading)
        open_task = self._store((questions, None))
        headless_task = self._store((headless_questions, None))
        self._convert()
        self.assertEqual(self._rows(paired_task), [(False, questions, answers, "answered")])
        self.assertEqual(
            self._rows(answer_heading_task), [(False, *answer_heading, "answered")], "a heading on the answer side"
        )
        self.assertEqual(self._rows(open_task), [(False, questions, False, "open")])
        self.assertEqual(self._rows(headless_task), [(False, "Q1", False, "open")], "no heading, no text lost")

    def test_an_answer_heading_over_blank_answers_alone_keeps_the_task_whole(self):
        empty_answers = ("<p>R1</p><ol><li>Q1</li></ol>", "<p>Later</p><ol><li></li></ol>")
        break_answers = ("<p>R1</p><ol><li>Q1</li></ol>", "<p>Later</p><ol><li><br></li></ol>")
        empty_task = self._store(empty_answers)
        break_task = self._store(break_answers)
        self._convert()
        self.assertEqual(self._rows(empty_task), [(False, *empty_answers, "answered")])
        self.assertEqual(self._rows(break_task), [(False, *break_answers, "answered")])

    def test_the_heading_of_an_answer_list_opens_each_of_its_answers(self):
        same_round = ("<p>Round one</p><ol><li>Scope?</li></ol>", "<p>Round one</p><ol><li>As agreed</li></ol>")
        other_round = (
            "<p>Round one</p><ol><li>Scope?</li><li>Data?</li></ol>",
            "<p><b>Answered on 13 Nov</b></p><ol><li>As agreed</li><li>Production</li></ol>",
        )
        same_round_task = self._store(same_round)
        other_round_task = self._store(other_round)
        self._convert()
        self.assertEqual(self._rows(same_round_task), [("Round one", "Scope?", "As agreed", "answered")])
        self.assertEqual(
            self._rows(other_round_task),
            [
                ("Round one", "Scope?", "<p><b>Answered on 13 Nov</b></p>As agreed", "answered"),
                ("Round one", "Data?", "<p><b>Answered on 13 Nov</b></p>Production", "answered"),
            ],
        )

    def test_an_image_is_content(self):
        screenshot = '<img src="/web/image/1">'
        image_answer_task = self._store((None, "<p>%s</p>" % screenshot))
        image_item_task = self._store(
            ("<ol><li>Scope?</li><li>Data?</li></ol>", "<ol><li>As agreed</li><li>%s</li></ol>" % screenshot),
        )
        self._convert()
        self.assertEqual(self._rows(image_answer_task), [(False, False, "<p>%s</p>" % screenshot, "answered")])
        self.assertEqual(
            self._rows(image_item_task),
            [(False, "Scope?", "As agreed", "answered"), (False, "Data?", screenshot, "answered")],
        )

    def test_a_nested_list_belongs_to_the_item_before_it(self):
        task = self._store(NESTED_LISTS)
        self._convert()
        self.assertEqual(
            self._rows(task),
            [
                (False, "Which stage?<ol><li>draft</li><li>done</li></ol>", "Done", "answered"),
                (False, "Who?", "The lead<ol><li>and tester</li></ol>", "answered"),
            ],
        )

    def test_a_nested_list_is_no_question_of_its_own(self):
        class_nested_task = self._store((NESTED_LISTS[0], None))
        unmarked_nested_task = self._store(
            ("<ol><li>Which stage?</li><li><ul><li>draft</li><li>done</li></ul></li><li>Who?</li></ol>", None),
        )
        self._convert()
        self.assertEqual(
            self._rows(class_nested_task),
            [
                (False, "Which stage?<ol><li>draft</li><li>done</li></ol>", False, "open"),
                (False, "Who?", False, "open"),
            ],
        )
        self.assertEqual(
            self._rows(unmarked_nested_task),
            [
                (False, "Which stage?<ul><li>draft</li><li>done</li></ul>", False, "open"),
                (False, "Who?", False, "open"),
            ],
            "an item holding a list and nothing else",
        )

    def test_a_blank_nested_list_adds_nothing_to_the_item_before_it(self):
        task = self._store(
            ('<ol><li>Which stage?</li><li class="oe-nested"><ol><li><br></li></ol></li><li>Who?</li></ol>', None),
        )
        self._convert()
        self.assertEqual(self._rows(task), [(False, "Which stage?", False, "open"), (False, "Who?", False, "open")])

    def test_a_list_put_right_in_a_list_belongs_to_the_item_before_it(self):
        task = self._store(
            (
                "<ol><li>Which stage?</li><ol><li>draft</li><li>done</li></ol><li>Who?</li></ol>",
                "<ol><li>Done</li><li>The lead</li></ol>",
            ),
        )
        self._convert()
        self.assertEqual(
            self._rows(task),
            [
                (False, "Which stage?<ol><li>draft</li><li>done</li></ol>", "Done", "answered"),
                (False, "Who?", "The lead", "answered"),
            ],
        )

    def test_a_list_nested_before_any_item_is_not_split(self):
        nested_first = '<ol><li class="oe-nested"><ol><li>draft</li></ol></li><li>Who?</li></ol>'
        answers = "<ol><li>Done</li><li>The lead</li></ol>"
        answered_task = self._store((nested_first, answers))
        open_task = self._store((nested_first, None))
        self._convert()
        self.assertEqual(self._rows(answered_task), [(False, nested_first, answers, "answered")])
        self.assertEqual(self._rows(open_task), [(False, nested_first, False, "open")])

    def test_lists_with_anything_else_around_them_are_kept_whole(self):
        answers = "<ol><li>As agreed</li></ol>"
        questions_per_case = {
            "text before the lists": "Asked by mail<ol><li>Scope?</li></ol>",
            "text after a list": "<ol><li>Scope?</li></ol>asked by mail",
            "text in a list before its items": "<ol>asked by mail<li>Scope?</li></ol>",
            "text in a list after an item": "<ol><li>Scope?</li>asked by mail</ol>",
            "other content in a list": "<ol><li>Scope?</li><p>Asked by mail</p></ol>",
            "a second heading": "<p>Round one</p><p>Asked by mail</p><ol><li>Scope?</li></ol>",
            "an image in a heading": '<p>Round one <img src="/web/image/1"></p><ol><li>Scope?</li></ol>',
            "an icon for a heading": '<p><span class="fa fa-star"></span></p><ol><li>Scope?</li></ol>',
            "lists inside a block": "<div><ol><li>Scope?</li></ol></div>",
            "a block holding a list before a list": (
                "<div><ol><li>Context A</li><li>Context B</li></ol></div><ol><li>Scope?</li></ol>"
            ),
            "a quote holding a list before a list": (
                "<blockquote><ul><li>Context</li></ul></blockquote><ol><li>Scope?</li></ol>"
            ),
            "a whole document": "<html><body><ol><li>Scope?</li></ol></body><body></body></html>",
        }
        tasks = {case: self._store((questions, answers)) for case, questions in questions_per_case.items()}
        self._convert()
        for case, task in tasks.items():
            self.assertEqual(self._rows(task), [(False, questions_per_case[case], answers, "answered")], case)

    def test_a_heading_reads_apart_across_line_breaks_and_cells(self):
        break_task = self._store(("<p>Round<br>12 Nov</p><ol><li>Scope?</li></ol>", None))
        table_task = self._store(
            ("<table><tr><td>Round</td><td>12 Nov</td></tr></table><ul><li>Data?</li></ul>", None),
        )
        self._convert()
        self.assertEqual(self._rows(break_task), [("Round 12 Nov", "Scope?", False, "open")])
        self.assertEqual(self._rows(table_task), [("Round 12 Nov", "Data?", False, "open")])

    def test_anything_else_with_both_sides_is_kept_whole(self):
        different_lengths_task = self._store(DIFFERENT_LENGTHS)
        free_text_task = self._store(FREE_TEXT)

        converted_counts, state_counts, _logged_line = self._convert()

        self.assertEqual(self._rows(different_lengths_task), [(False, *DIFFERENT_LENGTHS, "answered")])
        self.assertEqual(self._rows(free_text_task), [(False, *FREE_TEXT, "answered")])
        self.assertEqual(converted_counts, {"paired_lists": 0, "kept_whole": 2, "open_questions": 0, "answers_only": 0})
        self.assertEqual(state_counts, {"answered": 2, "open": 0})

    def test_questions_without_answers_stay_open(self):
        list_task = self._store(OPEN_LIST)
        text_task = self._store(OPEN_TEXT)

        converted_counts, state_counts, _logged_line = self._convert()

        self.assertEqual(
            self._rows(list_task),
            [("Round one", "Scope?", False, "open"), ("Round one", "Data?", False, "open")],
            "a blank item is no question",
        )
        self.assertEqual(self._rows(text_task), [(False, OPEN_TEXT[0], False, "open")])
        self.assertEqual(converted_counts, {"paired_lists": 0, "kept_whole": 0, "open_questions": 2, "answers_only": 0})
        self.assertEqual(state_counts, {"answered": 0, "open": 3})

    def test_blank_questions_and_answers_give_no_row(self):
        blank_task = self._store(BLANK)
        empty_task = self._store((None, None))
        converted_counts, state_counts, _logged_line = self._convert()
        self.assertFalse(blank_task.question_ids | empty_task.question_ids)
        self.assertEqual(converted_counts, dict.fromkeys(CONVERTED_CASES, 0))
        self.assertEqual(state_counts, {"answered": 0, "open": 0})

    def test_answers_alone_give_one_answered_row_without_a_question(self):
        text_task = self._store(ANSWERS_ONLY_TEXT)
        list_task = self._store(ANSWERS_ONLY_LIST)

        converted_counts, state_counts, _logged_line = self._convert()

        self.assertEqual(self._rows(text_task), [(False, False, ANSWERS_ONLY_TEXT[1], "answered")])
        self.assertEqual(self._rows(list_task), [(False, False, ANSWERS_ONLY_LIST[1], "answered")])
        self.assertEqual(converted_counts, {"paired_lists": 0, "kept_whole": 0, "open_questions": 0, "answers_only": 2})

    def test_rows_carry_no_answer_stamp_and_belong_to_the_task(self):
        task = self._store(ONE_LIST)
        self._convert()
        self.assertRecordValues(
            task.question_ids,
            [
                {
                    "project_id": self.project.id,
                    "answered_by_id": False,
                    "answered_on": False,
                    "create_uid": SUPERUSER_ID,
                    "write_uid": SUPERUSER_ID,
                },
            ]
            * 2,
        )

    def test_logs_one_line_with_the_counts(self):
        self._store(ONE_LIST)
        self._store(FREE_TEXT)
        self._store(OPEN_LIST)
        self._store(ANSWERS_ONLY_TEXT)
        with self.assertLogs(HOOKS_LOGGER, level=logging.INFO) as logs:
            case_counts, _state_counts = convert_questions_to_rows(self.env.cr)
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(
            logs.records[0].getMessage(),
            "Questions and answers converted to question rows: 1 tasks with lists paired item by item,"
            " 1 tasks with questions and answers kept whole, 1 tasks with questions and no answers,"
            " 1 tasks with answers and no questions, %d tasks with blank questions and answers,"
            " %d tasks skipped for the rows they have; rows inserted: 4 answered, 2 open"
            % (case_counts["blank"], case_counts["has_rows"]),
        )
        self.assertEqual(set(case_counts), set(QUESTION_CASES))

    def test_a_task_that_has_rows_is_skipped_and_a_second_run_inserts_nothing(self):
        asked_task = self._store(TWO_ROUNDS)
        self.env["project.task.question"].create({"question": QUESTION, "task_id": asked_task.id})
        converted_task = self._store(ONE_LIST)
        self._convert()
        rows = self._rows(asked_task) + self._rows(converted_task)
        self.assertEqual(self._rows(asked_task), [(False, QUESTION, False, "open")])

        converted_counts, state_counts, _logged_line = self._convert()

        self.assertEqual(converted_counts, dict.fromkeys(CONVERTED_CASES, 0))
        self.assertEqual(state_counts, {"answered": 0, "open": 0})
        self.assertEqual(self._rows(asked_task) + self._rows(converted_task), rows)

    def test_without_the_columns_nothing_is_done(self):
        with self._without_question_columns(), self.assertNoLogs(HOOKS_LOGGER, level=logging.WARNING):
            self.assertFalse(column_exists(self.env.cr, "project_task", "questions"))
            case_counts, state_counts = convert_questions_to_rows(self.env.cr)
        self.assertEqual(case_counts, dict.fromkeys(QUESTION_CASES, 0))
        self.assertEqual(state_counts, {"answered": 0, "open": 0})

    def test_an_update_without_the_columns_warns(self):
        script = _load_script(MIGRATION_SCRIPT)
        with self._without_question_columns(), self.assertLogs(HOOKS_LOGGER, level=logging.WARNING) as logs:
            script.migrate(self.env.cr, "16.0.2.6.1")
        self.assertEqual(
            [record.getMessage() for record in logs.records],
            ["Questions and answers not converted to question rows: project_task has no column questions, answers"],
        )

    def test_post_init_hook_converts(self):
        task = self._store(FREE_TEXT)
        post_init_hook(self.env.cr, self.env.registry)
        self.assertEqual(self._rows(task), [(False, *FREE_TEXT, "answered")])

    def test_migration_script_converts_on_update_only(self):
        script = _load_script(MIGRATION_SCRIPT)
        task = self._store(FREE_TEXT)

        script.migrate(self.env.cr, None)
        self.assertFalse(task.question_ids)

        script.migrate(self.env.cr, "16.0.2.6.1")
        self.assertEqual(self._rows(task), [(False, *FREE_TEXT, "answered")])
