from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import format_datetime


class AiConversation(models.Model):
    """One conversation with an assistant, as an administrator reviews it.

    The chatter carries only what went off the normal path (a fall to another
    model, a refusal, the spend limit); a plain answer is a row of Answers.
    Each assistant adds its value to `assistant`, and for its conversations
    the exchanges and messages the figures and the transcript read; an
    override handles its own values and passes the others to super().
    The stored figures of `_compute_stats` have no dependency here: an
    assistant that overrides `_conversation_exchanges` adds the depends of
    its exchanges to `_compute_stats`, or its figures are never recomputed.
    """

    _name = "ai.conversation"
    _description = "AI conversation"
    _inherit = ["mail.thread"]
    _order = "date_last desc, id desc"
    # Lets administrators note on the deviations without write access to the record.
    _mail_post_access = "read"

    name = fields.Char(compute="_compute_name", store=True)
    assistant = fields.Selection([], required=True, readonly=True, index=True)
    date_last = fields.Datetime(string="Last activity", compute="_compute_stats", store=True)
    question_count = fields.Integer(string="Questions", compute="_compute_stats", store=True)
    cost = fields.Float(string="Cost (USD)", digits=(16, 6), compute="_compute_stats", store=True)
    model_names = fields.Char(string="Models used", compute="_compute_stats", store=True)
    fallback_count = fields.Integer(
        string="Fallbacks",
        compute="_compute_stats",
        store=True,
        help="Questions whose first Claude model did not answer them.",
    )
    transcript = fields.Html(compute="_compute_transcript")

    @api.depends("assistant")
    def _compute_name(self):
        labels = dict(self._fields["assistant"]._description_selection(self.env))
        for conversation in self:
            conversation.name = labels.get(conversation.assistant)

    def _compute_stats(self):
        # The dependencies are each assistant's, on the exchanges its override of this method names.
        for conversation in self:
            exchanges = conversation._conversation_exchanges().sorted("id")
            questions = exchanges._visitor_questions()
            conversation.date_last = max((exchange.create_date for exchange in exchanges), default=False)
            conversation.question_count = len(questions)
            conversation.cost = sum(exchanges.mapped("cost"))
            conversation.model_names = ", ".join(dict.fromkeys(name for name in exchanges.mapped("model") if name))
            conversation.fallback_count = len(questions.filtered(lambda exchange: exchange._claude_fell_back()))

    def _compute_transcript(self):
        # The bodies were sanitized when posted and the field sanitizes the whole again;
        # only the headers are text of ours to escape.
        for conversation in self:
            conversation.transcript = Markup().join(
                Markup('<p class="mb-0"><b>%s</b> <small class="text-muted">%s</small></p>%s')
                % (
                    message.author_id.name or message.author_guest_id.name or _("Visitor"),
                    format_datetime(self.env, message.date),
                    Markup(message.body or ""),
                )
                for message in conversation._transcript_messages()
            )

    def _conversation_exchanges(self):
        """The exchanges the conversation's figures count, a chat classification's included."""
        self.ensure_one()
        return self.env["ai.exchange.mixin"]

    def _transcript_messages(self):
        """The messages the transcript shows, oldest first."""
        self.ensure_one()
        return self.env["mail.message"]

    def _post_deviations(self, lines):
        """One note by OdooBot listing what went off the normal path; nothing without lines."""
        if not self or not lines:
            return
        self.ensure_one()
        self.sudo().message_post(
            body=Markup("<ul>%s</ul>") % Markup().join(Markup("<li>%s</li>") % line for line in lines),
            author_id=self.env.ref("base.partner_root").id,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )
