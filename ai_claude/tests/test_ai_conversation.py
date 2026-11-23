from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

ASSISTANT = ("ai_claude_test", "Test assistant")


@tagged("post_install", "-at_install")
class TestAiConversation(TransactionCase):
    """The conversation of an assistant that names neither its conversations nor their exchanges."""

    def setUp(self):
        super().setUp()
        Conversation = self.env["ai.conversation"].with_context(
            lang=None, mail_create_nolog=True, mail_create_nosubscribe=True
        )
        field = Conversation._fields["assistant"]
        selection = (*field.selection, ASSISTANT)
        self.startPatcher(patch.multiple(field, selection=selection, _selection=dict(selection)))
        self.conversation = Conversation.create({"assistant": ASSISTANT[0]})

    def test_a_conversation_is_named_after_its_assistant(self):
        self.assertEqual(self.conversation.name, "Test assistant")

    def test_without_exchanges_or_messages_there_is_nothing_to_count_or_show(self):
        conversation = self.conversation
        self.assertEqual((conversation.question_count, conversation.fallback_count, conversation.cost), (0, 0, 0))
        self.assertFalse(conversation.model_names)
        self.assertFalse(conversation.date_last)
        self.assertFalse(conversation.transcript)

    def test_the_deviations_are_one_note_by_odoobot(self):
        self.conversation._post_deviations([])
        self.assertFalse(self.conversation.message_ids, "no lines, no note")

        self.conversation._post_deviations(["claude-haiku-5-5 did not <b>answer</b>.", "No price is known."])
        note = self.conversation.message_ids
        self.assertEqual(len(note), 1)
        self.assertEqual(note.author_id, self.env.ref("base.partner_root"))
        self.assertEqual(note.subtype_id, self.env.ref("mail.mt_note"))
        self.assertEqual(
            note.body,
            "<ul><li>claude-haiku-5-5 did not &lt;b&gt;answer&lt;/b&gt;.</li><li>No price is known.</li></ul>",
        )
