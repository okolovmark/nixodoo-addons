import { Component, onWillDestroy, onWillStart, proxy, t, usePlugin, useProps } from "@odoo/owl";
import { BusPlugin } from "@bus/services/bus_plugin";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

import {
    APP_ANSWER_NOTIFICATION,
    AiAssistantThread,
} from "@ai_assistant/components/ai_assistant_thread/ai_assistant_thread";
import { useAiReportActions } from "@ai_assistant/components/ai_report_view/ai_report_actions";
import { AiReportView } from "@ai_assistant/components/ai_report_view/ai_report_view";

/**
 * The AI Assistant app: the employee's conversations, newest first, a
 * conversation with its composer, and My reports: their AI reports and those
 * shared with them, each opening on its page. On a phone the list and the
 * page take the screen in turn.
 */
export class AiAssistantApp extends Component {
    static template = "ai_assistant.AiAssistantApp";
    static components = { AiAssistantThread, AiReportView, Dropdown, DropdownItem, Layout };
    // Opened by its tag, from a link or a button, the action has no name: the breadcrumb would read none.
    static displayName = _t("AI Assistant");

    props = useProps({
        ...standardActionServiceProps,
        // Given by the quick panel; a link or a server action carries them in the action's params.
        conversationId: t.or([t.number(), t.boolean()]).optional(),
        reportId: t.or([t.number(), t.boolean()]).optional(),
    });

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.reportActions = useAiReportActions();
        const params = this.props.action.params || {};
        const reportId = this.props.reportId || params.report_id || false;
        const conversationId = this.props.conversationId || params.conversation_id || false;
        this.state = proxy({
            conversations: [],
            conversationId,
            reports: [],
            reportId,
            reportName: "",
            page: reportId ? "report" : "chat",
            isListShown: !conversationId && !reportId,
        });
        onWillStart(async () => {
            await Promise.all([this.loadConversations(), this.loadReports()]);
            // The list holds the employee's linked conversation however old: one not in it was deleted
            // since, and its thread would fail to load.
            const conversationIds = this.state.conversations.map(({ id }) => id);
            if (this.state.conversationId && !conversationIds.includes(this.state.conversationId)) {
                Object.assign(this.state, { conversationId: false, isListShown: true });
            }
        });
        const unsubscribe = usePlugin(BusPlugin).subscribe(APP_ANSWER_NOTIFICATION, ({ message }) => {
            // A refused asker may no longer read their conversations and reports: no reload to fail.
            if (!message.is_refused) {
                this.loadConversations();
                this.loadReports();
            }
        });
        onWillDestroy(unsubscribe);
    }

    async loadConversations() {
        this.state.conversations = await this.orm.call("ai.conversation", "app_conversations", [], {
            conversation_id: this.state.conversationId,
        });
    }

    async loadReports() {
        this.state.reports = await this.orm.call("ai.report", "app_reports", []);
    }

    get title() {
        if (this.state.page === "reports") {
            return _t("My reports");
        }
        if (this.state.page === "report") {
            return this.state.reportName;
        }
        const conversation = this.state.conversations.find(({ id }) => id === this.state.conversationId);
        return conversation ? conversation.name : _t("New chat");
    }

    lastActivity(conversation) {
        return conversation.date_last ? deserializeDateTime(conversation.date_last).toRelative() : "";
    }

    formatDate(value) {
        return formatDateTime(deserializeDateTime(value));
    }

    newChat() {
        Object.assign(this.state, { conversationId: false, page: "chat", isListShown: false });
    }

    openConversation(conversationId) {
        Object.assign(this.state, { conversationId, page: "chat", isListShown: false });
    }

    openReports() {
        Object.assign(this.state, { page: "reports", isListShown: false });
    }

    openReport(reportId) {
        const report = this.state.reports.find(({ id }) => id === reportId);
        Object.assign(this.state, {
            reportId,
            reportName: report ? report.name : "",
            page: "report",
            isListShown: false,
        });
    }

    onReportLoaded(report) {
        this.state.reportName = report.name;
    }

    onReportDeleted() {
        this.openReports();
        return this.loadReports();
    }

    async refreshReport(report) {
        await this.reportActions.refresh(report.id);
        await this.loadReports();
    }

    shareReport(report) {
        return this.reportActions.share(report.id, () => this.loadReports());
    }

    deleteReport(report) {
        this.reportActions.remove(report, () => this.onReportDeleted());
    }

    goBack() {
        if (this.state.page === "report") {
            this.openReports();
        } else {
            this.state.isListShown = true;
        }
    }

    onAsked(conversation) {
        this.state.conversationId = conversation.id;
        this.loadConversations();
    }

    deleteConversation(conversation) {
        this.dialog.add(ConfirmationDialog, {
            title: _t("Delete conversation"),
            body: _t("Delete this conversation? Its questions and answers are deleted for good."),
            confirmLabel: _t("Delete"),
            confirm: async () => {
                await this.orm.unlink("ai.conversation", [conversation.id]);
                if (conversation.id === this.state.conversationId) {
                    this.state.conversationId = false;
                }
                await this.loadConversations();
            },
            cancel: () => {},
        });
    }
}

registry.category("actions").add("ai_assistant.app", AiAssistantApp);
