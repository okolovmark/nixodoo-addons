import { proxy } from "@odoo/owl";
import { registry } from "@web/core/registry";

/**
 * Whether the AI Assistant's quick panel is open, and on which screen:
 * {res_model, res_id} of the action and record the employee had open.
 */
export const aiAssistantPanelService = {
    start() {
        const state = proxy({ isOpen: false, screen: {} });
        return {
            state,
            open(screen) {
                Object.assign(state, { isOpen: true, screen });
            },
            close() {
                state.isOpen = false;
            },
        };
    },
};

registry.category("services").add("ai_assistant_panel", aiAssistantPanelService);
