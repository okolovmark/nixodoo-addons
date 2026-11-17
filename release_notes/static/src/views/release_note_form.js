import { effect, onMounted, onWillDestroy, untrack, usePlugin } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { ReleaseNotesPlugin } from "../announcement/release_notes_plugin";

/** A release note opened in the form, from anywhere, is no longer announced to its reader. */
export class ReleaseNoteFormController extends FormController {
    releaseNotes = usePlugin(ReleaseNotesPlugin);

    setup() {
        super.setup();
        let disposeEffect = () => {};
        onMounted(() => {
            disposeEffect = effect(() => {
                const noteId = this.model.root.resId;
                if (noteId) {
                    untrack(() => this.releaseNotes.markSeen(noteId));
                }
            });
        });
        onWillDestroy(() => disposeEffect());
    }
}

registry.category("views").add("release_note_form", {
    ...formView,
    Controller: ReleaseNoteFormController,
});
