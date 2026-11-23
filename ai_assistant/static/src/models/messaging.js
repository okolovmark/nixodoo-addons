/** @odoo-module **/

import { registerPatch } from "@mail/model/model_core";

// Patches mail's own openDocument: loaded first.
import "@mail/models/messaging";

registerPatch({
    name: "Messaging",
    recordMethods: {
        /**
         * The Inbox opens an employee's own AI Assistant conversation in the
         * app: its form is the administrators' review screen.
         *
         * @override
         */
        async openDocument({ id, model }) {
            // Taken before the await: any other patched method of Messaging resets _super.
            const openForm = this._super;
            if (model === "ai.conversation") {
                const action = await this.env.services.orm.call("ai.conversation", "app_open_action", [[id]]);
                if (!this.exists()) {
                    return;
                }
                if (action) {
                    await this.env.services.action.doAction(action);
                    if (this.messaging.device.isSmall) {
                        this.messaging.chatWindowManager.closeAll();
                        this.messaging.messagingMenu.close();
                    }
                    return;
                }
            }
            return openForm.call(this, ...arguments);
        },
    },
});
