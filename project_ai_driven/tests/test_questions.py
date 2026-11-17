from datetime import datetime

from freezegun import freeze_time

from odoo import Command
from odoo.exceptions import AccessError, MissingError
from odoo.tests import Form

from ..models.project_task_question import is_blank_html
from .common import ProjectAiDrivenCommon

EARLIER = datetime(2026, 3, 2, 9, 0)
LATER = datetime(2026, 3, 3, 15, 30)
QUESTION = "<p>Who reviews it?</p>"
ANSWER = "<p>The lead reviews it.</p>"
BLANK_HTML = "<p>&nbsp;<br></p>"
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
STATE_DECORATIONS = {
    "decoration-info": "state == 'open'",
    "decoration-success": "state == 'answered'",
    "decoration-muted": "state == 'dropped'",
}
# Html lxml reads as a whole document, without a body or with two, and the state it gives a row as its answer.
WHOLE_DOCUMENT_STATES = {
    "<html></html>": "open",
    "<html><body>a</body><body>b</body></html>": "answered",
    "<!DOCTYPE html>": "open",
}


class TestQuestions(ProjectAiDrivenCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Question = cls.env["project.task.question"]

    def _new_question(self, **vals):
        return self.Question.create({"question": QUESTION, "task_id": self.task.id, **vals})

    def _answer_stamps(self, questions):
        return [(question.state, question.answered_by_id.id, question.answered_on) for question in questions]

    def _row_values(self, task):
        """The rows of task in the order they show: a cached one2many keeps the order of creation."""
        self.env.invalidate_all()
        values = task.question_ids.read(QUESTION_LIST_COLUMNS, load=None)
        for row_values in values:
            del row_values["id"]
        return values

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
        self._new_question(state="dropped", sequence=5)
        values = self._row_values(self.task)

        with freeze_time(LATER):
            task_copy = self.task.copy()

        self.assertEqual(self._row_values(task_copy), values)
        self.assertEqual(len(self.task.question_ids), len(values), "the original keeps its own rows")
        self.assertFalse(task_copy.question_ids & self.task.question_ids)

    def test_copying_several_tasks_copies_each_ones_rows(self):
        other_task = self.env["project.task"].create({"name": "Other Task", "project_id": self.project.id})
        self._new_question(round="First task")
        self._new_question(round="Other task", task_id=other_task.id)
        task_copy, other_copy = (self.task | other_task).copy()
        self.assertEqual(task_copy.question_ids.mapped("round"), ["First task"])
        self.assertEqual(other_copy.question_ids.mapped("round"), ["Other task"])

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
                    with self.assertRaisesRegex(AssertionError, "readonly field '%s'$" % field_name):
                        setattr(question_form, field_name, value)
        self.assertEqual(self._answer_stamps(self.task.question_ids), [("answered", self.user_dev.id, EARLIER)])

    def test_the_task_form_changes_the_state_of_a_saved_row(self):
        question = self._new_question(answer=ANSWER)
        with Form(self.task.with_user(self.user_dev)) as task_form:
            with task_form.question_ids.edit(0) as question_form:
                question_form.state = "open"
        self.assertEqual(self._answer_stamps(question), [("open", False, False)])

    def test_the_technical_solution_page_lists_the_questions_then_the_solution(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (page,) = form_arch.xpath("//page[@name='technical_solution']")
        self.assertEqual(page.xpath("./field/@name | ./group/field/@name"), ["question_ids", "solution"])
        (question_list,) = page.xpath("./field[@name='question_ids']")
        self.assertEqual(question_list.xpath("./list/field/@name"), QUESTION_LIST_COLUMNS)
        self.assertEqual(question_list.xpath("./list/@decoration-muted"), ["state == 'dropped'"])
        (state_column,) = question_list.xpath("./list/field[@name='state']")
        self.assertEqual({key: state_column.get(key) for key in STATE_DECORATIONS}, STATE_DECORATIONS)
        self.assertLessEqual({"question", "answer"}, set(question_list.xpath("./form//field/@name")))
        self.assertEqual(
            question_list.xpath("./form//field[@readonly='1']/@name"),
            ["answered_by_id", "answered_on"],
            "the rule stamps the answer",
        )
        self.assertEqual(question_list.xpath("./form//field[@name='state']/@readonly"), ["not id"])
        self.assertFalse(form_arch.xpath("//field[@name='questions' or @name='answers']"))
        self.assertFalse({"questions", "answers"} & set(self.env["project.task"]._fields))

    def test_a_phone_gets_cards_of_the_questions(self):
        form_arch = self.env.ref("project.view_task_form2")._get_combined_arch()
        (question_list,) = form_arch.xpath("//field[@name='question_ids']")
        self.assertEqual(question_list.get("mode"), "list,kanban")
        (card_template,) = question_list.xpath("./kanban//t[@t-name='card']")
        self.assertLessEqual(
            {"round", "question", "state", "addressed_to_id", "answered_by_id", "answered_on"},
            set(card_template.xpath(".//field/@name")),
        )
        (state_badge,) = card_template.xpath(".//field[@name='state']")
        self.assertEqual(state_badge.get("widget"), "badge")
        self.assertEqual({key: state_badge.get(key) for key in STATE_DECORATIONS}, STATE_DECORATIONS)


class TestQuestionAccess(ProjectAiDrivenCommon):
    """A global restriction hides one task from everyone, whichever modules' task access is installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._restrict_task_reading("Hide the hidden task", "[('name', '!=', 'Hidden Task')]")
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
        self.assertEqual(self.as_developer.read_group(domain, ["task_id"], ["__count"]), [(self.task.id, 1)])

    def test_rows_of_an_archived_task_the_user_can_read_are_found_and_changed(self):
        self.task.active = False
        self.assertEqual(self.as_developer.search([("task_id", "in", self.both_tasks.ids)]), self.visible_question)
        self.visible_question.with_user(self.user_dev).write({"answer": ANSWER})
        self.assertEqual(self.visible_question.state, "answered")

    def test_rows_of_a_task_the_user_cannot_read_are_not_read_written_or_deleted(self):
        hidden_question = self.hidden_question.with_user(self.user_dev)
        with self.assertRaises(AccessError):
            hidden_question.read(["question"])
        # an answer is refused already by the read of the state before the write
        for vals in ({"round": "Second round"}, {"state": "dropped"}, {"answer": ANSWER}):
            with self.subTest(vals=vals), self.assertRaises(AccessError):
                hidden_question.write(vals)
        with self.assertRaises(AccessError):
            hidden_question.unlink()
        self.assertRecordValues(self.hidden_question, [{"state": "open", "answer": False, "round": False}])
        visible_question = self.visible_question.with_user(self.user_dev)
        visible_question.write({"answer": ANSWER})
        self.assertEqual(visible_question.state, "answered")
        visible_question.unlink()
        self.assertFalse(self.visible_question.exists())

    def test_a_row_deleted_meanwhile_fails_the_access_check_as_missing(self):
        """Two users with the task form open both delete the same row: the
        second save fails, as core evaluates the access domain on a deleted record.
        """
        self.visible_question.unlink()
        self.env.invalidate_all()
        with self.assertRaises(MissingError):
            self.task.with_user(self.user_dev).write({"question_ids": [Command.delete(self.visible_question.id)]})
        deleted_question = self.visible_question.with_user(self.user_dev)
        for operation in ("read", "write", "unlink"):
            self.env.invalidate_all()
            with self.subTest(operation=operation), self.assertRaises(MissingError):
                deleted_question.check_access(operation)
        self.env.invalidate_all()
        with self.assertRaises((AccessError, MissingError)):
            (deleted_question | self.hidden_question.with_user(self.user_dev)).check_access("unlink")

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
