import { Component, onMounted, onWillStart, onWillUpdateProps, proxy, t, useProps } from "@odoo/owl";
import { deserializeDateTime, formatDateTime } from "@web/core/l10n/dates";
import { useService } from "@web/core/utils/hooks";

import { useAiReportActions } from "@ai_assistant/components/ai_report_view/ai_report_actions";

/**
 * One AI report: its data date, the actions the viewer may take, and its page
 * in the frame the server builds, sandboxed so that the page reaches neither
 * Odoo nor the network.
 */
export class AiReportView extends Component {
    static template = "ai_assistant.AiReportView";

    props = useProps({
        reportId: t.number(),
        onLoaded: t.function(),
        onChanged: t.function(),
        onDeleted: t.function(),
    });

    setup() {
        this.orm = useService("orm");
        this.reportActions = useAiReportActions();
        this.state = proxy({ report: null });
        onWillStart(() => this.load(this.props.reportId));
        onWillUpdateProps((nextProps) => {
            if (nextProps.reportId !== this.state.report.id) {
                return this.load(nextProps.reportId);
            }
        });
        // Told once mounted: a parent rendering again before then would make this view anew.
        onMounted(() => this.props.onLoaded(this.state.report));
    }

    async load(reportId) {
        this.state.report = await this.orm.call("ai.report", "app_report", [[reportId]]);
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
            this.props.onLoaded(this.state.report);
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
