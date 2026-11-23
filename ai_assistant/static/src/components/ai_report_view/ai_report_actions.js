/** @odoo-module **/

import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { useService } from "@web/core/utils/hooks";
import { sprintf } from "@web/core/utils/strings";

import { useEnv } from "@odoo/owl";

export const REPORT_XLSX_ACTION = "ai_assistant.ai_report_action_xlsx";

/**
 * What can be done to an AI report from the app, by whoever the server lets:
 * refresh and share are the owner's, delete the owner's and administrators',
 * export everyone's who sees the report.
 */
export function useAiReportActions() {
    const env = useEnv();
    const orm = useService("orm");
    const action = useService("action");
    const dialog = useService("dialog");
    const notification = useService("notification");
    return {
        async refresh(reportId) {
            const report = await orm.call("ai.report", "action_refresh", [[reportId]]);
            notification.add(env._t("The report's data is refreshed."), { type: "success" });
            return report;
        },
        async share(reportId, onClose) {
            const shareAction = await orm.call("ai.report", "action_share", [[reportId]]);
            await action.doAction(shareAction, { onClose });
        },
        exportXlsx(reportId) {
            return action.doAction(REPORT_XLSX_ACTION, {
                additionalContext: { active_model: "ai.report", active_id: reportId, active_ids: [reportId] },
            });
        },
        remove(report, onDeleted) {
            dialog.add(ConfirmationDialog, {
                title: env._t("Delete AI report"),
                body: sprintf(
                    env._t("Delete the report %s? It is deleted for everyone it is shared with too."),
                    report.name
                ),
                confirmLabel: env._t("Delete"),
                confirm: async () => {
                    await orm.unlink("ai.report", [report.id]);
                    onDeleted();
                },
                cancel: () => {},
            });
        },
    };
}
