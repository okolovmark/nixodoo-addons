import { Thread } from "@mail/core/common/thread_model";
import { patch } from "@web/core/utils/patch";

patch(Thread.prototype, {
    /**
     * A notification opens an employee's own AI Assistant conversation in the
     * app: its form is the administrators' review screen.
     *
     * @override
     */
    open(options) {
        if (this.model !== "ai.conversation") {
            return super.open(...arguments);
        }
        return this.openAiAssistantApp(options);
    },

    async openAiAssistantApp(options) {
        const { action: actionService, orm } = this.store.env.services;
        const action = await orm.call("ai.conversation", "app_open_action", [[this.id]]);
        if (action) {
            return actionService.doAction(action, { newWindow: options?.newWindow });
        }
        return super.open(options);
    },
});
