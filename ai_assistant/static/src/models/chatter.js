/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";
import { attr } from "@mail/model/model_field";
import { session } from "@web/session";

import { AskAiDialog } from "@ai_assistant/components/ask_ai_dialog/ask_ai_dialog";

registerPatch({
    name: "Chatter",
    fields: {
        /**
         * Ask AI is for the AI Assistant's group, on a saved record, never on the assistants' own records.
         */
        hasAskAi: attr({
            compute() {
                return Boolean(
                    session.can_ask_ai &&
                        this.threadId &&
                        !this.isTemporary &&
                        !(this.threadModel || "").startsWith("ai.")
                );
            },
        }),
    },
    recordMethods: {
        onClickAskAi() {
            this.env.services.dialog.add(AskAiDialog, {
                resModel: this.threadModel,
                resId: this.threadId,
                onAsked: () => {
                    if (this.exists() && this.thread) {
                        this.thread.cache.loadNewMessages();
                    }
                },
            });
        },
    },
});
