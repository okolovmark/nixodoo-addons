/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";

registerPatch({
    name: "MessagingNotificationHandler",
    recordMethods: {
        /**
         * The AI Assistant posted its answer on a record the asker may have
         * open: a record's chatter gets no new messages over the bus otherwise.
         *
         * @override
         */
        async _handleNotification(message) {
            if (message.type === "ai_assistant/answer") {
                const thread = this.messaging.models["Thread"].findFromIdentifyingData({
                    id: message.payload.id,
                    model: message.payload.model,
                });
                if (thread) {
                    thread.cache.loadNewMessages();
                }
            }
            return this._super(message);
        },
    },
});
