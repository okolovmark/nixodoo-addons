/** @odoo-module **/

import { Dialog } from "@web/core/dialog/dialog";
import { useAutofocus, useService } from "@web/core/utils/hooks";

import { Component, useState } from "@odoo/owl";

export class AskAiDialog extends Component {
    setup() {
        this.orm = useService("orm");
        this.user = useService("user");
        this.notification = useService("notification");
        this.state = useState({ question: "", isSending: false });
        useAutofocus();
    }

    get title() {
        return this.env._t("Ask AI");
    }

    get canAsk() {
        return Boolean(this.state.question.trim()) && !this.state.isSending;
    }

    async onClickAsk() {
        if (!this.canAsk) {
            return;
        }
        this.state.isSending = true;
        try {
            await this.orm.call(
                "ai.assistant.exchange",
                "ask_ai",
                [this.props.resModel, this.props.resId, this.state.question],
                { allowed_company_ids: this.user.context.allowed_company_ids }
            );
        } finally {
            this.state.isSending = false;
        }
        this.props.onAsked();
        this.notification.add(
            this.env._t("The AI Assistant is answering: the answer will appear in the chatter."),
            { type: "info" }
        );
        this.props.close();
    }
}
AskAiDialog.components = { Dialog };
AskAiDialog.template = "ai_assistant.AskAiDialog";
AskAiDialog.props = {
    resModel: String,
    resId: Number,
    onAsked: Function,
    close: Function,
};
