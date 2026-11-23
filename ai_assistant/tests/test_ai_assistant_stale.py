from datetime import timedelta
from unittest.mock import patch

from psycopg2 import errors as pg_errors

from odoo.tests import tagged
from odoo.tools import plaintext2html

from .test_ai_assistant_app import REFUSED, UNAVAILABLE, AppCase
from odoo.addons.ai_assistant.models.ai_assistant_exchange import (
    ANSWER_NOTIFICATION,
    APP_ANSWER_NOTIFICATION,
    ASSISTANT_GROUP,
    FINISH_TRIES,
    PENDING_STALE_SECONDS,
    STALE_ERROR,
)
from odoo.addons.ai_assistant.models.ai_assistant_tools import ASSISTANT_PREFIX
from odoo.addons.queue_job.job import Job

QUEUE_JOB_LOGGER = "odoo.addons.ai_assistant.models.queue_job"
EXCHANGE_LOGGER = "odoo.addons.ai_assistant.models.ai_assistant_exchange"
FAILURE = {
    "exc_name": "psycopg2.errors.SerializationFailure",
    "exc_message": "could not serialize access due to concurrent update",
    "exc_info": "Traceback (most recent call last): ...",
}


@tagged("post_install", "-at_install")
class TestStaleAnswers(AppCase):
    def _age(self, exchange, seconds):
        self.env.cr.execute(
            "UPDATE ai_assistant_exchange SET create_date = create_date - %s * interval '1 second' WHERE id = %s",
            (seconds, exchange.id),
        )
        self.env.invalidate_all()

    def _bot_notes(self, record):
        return record.message_ids.filtered(lambda message: message.author_id == self.bot_partner)

    def _stored_job(self, exchange, method):
        """The answer job of `exchange` as queue_job stores it, which `_ask` and `_app_ask` trap."""
        getattr(exchange.with_delay(identity_key=exchange._answer_job_key()), method)()
        return self.env["queue.job"].search([("identity_key", "=", exchange._answer_job_key())])

    def _fail(self, job_record, **failure):
        """Store the job as failed, as queue_job's runner does when the job raises."""
        job = Job.load(self.env, job_record.uuid)
        job.set_failed(**(failure or FAILURE))
        job.store()

    def test_a_stale_ask_ai_question_gets_the_unavailable_note_and_its_job_never_answers(self):
        stale, trap = self._ask()
        young, _young_trap = self._ask("Asked a minute ago?")
        answered = self._answered(question="Answered long ago?")
        self._age(stale, PENDING_STALE_SECONDS + 60)
        self._age(answered, PENDING_STALE_SECONDS + 60)
        self._age(young, 60)
        payloads_before = len(self._bus_payloads(self.employee.partner_id, ANSWER_NOTIFICATION))

        closed = self.env["ai.assistant.exchange"]._cron_close_stale()

        self.assertEqual(closed & (stale | young | answered), stale)
        self.assertEqual((stale.state, stale.error), ("unavailable", STALE_ERROR % PENDING_STALE_SECONDS))
        self.assertEqual((young.state, answered.state), ("pending", "answered"))
        note = stale.answer_message_id
        self.assertEqual((note.author_id, note.subtype_id), (self.bot_partner, self.ask_ai_subtype))
        self.assertIn(UNAVAILABLE, note.body)
        self.assertEqual(self._notified(note), 0, "the note notifies nobody")
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, ANSWER_NOTIFICATION)[payloads_before:],
            [{"model": "res.partner", "id": self.partner.id}],
            "the asker's open chatter loads the note",
        )

        with self._claude() as claude:
            trap.perform_enqueued_jobs()
        claude.factory.assert_not_called()
        self.assertEqual(stale.state, "unavailable", "a job that runs after all finds the question closed")
        self.assertEqual(self._bot_notes(self.partner), note | answered.answer_message_id)

    def test_a_stale_app_question_is_closed_and_the_composer_enabled_again(self):
        conversation, stale, _trap = self._app_ask()
        self._age(stale, PENDING_STALE_SECONDS + 60)
        payloads_before = len(self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION))

        self.env["ai.assistant.exchange"]._cron_close_stale()

        self.assertEqual((stale.state, stale.error), ("unavailable", STALE_ERROR % PENDING_STALE_SECONDS))
        self.assertFalse(stale.answer_message_id, "the app posts no note on any record")
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)[payloads_before:],
            [self._app_payload(conversation, stale)],
        )
        last_message = conversation.with_user(self.employee).app_messages()[-1]
        self.assertEqual(last_message["state"], "unavailable", "the app waits only while the last one is pending")
        self.assertIn(UNAVAILABLE, last_message["answer_html"])
        _same_conversation, follow_up, _follow_up_trap = self._app_ask("Again?", conversation=conversation)
        self.assertEqual((follow_up.conversation_id, follow_up.state), (conversation, "pending"))

    def test_a_stale_app_question_of_an_asker_who_left_the_group_closes_as_refused(self):
        _conversation, stale, _trap = self._app_ask()
        self._age(stale, PENDING_STALE_SECONDS + 60)
        self._leave_group(ASSISTANT_GROUP, self.employee)

        self.env["ai.assistant.exchange"]._cron_close_stale()

        self.assertEqual(
            (stale.state, stale.is_refused, stale.error), ("unavailable", True, STALE_ERROR % PENDING_STALE_SECONDS)
        )
        [payload] = self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)[-1:]
        self.assertTrue(payload["message"]["is_refused"], "the open app reloads nothing the asker may not read")
        self.assertEqual(payload["message"]["answer_html"], plaintext2html(REFUSED))

    def test_a_stale_close_that_keeps_conflicting_is_retried_as_stale(self):
        conversation, stale, _trap = self._app_ask()
        self._age(stale, PENDING_STALE_SECONDS + 60)
        exchange_cls = type(stale)
        original_close = exchange_cls._close_stuck
        closes = []

        def close_after_conflicts(record, error):
            if record != stale:
                return original_close(record, error)
            closes.append(error)
            if len(closes) <= FINISH_TRIES:
                raise pg_errors.SerializationFailure()
            return original_close(record, error)

        with patch.object(exchange_cls, "_close_stuck", close_after_conflicts):
            self.env["ai.assistant.exchange"]._cron_close_stale()
        self.assertEqual(closes, [STALE_ERROR % PENDING_STALE_SECONDS] * (FINISH_TRIES + 1), "never CONFLICT_ERROR")
        self.assertEqual((stale.state, stale.error), ("unavailable", STALE_ERROR % PENDING_STALE_SECONDS))
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION)[-1:],
            [self._app_payload(conversation, stale)],
        )

    def test_pending_stale_seconds_sets_how_long_a_question_may_wait(self):
        parameter = self.env.ref("ai_assistant.config_pending_stale")
        self.assertEqual((parameter.key, parameter.value), (f"{ASSISTANT_PREFIX}.pending_stale_seconds", "1800"))
        exchange, _trap = self._ask()
        self._age(exchange, 120)
        self.env["ai.assistant.exchange"]._cron_close_stale()
        self.assertEqual(exchange.state, "pending", "two minutes is not stale by default")
        self.env["ir.config_parameter"].sudo().set_str(f"{ASSISTANT_PREFIX}.pending_stale_seconds", "60")
        self.env["ai.assistant.exchange"]._cron_close_stale()
        self.assertEqual((exchange.state, exchange.error), ("unavailable", STALE_ERROR % 60))

    def test_a_question_whose_answer_job_runs_is_never_closed(self):
        exchange, _trap = self._ask()
        job_record = self._stored_job(exchange, "_ask_ai_answer")
        self._age(exchange, PENDING_STALE_SECONDS * 3)
        long_ago = self.env.cr.now() - timedelta(seconds=PENDING_STALE_SECONDS * 2)
        job_record.write({"state": "started", "date_enqueued": long_ago, "date_started": long_ago})
        self.assertFalse(self.env["ai.assistant.exchange"]._cron_close_stale() & exchange)
        self.assertEqual(exchange.state, "pending", "a job still in its Claude calls is never cut short")

    def test_the_wait_counts_from_the_answer_job_s_start_or_enqueueing(self):
        exchange, _trap = self._ask()
        job_record = self._stored_job(exchange, "_ask_ai_answer")
        self._age(exchange, PENDING_STALE_SECONDS * 3)
        now = self.env.cr.now()
        job_record.write({"state": "enqueued", "date_enqueued": now - timedelta(seconds=60)})
        self.env["ai.assistant.exchange"]._cron_close_stale()
        self.assertEqual(exchange.state, "pending", "waiting a minute for a slot is not stale")
        long_ago = now - timedelta(seconds=PENDING_STALE_SECONDS + 60)
        job_record.write({"state": "done", "date_started": long_ago, "date_done": long_ago})
        self.env["ai.assistant.exchange"]._cron_close_stale()
        self.assertEqual((exchange.state, exchange.error), ("unavailable", STALE_ERROR % PENDING_STALE_SECONDS))

    def test_one_question_that_fails_to_close_never_stops_the_others(self):
        older, _trap = self._ask("Older?")
        newer, _newer_trap = self._ask("Newer?")
        for exchange in (older, newer):
            self._age(exchange, PENDING_STALE_SECONDS + 60)
        exchange_cls = type(older)
        original_close = exchange_cls._close_stuck

        def close_or_fail(record, error):
            if record.id == newer.id:
                raise RuntimeError("boom")
            return original_close(record, error)

        # The newer goes first, by the model's order: its failure must not stop the older.
        with patch.object(exchange_cls, "_close_stuck", close_or_fail), self.assertLogs(EXCHANGE_LOGGER, "ERROR"):
            self.env["ai.assistant.exchange"]._cron_close_stale()
        self.assertEqual((newer.state, older.state), ("pending", "unavailable"))

    def test_a_stale_question_on_a_deleted_record_is_closed_without_a_note(self):
        partner = self.env["res.partner"].create({"name": "AI Assistant Short-lived"})
        exchange, _trap = self._ask(record=partner)
        partner.unlink()
        exchange._close_stuck("its answer job failed: boom")
        self.assertEqual((exchange.state, exchange.error), ("unavailable", "its answer job failed: boom"))
        self.assertFalse(exchange.answer_message_id)

    def test_close_stuck_leaves_a_closed_row_as_it_was(self):
        exchange = self._answered()
        exchange._close_stuck("too late")
        self.assertEqual((exchange.state, exchange.error), ("answered", False))
        self.assertEqual(self._bot_notes(self.partner), exchange.answer_message_id)

    def test_notify_helpers_send_what_the_web_client_listens_for(self):
        exchange, _trap = self._ask()
        conversation, app_exchange, _app_trap = self._app_ask()
        exchange._ask_ai_notify()
        app_exchange._app_notify()
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, ANSWER_NOTIFICATION),
            [{"model": "res.partner", "id": self.partner.id}],
        )
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION),
            [self._app_payload(conversation, app_exchange)],
        )

    def test_a_failed_answer_job_closes_its_question_at_once(self):
        exchange, _trap = self._ask()
        job_record = self._stored_job(exchange, "_ask_ai_answer")
        self.assertEqual(job_record.channel, "root.ai_assistant")
        self._fail(job_record)
        self.assertEqual(job_record.state, "failed")
        self.assertEqual(
            (exchange.state, exchange.error),
            ("unavailable", "its answer job failed: could not serialize access due to concurrent update"),
        )
        self.assertIn(UNAVAILABLE, exchange.answer_message_id.body)

    def test_a_cancelled_answer_job_closes_its_question(self):
        conversation, exchange, _trap = self._app_ask()
        job_record = self._stored_job(exchange, "_app_answer")
        job_record.button_cancelled()
        self.assertEqual(job_record.state, "cancelled")
        self.assertEqual((exchange.state, exchange.error), ("unavailable", "its answer job was cancelled"))
        self.assertEqual(
            self._bus_payloads(self.employee.partner_id, APP_ANSWER_NOTIFICATION),
            [self._app_payload(conversation, exchange)],
        )

    def test_close_ai_assistant_answers_leaves_other_jobs_alone(self):
        exchange, _trap = self._ask()
        other_job = self.env["queue.job"].search(
            [("uuid", "=", self.partner.with_delay().write({"comment": "x"}).uuid)]
        )
        self._fail(other_job)
        other_job._close_ai_assistant_answers({"state": "failed"})
        self.assertEqual((other_job.state, exchange.state), ("failed", "pending"))
        job_record = self._stored_job(exchange, "_ask_ai_answer")
        job_record._close_ai_assistant_answers({"state": "failed"})
        self.assertEqual((exchange.state, exchange.error), ("unavailable", "its answer job failed: no reason"))

    def test_a_failure_closing_the_question_never_stops_the_job_s_failure(self):
        exchange, _trap = self._ask()
        job_record = self._stored_job(exchange, "_ask_ai_answer")
        with (
            patch.object(type(exchange), "_close_stuck", side_effect=RuntimeError("boom")),
            self.assertLogs(QUEUE_JOB_LOGGER, "ERROR"),
        ):
            self._fail(job_record)
        self.assertEqual(job_record.state, "failed")
        self.assertEqual(exchange.state, "pending", "left for the stale cron")

    def test_the_stale_cron_runs_every_five_minutes(self):
        cron = self.env.ref("ai_assistant.cron_close_stale_answers").with_context(active_test=False)
        self.assertEqual(
            (cron.model_id.model, cron.code, cron.interval_number, cron.interval_type),
            ("ai.assistant.exchange", "model._cron_close_stale()", 5, "minutes"),
        )
