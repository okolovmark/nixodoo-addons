import {
    Component,
    markup,
    onMounted,
    onPatched,
    onWillDestroy,
    onWillStart,
    onWillUpdateProps,
    proxy,
    signal,
    t,
    usePlugin,
    useProps,
} from "@odoo/owl";
import { BusPlugin } from "@bus/services/bus_plugin";
import { deserializeDateTime, formatDate } from "@web/core/l10n/dates";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";

export const APP_ANSWER_NOTIFICATION = "ai_assistant/app_answer";

/**
 * One AI Assistant conversation: its questions and answers and the composer.
 * Without a conversation it is a new chat, which the first question creates.
 * Answers arrive from a queue job; the bus carries each one once it is written.
 */
export class AiAssistantThread extends Component {
    static template = "ai_assistant.AiAssistantThread";

    props = useProps({
        conversationId: t.or([t.number(), t.literal(false)]),
        // What the quick panel was opened on; the full page has none.
        screen: t.object().optional(),
        onAsked: t.function(),
        onOpenReport: t.function(),
    });
    messagesRef = signal.ref();
    composerRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.ui = useService("ui");
        this.state = proxy({ messages: [], draft: "", isSending: false });
        // The answers the bus carried, by message id: one may arrive before the app_ask or app_messages
        // response that still holds its question as pending.
        this.answers = new Map();
        this.scrolledTo = null;
        this.focusedConversationId = null;

        onWillStart(() => this.load(this.props.conversationId));
        // A parent render that another one overtakes calls this again with the props it had: the
        // conversation already loading is not fetched twice.
        onWillUpdateProps((nextProps) => {
            if (nextProps.conversationId !== this.loading.conversationId) {
                return this.load(nextProps.conversationId);
            }
            return this.loading.promise;
        });
        const unsubscribe = usePlugin(BusPlugin).subscribe(APP_ANSWER_NOTIFICATION, (payload) =>
            this.onAnswer(payload)
        );
        onWillDestroy(unsubscribe);
        onMounted(() => this.afterRender());
        onPatched(() => this.afterRender());
    }

    load(conversationId) {
        const promise = (
            conversationId
                ? this.orm.call("ai.conversation", "app_messages", [[conversationId]])
                : Promise.resolve([])
        ).then((messages) => {
            // Only the latest load shows: an earlier one may answer last.
            if (this.loading.promise === promise) {
                this.state.messages = messages.map((message) => this.latest(message));
            }
        });
        this.loading = { conversationId, promise };
        return promise;
    }

    onAnswer({ conversation_id, message }) {
        this.answers.set(message.id, message);
        if (conversation_id === this.props.conversationId) {
            this.showMessage(message);
        }
    }

    /** Keep the last message in view, and the composer focused on a new conversation. */
    afterRender() {
        const scrollKey = `${this.state.messages.length}/${this.isWaiting}`;
        const messagesEl = this.messagesRef();
        if (messagesEl && scrollKey !== this.scrolledTo) {
            this.scrolledTo = scrollKey;
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        // On a phone a focused composer opens the keyboard over the answers.
        if (this.props.conversationId !== this.focusedConversationId) {
            this.focusedConversationId = this.props.conversationId;
            if (!this.ui.isSmall) {
                this.composerRef()?.focus();
            }
        }
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
        return this.state.isSending || this.state.messages.at(-1)?.state === "pending";
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
                allowed_company_ids: user.context.allowed_company_ids,
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
        const ratedMessage = await this.orm.call("ai.conversation", "app_feedback", [
            [this.props.conversationId],
            message.id,
            feedback,
        ]);
        Object.assign(message, ratedMessage);
    }
}
