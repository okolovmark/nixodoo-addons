/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

import { Component } from "@odoo/owl";

export class AiAssistantSystray extends Component {
    setup() {
        this.action = useService("action");
        this.panel = useService("ai_assistant_panel");
    }

    /**
     * The action's model and, on a form, the record it shows now: the form
     * keeps its pager position in its local state, not in its props.
     */
    get screen() {
        const controller = this.action.currentController;
        if (controller?.action.type !== "ir.actions.act_window") {
            return {};
        }
        const resId = controller.view?.type === "form" && controller.getLocalState?.()?.resId;
        return { res_model: controller.props.resModel, res_id: resId || false };
    }

    onClick() {
        if (this.panel.state.isOpen) {
            this.panel.close();
        } else {
            this.panel.open(this.screen);
        }
    }
}
AiAssistantSystray.template = "ai_assistant.AiAssistantSystray";

registry.category("systray").add(
    "ai_assistant.AiAssistantSystray",
    { Component: AiAssistantSystray, isDisplayed: () => Boolean(session.can_ask_ai) },
    { sequence: 31 }
);
