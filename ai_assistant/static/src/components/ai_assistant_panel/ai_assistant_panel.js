import { Component, onWillStart, proxy, signal, t, useProps } from "@odoo/owl";
import { useHotkey } from "@web/core/hotkeys/hotkey_hook";
import { registry } from "@web/core/registry";
import { useActiveElement } from "@web/core/ui/ui_plugin";
import { useService } from "@web/core/utils/hooks";

import { AiAssistantThread } from "@ai_assistant/components/ai_assistant_thread/ai_assistant_thread";

export const APP_ACTION = "ai_assistant.ai_assistant_app_action";

/**
 * The quick panel: the AI Assistant over the current screen. It continues the
 * employee's latest conversation while that one was last asked from this
 * screen, and starts a new one otherwise.
 */
export class AiAssistantPanel extends Component {
    static template = "ai_assistant.AiAssistantPanel";
    static components = { AiAssistantThread };

    props = useProps({
        screen: t.object(),
        close: t.function(),
    });
    panelRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = proxy({ conversationId: false, screenName: false });
        useActiveElement(this.panelRef);
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
        this.action.doAction(APP_ACTION, { props: { conversationId } });
    }

    openReport(reportId) {
        this.props.close();
        this.action.doAction(APP_ACTION, { props: { reportId } });
    }
}

export class AiAssistantPanelContainer extends Component {
    static template = "ai_assistant.AiAssistantPanelContainer";
    static components = { AiAssistantPanel };

    setup() {
        this.panel = useService("ai_assistant_panel");
    }
}

registry
    .category("main_components")
    .add("ai_assistant.AiAssistantPanelContainer", { Component: AiAssistantPanelContainer });
