/** @odoo-module **/

import { registry } from "@web/core/registry";

import { reactive } from "@odoo/owl";

/**
 * Whether the AI Assistant's quick panel is open, and on which screen:
 * {res_model, res_id} of the action and record the employee had open.
 */
export const aiAssistantPanelService = {
    start() {
        const state = reactive({ isOpen: false, screen: {} });
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
