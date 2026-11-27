from textwrap import shorten

from markupsafe import Markup
from psycopg2 import errors as pg_errors

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, MissingError, UserError
from odoo.tools import format_datetime, plaintext2html
from odoo.tools.sql import index_exists

from odoo.addons.ai_claude.models.ai_claude_gateway import quiet_sql_errors

from .ai_assistant_exchange import (
    APP_ACTION_TAG,
    APP_MENU,
    ASK_AI_SUBTYPE,
    ASSISTANT_GROUP,
    ASSISTANT_PARTNER,
    AskAiConversationRace,
)

APP_TITLE_CHARS = 80
APP_LIST_LIMIT = 200
ASK_AI_RECORD_INDEX = "ai_conversation_ask_ai_record_uniq"


class AiConversation(models.Model):
    _inherit = "ai.conversation"

    assistant = fields.Selection(
        selection_add=[("ask_ai", "Ask AI"), ("app", "AI Assistant")],
        ondelete={"ask_ai": "cascade", "app": "cascade"},
    )
    user_id = fields.Many2one("res.users", string="Employee", readonly=True, index="btree_not_null")
    res_model = fields.Char(
        string="Record model",
        readonly=True,
        index=True,
        help="Ask AI: the record asked about. AI Assistant: the screen its latest question was asked from.",
    )
    res_id = fields.Many2oneReference(string="Record ID", model_field="res_model", readonly=True, index=True)
    assistant_exchange_ids = fields.One2many(
        "ai.assistant.exchange", "conversation_id", string="AI Assistant answers", readonly=True
    )
    not_helpful_count = fields.Integer(
        string="Not helpful",
        compute="_compute_not_helpful_count",
        store=True,
        help="Answers in this conversation that their asker rated thumbs-down.",
    )

    def init(self):
        super().init()
        # One Ask AI conversation per record, shared by every asker.
        if not index_exists(self.env.cr, ASK_AI_RECORD_INDEX):
            self.env.cr.execute(
                "CREATE UNIQUE INDEX %s ON %s (res_model, res_id) WHERE assistant = 'ask_ai'"
                % (ASK_AI_RECORD_INDEX, self._table)
            )

    @api.depends("res_model", "res_id", "assistant_exchange_ids.question")
    def _compute_name(self):
        ask_ai_conversations = self.filtered(lambda conversation: conversation.assistant == "ask_ai")
        app_conversations = self.filtered(lambda conversation: conversation.assistant == "app")
        super(AiConversation, self - ask_ai_conversations - app_conversations)._compute_name()
        for conversation in ask_ai_conversations:
            conversation.name = _("Ask AI: %s") % conversation._ask_ai_record_name()
        for conversation in app_conversations:
            first_question = conversation.assistant_exchange_ids.sorted("id")[:1].question or ""
            conversation.name = shorten(first_question, APP_TITLE_CHARS, placeholder="...") or _("New chat")

    @api.depends("assistant_exchange_ids.cost", "assistant_exchange_ids.model", "assistant_exchange_ids.attempts")
    def _compute_stats(self):
        return super()._compute_stats()

    @api.depends(
        "res_model",
        "res_id",
        "assistant_exchange_ids.answer",
        "assistant_exchange_ids.state",
        "assistant_exchange_ids.is_refused",
        "assistant_exchange_ids.attempts",
        "assistant_exchange_ids.reply_to_id",
    )
    def _compute_transcript(self):
        app_conversations = self.filtered(lambda conversation: conversation.assistant == "app")
        super(AiConversation, self - app_conversations)._compute_transcript()
        bot_name = self.env.ref(ASSISTANT_PARTNER).sudo().name
        header = Markup('<p class="mb-0"><b>%s</b> <small class="text-muted">%s</small></p>%s')
        reply_line = Markup('<p class="text-muted mb-0">%s</p>')
        for conversation in app_conversations:
            blocks = []
            for exchange in conversation.assistant_exchange_ids.sorted("id"):
                date = format_datetime(self.env, exchange.create_date)
                if not exchange.reply_to_id:
                    question_html = plaintext2html(exchange.question or "")
                    blocks.append(header % (conversation.user_id.name or "", date, question_html))
                if exchange.state == "pending":
                    continue
                answer_html = exchange._answer_html()
                if exchange.reply_to_id:
                    asked_date = format_datetime(self.env, exchange._asked_exchange().create_date)
                    answer_html = reply_line % (_("New answer to the question of %s") % asked_date) + answer_html
                blocks.append(header % (bot_name, date, answer_html))
            conversation.transcript = Markup().join(blocks)

    @api.depends("assistant_exchange_ids.feedback")
    def _compute_not_helpful_count(self):
        for conversation in self:
            conversation.not_helpful_count = len(
                conversation.assistant_exchange_ids.filtered(lambda exchange: exchange.feedback == "down")
            )

    def _conversation_exchanges(self):
        self.ensure_one()
        if self.assistant in ("ask_ai", "app"):
            return self.assistant_exchange_ids
        return super()._conversation_exchanges()

    def _transcript_messages(self):
        """The record's Ask AI notes, questions and answers from every asker, oldest first."""
        self.ensure_one()
        if self.assistant != "ask_ai":
            return super()._transcript_messages()
        return (
            self.env["mail.message"]
            .sudo()
            .search(
                [
                    ("model", "=", self.res_model),
                    ("res_id", "=", self.res_id),
                    ("subtype_id", "=", self.env.ref(ASK_AI_SUBTYPE).id),
                ],
                order="id",
            )
        )

    def unlink(self):
        """An AI Assistant conversation goes with its questions and answers: its owner deletes it to forget them.
        Ask AI exchanges stay, as their notes stay on the record."""
        self.check_access_rights("unlink")
        self.check_access_rule("unlink")
        self.sudo().filtered(lambda conversation: conversation.assistant == "app").assistant_exchange_ids.unlink()
        return super().unlink()

    def _get_access_action(self, access_uid=None, force_website=False):
        """A mail's View button takes the owner of an AI Assistant conversation to the app: the form is the
        administrators' review screen. /mail/view follows only a URL or a window action."""
        self.ensure_one()
        if self._opens_in_app(access_uid or self.env.uid):
            return {"type": "ir.actions.act_url", "url": self._app_url(), "target": "self"}
        return super()._get_access_action(access_uid=access_uid, force_website=force_website)

    def app_open_action(self):
        """The app opened on the conversation when it is the current user's own AI Assistant conversation,
        otherwise False, also for one deleted since: the Inbox's link to the conversation then opens its form."""
        self.ensure_one()
        if not self.exists() or not self._opens_in_app(self.env.uid):
            return False
        return {
            "type": "ir.actions.client",
            "tag": APP_ACTION_TAG,
            "name": _("AI Assistant"),
            "params": {"conversation_id": self.id},
        }

    @api.model
    def app_conversations(self, conversation_id=False):
        """The current employee's AI Assistant conversations, the latest first, and last `conversation_id`
        when it is one of theirs too old for the list: a link opens it."""
        domain = [("assistant", "=", "app"), ("user_id", "=", self.env.uid)]
        conversations = self.search(domain, limit=APP_LIST_LIMIT)
        if conversation_id and conversation_id not in conversations.ids:
            conversations |= self.search([*domain, ("id", "=", conversation_id)])
        return [conversation._app_summary() for conversation in conversations]

    @api.model
    def app_panel(self, screen=None):
        """What the quick panel opens on `screen` ({"res_model", "res_id"}): the employee's latest
        conversation while its screen is this one, otherwise none, so the next question starts one."""
        res_model, res_id = self._app_screen(screen)
        latest = self.search([("assistant", "=", "app"), ("user_id", "=", self.env.uid)], limit=1)
        is_same_screen = bool(latest) and (latest.res_model or False, latest.res_id or False) == (res_model, res_id)
        return {
            "conversation": latest._app_summary() if is_same_screen else False,
            "screen_name": self._app_screen_name(res_model, res_id),
        }

    def app_messages(self):
        """The conversation's questions and answers, oldest first."""
        self._app_check(owner=False)
        return [exchange._app_message() for exchange in self.sudo().assistant_exchange_ids.sorted("id")]

    @api.model
    def app_ask(self, question, conversation_id=False, screen=None, allowed_company_ids=None):
        """Ask the AI Assistant in the app, in the employee's conversation `conversation_id` or in a new one.

        `screen` ({"res_model", "res_id"}) is what the quick panel was opened
        on; from the full page it is None, and the conversation keeps the
        screen of its latest question. Returns the conversation and the new
        message, whose answer a queue job writes.
        """
        user = self.env.user
        if not user.has_group(ASSISTANT_GROUP):
            raise AccessError(_("%s may not use the AI Assistant.") % user.name)
        question = self.env["ai.assistant.exchange"]._clean_question(question)
        if conversation_id:
            conversation = self.browse(conversation_id)
            conversation._app_check(owner=True)
            if screen is None:
                screen = {"res_model": conversation.res_model, "res_id": conversation.res_id}
        else:
            conversation = (
                self.sudo()
                .with_context(mail_create_nosubscribe=True, mail_create_nolog=True)
                .create({"assistant": "app", "user_id": user.id})
            )
        res_model, res_id = self._app_screen(screen)
        conversation.sudo().write({"res_model": res_model, "res_id": res_id})
        exchange = self.env["ai.assistant.exchange"]._app_enqueue(conversation, question, allowed_company_ids)
        return {"conversation": conversation._app_summary(), "message": exchange._app_message()}

    def app_feedback(self, exchange_id, feedback):
        """The asker's thumbs-up or thumbs-down on one answer of the conversation; the same one again takes it back."""
        self._app_check(owner=True)
        if feedback not in ("up", "down"):
            raise UserError(_("Rate an answer up or down."))
        exchange = self.sudo().assistant_exchange_ids.filtered(lambda exchange: exchange.id == exchange_id)
        if exchange.user_id != self.env.user:
            raise AccessError(_("Only the employee who asked rates an answer."))
        if exchange.state != "answered":
            raise UserError(_("Only an answer can be rated."))
        exchange.feedback = False if exchange.feedback == feedback else feedback
        return exchange._app_message()

    def _app_check(self, owner):
        """Fail unless this is an AI Assistant conversation the current user may read, by the record rules,
        and, with `owner`, one of their own."""
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        if self.assistant != "app" or (owner and self.user_id != self.env.user):
            raise AccessError(_("This is not one of your AI Assistant conversations."))

    def _app_summary(self):
        self.ensure_one()
        return {
            "id": self.id,
            "name": self.name,
            "date_last": self.date_last or self.create_date,
            "res_model": self.res_model or False,
            "res_id": self.res_id or False,
        }

    def _opens_in_app(self, uid):
        self.ensure_one()
        conversation = self.sudo()
        return conversation.assistant == "app" and conversation.user_id.id == uid

    def _app_url(self):
        """The link that opens the AI Assistant app on this conversation."""
        self.ensure_one()
        return "%s/web#action=%s&conversation_id=%s&menu_id=%s" % (
            self.get_base_url(),
            APP_ACTION_TAG,
            self.id,
            self.env.ref(APP_MENU).id,
        )

    @api.model
    def _app_screen(self, screen):
        """(res_model, res_id) of a screen the web client sent, False for what it does not name."""
        screen = screen or {}
        res_model = screen.get("res_model") or False
        if res_model not in self.env:
            return False, False
        res_id = screen.get("res_id")
        return res_model, res_id if isinstance(res_id, int) and res_id > 0 else False

    @api.model
    def _app_screen_name(self, res_model, res_id):
        """The screen as the employee sees it: the model, and the record's name when they may read it."""
        if not res_model:
            return False
        model_name = self.env["ir.model"]._get(res_model).name
        if not res_id:
            return model_name
        try:
            record_name = self.env[res_model].browse(res_id).display_name
        except (AccessError, MissingError):
            return model_name
        return f"{model_name}: {record_name}" if record_name else model_name

    def _ask_ai_record_name(self):
        self.ensure_one()
        if self.res_model not in self.env:
            return f"{self.res_model} {self.res_id}"
        record = self.env[self.res_model].sudo().browse(self.res_id).exists()
        return record.display_name if record else f"{self.env[self.res_model]._description} {self.res_id}"

    @api.model
    def _for_ask_ai(self, res_model, res_id):
        """The record's Ask AI conversation, one for every asker, created with its first question.

        A concurrent first question on the record loses on the unique index.
        The winner committed after this transaction's snapshot, so only a new
        cursor sees it: AskAiConversationRace is raised, the violation rolled
        back to a savepoint, for the caller to retry there.
        """
        Conversation = self.sudo()
        conversation = Conversation.search(
            [("assistant", "=", "ask_ai"), ("res_model", "=", res_model), ("res_id", "=", res_id)], limit=1
        )
        if conversation:
            return conversation
        try:
            with quiet_sql_errors(pg_errors.UniqueViolation), self.env.cr.savepoint():
                return Conversation.with_context(mail_create_nosubscribe=True, mail_create_nolog=True).create(
                    {"assistant": "ask_ai", "res_model": res_model, "res_id": res_id}
                )
        except pg_errors.UniqueViolation as error:
            raise AskAiConversationRace() from error
