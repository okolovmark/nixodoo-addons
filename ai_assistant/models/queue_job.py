import logging
from textwrap import shorten

from odoo import models

from odoo.addons.queue_job.job import CANCELLED, FAILED

_logger = logging.getLogger(__name__)

ANSWER_METHODS = ("_ask_ai_answer", "_app_answer")
JOB_ERROR_CHARS = 200


class QueueJob(models.Model):
    _inherit = "queue.job"

    def write(self, vals):
        result = super().write(vals)
        if vals.get("state") in (FAILED, CANCELLED):
            self._close_ai_assistant_answers(vals)
        return result

    def _close_ai_assistant_answers(self, vals):
        """Close the AI Assistant questions of answer jobs that failed or were cancelled: nothing else answers them."""
        answer_jobs = self.filtered(
            lambda job: job.model_name == "ai.assistant.exchange" and job.method_name in ANSWER_METHODS
        )
        if not answer_jobs:
            return
        if vals["state"] == FAILED:
            reason = shorten(vals.get("exc_message") or vals.get("exc_name") or "", JOB_ERROR_CHARS) or "no reason"
            error = "its answer job failed: %s" % reason
        else:
            error = "its answer job was cancelled"
        exchange_ids = [exchange_id for job in answer_jobs for exchange_id in job.records.ids]
        exchanges = self.env["ai.assistant.exchange"].sudo().browse(exchange_ids).exists()
        for exchange in exchanges:
            try:
                with self.env.cr.savepoint():
                    exchange._close_stuck(error)
            except Exception:  # noqa: BLE001
                # The job's own failure must be stored, whatever closing its question meets.
                _logger.exception("ai assistant: could not close question %s after its job ended", exchange.id)
