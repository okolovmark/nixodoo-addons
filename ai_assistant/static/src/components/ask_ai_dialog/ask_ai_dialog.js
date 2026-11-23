import { Component, proxy, signal, t, useProps } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useAutofocus, useService } from "@web/core/utils/hooks";

export class AskAiDialog extends Component {
    static template = "ai_assistant.AskAiDialog";
    static components = { Dialog };

    props = useProps({
        resModel: t.string(),
        resId: t.number(),
        onAsked: t.function(),
        close: t.function(),
    });
    questionRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = proxy({ question: "", isSending: false });
        useAutofocus({ ref: this.questionRef });
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
                { allowed_company_ids: user.context.allowed_company_ids }
            );
        } finally {
            this.state.isSending = false;
        }
        this.props.onAsked();
        this.notification.add(_t("The AI Assistant is answering: the answer will appear in the chatter."), {
            type: "info",
        });
        this.props.close();
    }
}
