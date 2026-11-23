import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { onWillDestroy, usePlugin } from "@odoo/owl";
import { BusPlugin } from "@bus/services/bus_plugin";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";

import { AskAiDialog } from "@ai_assistant/components/ask_ai_dialog/ask_ai_dialog";

export const ANSWER_NOTIFICATION = "ai_assistant/answer";

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        // The AI Assistant posted its answer on a record the asker may have open: a record's chatter
        // gets no new messages over the bus otherwise.
        const unsubscribe = usePlugin(BusPlugin).subscribe(ANSWER_NOTIFICATION, ({ model, id }) => {
            const thread = this.thread();
            if (thread?.model === model && thread.id === id) {
                thread.fetchNewMessages();
            }
        });
        onWillDestroy(unsubscribe);
    },

    /** Ask AI is for the AI Assistant's group, on a saved record, never on the assistants' own records. */
    get hasAskAi() {
        const thread = this.thread();
        return Boolean(session.can_ask_ai && thread?.id && !thread.model.startsWith("ai."));
    },

    onClickAskAi() {
        const thread = this.thread();
        this.env.services.dialog.add(AskAiDialog, {
            resModel: thread.model,
            resId: thread.id,
            onAsked: () => thread.fetchNewMessages(),
        });
    },
});
