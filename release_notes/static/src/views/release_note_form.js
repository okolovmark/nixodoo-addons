/** @odoo-module **/

import {FormController} from "@web/views/form/form_controller";
import {formView} from "@web/views/form/form_view";
import {registry} from "@web/core/registry";
import {useEffect} from "@odoo/owl";
import {useService} from "@web/core/utils/hooks";

/** A release note opened in the form, from anywhere, is no longer announced to its reader. */
export class ReleaseNoteFormController extends FormController {
    setup() {
        super.setup();
        const releaseNotes = useService("release_notes");
        useEffect(
            (noteId) => {
                if (noteId) {
                    releaseNotes.markSeen(noteId);
                }
            },
            () => [this.model.root.resId]
        );
    }
}

registry.category("views").add("release_note_form", {
    ...formView,
    Controller: ReleaseNoteFormController,
});
