/** @odoo-module **/

import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { useService } from "@web/core/utils/hooks";

import { useAiReportActions } from "@ai_assistant/components/ai_report_view/ai_report_actions";

import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

/**
 * One AI report: its data date, the actions the viewer may take, and its page
 * in the frame the server builds, sandboxed so that the page reaches neither
 * Odoo nor the network.
 */
export class AiReportView extends Component {
    setup() {
        this.orm = useService("orm");
        this.reportActions = useAiReportActions();
        this.state = useState({ report: null });
        onWillStart(() => this.load(this.props.reportId));
        onWillUpdateProps((nextProps) => {
            if (nextProps.reportId !== this.props.reportId) {
                return this.load(nextProps.reportId);
            }
        });
    }

    async load(reportId) {
        this.state.report = await this.orm.call("ai.report", "app_report", [[reportId]]);
        this.props.onLoaded(this.state.report);
    }

    formatDate(value) {
        return formatDateTime(deserializeDateTime(value));
    }

    async refresh() {
        this.state.report = await this.reportActions.refresh(this.state.report.id);
        this.props.onLoaded(this.state.report);
        this.props.onChanged();
    }

    share() {
        const reportId = this.state.report.id;
        return this.reportActions.share(reportId, async () => {
            await this.load(reportId);
            this.props.onChanged();
        });
    }

    exportXlsx() {
        return this.reportActions.exportXlsx(this.state.report.id);
    }

    remove() {
        this.reportActions.remove(this.state.report, () => this.props.onDeleted());
    }
}
AiReportView.template = "ai_assistant.AiReportView";
AiReportView.props = {
    reportId: Number,
    onLoaded: Function,
    onChanged: Function,
    onDeleted: Function,
};
