/** @odoo-module **/

import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { registry } from "@web/core/registry";
import { useActiveElement } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";

import { AiAssistantThread } from "@ai_assistant/components/ai_assistant_thread/ai_assistant_thread";

import { Component, onWillStart, useState } from "@odoo/owl";

/**
 * The quick panel: the AI Assistant over the current screen. It continues the
 * employee's latest conversation while that one was last asked from this
 * screen, and starts a new one otherwise.
 */
export class AiAssistantPanel extends Component {
    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ conversationId: false, screenName: false });
        useActiveElement("panel");
        useHotkey("escape", () => this.props.close(), { bypassEditableProtection: true });
        onWillStart(async () => {
            const panel = await this.orm.call("ai.conversation", "app_panel", [], {
                screen: this.props.screen,
            });
            this.state.conversationId = panel.conversation ? panel.conversation.id : false;
            this.state.screenName = panel.screen_name;
        });
    }

    newChat() {
        this.state.conversationId = false;
    }

    onAsked(conversation) {
        this.state.conversationId = conversation.id;
    }

    openApp() {
        const conversationId = this.state.conversationId;
        this.props.close();
        this.action.doAction("ai_assistant.ai_assistant_app_action", {
            props: { conversationId },
        });
    }

    openReport(reportId) {
        this.props.close();
        this.action.doAction("ai_assistant.ai_assistant_app_action", { props: { reportId } });
    }
}
AiAssistantPanel.components = { AiAssistantThread };
AiAssistantPanel.template = "ai_assistant.AiAssistantPanel";
AiAssistantPanel.props = {
    screen: Object,
    close: Function,
};

export class AiAssistantPanelContainer extends Component {
    setup() {
        this.panel = useService("ai_assistant_panel");
        this.state = useState(this.panel.state);
    }
}
AiAssistantPanelContainer.components = { AiAssistantPanel };
AiAssistantPanelContainer.template = "ai_assistant.AiAssistantPanelContainer";

registry
    .category("main_components")
    .add("ai_assistant.AiAssistantPanelContainer", { Component: AiAssistantPanelContainer });
