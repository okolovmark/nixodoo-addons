/** @odoo-module **/

import { deserializeDateTime, formatDate } from "@web/core/l10n/dates";
import { useBus, useService } from "@web/core/utils/hooks";

import { Component, markup, onWillStart, onWillUpdateProps, useEffect, useRef, useState } from "@odoo/owl";

export const APP_ANSWER_NOTIFICATION = "ai_assistant/app_answer";

/**
 * One AI Assistant conversation: its questions and answers and the composer.
 * Without a conversation it is a new chat, which the first question creates.
 * Answers arrive from a queue job; the bus carries each one once it is written.
 */
export class AiAssistantThread extends Component {
    setup() {
        this.orm = useService("orm");
        this.user = useService("user");
        this.state = useState({ messages: [], draft: "", isSending: false });
        this.messagesRef = useRef("messages");
        this.composerRef = useRef("composer");
        // The answers the bus carried, by message id: one may arrive before the app_ask or app_messages
        // response that still holds its question as pending.
        this.answers = new Map();

        onWillStart(() => this.load(this.props.conversationId));
        onWillUpdateProps((nextProps) => {
            if (nextProps.conversationId !== this.props.conversationId) {
                return this.load(nextProps.conversationId);
            }
        });
        useBus(this.env.services.bus_service, "notification", ({ detail: notifications }) => {
            for (const { type, payload } of notifications) {
                if (type === APP_ANSWER_NOTIFICATION) {
                    this.answers.set(payload.message.id, payload.message);
                    if (payload.conversation_id === this.props.conversationId) {
                        this.showMessage(payload.message);
                    }
                }
            }
        });
        useEffect(
            () => {
                if (this.messagesRef.el) {
                    this.messagesRef.el.scrollTop = this.messagesRef.el.scrollHeight;
                }
            },
            () => [this.state.messages.length, this.isWaiting]
        );
        useEffect(
            () => {
                // On a phone a focused composer opens the keyboard over the answers.
                if (!this.env.isSmall) {
                    this.composerRef.el?.focus();
                }
            },
            () => [this.props.conversationId]
        );
    }

    async load(conversationId) {
        const messages = conversationId
            ? await this.orm.call("ai.conversation", "app_messages", [[conversationId]])
            : [];
        this.state.messages = messages.map((message) => this.latest(message));
    }

    /** The message, or the answer the bus already carried for it while it is pending. */
    latest(message) {
        return message.state === "pending" ? this.answers.get(message.id) || message : message;
    }

    /**
     * Insert the message or replace its pending copy, never an answer with a
     * pending copy. An answer comes as the bus carries it, not reloaded: an
     * employee refused when the answer job ran may no longer read the
     * conversation.
     */
    showMessage(message) {
        const latestMessage = this.latest(message);
        const index = this.state.messages.findIndex(({ id }) => id === message.id);
        if (index === -1) {
            this.state.messages.push(latestMessage);
        } else if (latestMessage.state !== "pending") {
            this.state.messages[index] = latestMessage;
        }
    }

    get isWaiting() {
        const lastMessage = this.state.messages[this.state.messages.length - 1];
        return this.state.isSending || lastMessage?.state === "pending";
    }

    get canSend() {
        return Boolean(this.state.draft.trim()) && !this.isWaiting;
    }

    answerHtml(message) {
        return markup(message.answer_html);
    }

    questionLines(message) {
        return message.question.split("\n");
    }

    replyDate(message) {
        return formatDate(deserializeDateTime(message.reply_to.date));
    }

    async send() {
        if (!this.canSend) {
            return;
        }
        this.state.isSending = true;
        try {
            const result = await this.orm.call("ai.conversation", "app_ask", [this.state.draft], {
                conversation_id: this.props.conversationId || false,
                screen: this.props.screen,
                allowed_company_ids: this.user.context.allowed_company_ids,
            });
            this.state.draft = "";
            if (result.conversation.id === this.props.conversationId) {
                this.showMessage(result.message);
            } else {
                this.state.messages = [this.latest(result.message)];
            }
            this.props.onAsked(result.conversation);
        } finally {
            this.state.isSending = false;
        }
    }

    onKeydownComposer(ev) {
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            this.send();
        }
    }

    async rate(message, feedback) {
        const ratedMessage = await this.orm.call(
            "ai.conversation",
            "app_feedback",
            [[this.props.conversationId], message.id, feedback]
        );
        Object.assign(message, ratedMessage);
    }
}
AiAssistantThread.template = "ai_assistant.AiAssistantThread";
AiAssistantThread.props = {
    conversationId: { type: [Number, Boolean] },
    // What the quick panel was opened on; the full page has none.
    screen: { type: Object, optional: true },
    onAsked: Function,
    onOpenReport: Function,
};
