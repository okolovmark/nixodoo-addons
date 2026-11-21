import { WarningDialog } from "@web/core/errors/error_dialogs";
import { RPCError } from "@web/core/network/rpc";
import { patch } from "@web/core/utils/patch";
import { KanbanQuickCreateController } from "@web/views/kanban/kanban_record_quick_create";

patch(KanbanQuickCreateController.prototype, {
    showFormDialogInError(error) {
        // core's fallback opens the default task form, the internal one, which project sharing cannot load
        if (!(error instanceof RPCError)) {
            return super.showFormDialogInError(error);
        }
        this.addDialog(WarningDialog, {
            exceptionName: error.exceptionName,
            message: error.data.message,
        });
    },
});
