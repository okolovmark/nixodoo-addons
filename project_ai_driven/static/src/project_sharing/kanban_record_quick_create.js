import { browser } from "@web/core/browser/browser";
import { RPCErrorDialog, WarningDialog } from "@web/core/errors/error_dialogs";
import { fullTraceback } from "@web/core/errors/error_utils";
import { RPCError } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";
import { KanbanQuickCreateController } from "@web/views/kanban/kanban_record_quick_create";

patch(KanbanQuickCreateController.prototype, {
    showFormDialogInError(error) {
        // core's fallback opens the default task form, the internal one, which project sharing cannot load
        if (!(error instanceof RPCError)) {
            return super.showFormDialogInError(error);
        }
        // the message alone for a portal user: core shows a visitor no traceback
        const FallbackDialog = user.isInternalUser ? RPCErrorDialog : WarningDialog;
        // the dialog and props of core's rpcErrorHandler; not rethrown, so the quick create stays open
        const ErrorDialog = registry.category("error_dialogs").get(error.exceptionName, FallbackDialog);
        this.addDialog(ErrorDialog, {
            traceback: fullTraceback(error),
            message: error.message,
            name: error.name,
            exceptionName: error.exceptionName,
            data: error.data,
            subType: error.subType,
            code: error.code,
            type: error.type,
            serverHost: browser.location.host,
            model: error.model,
        });
    },
});
