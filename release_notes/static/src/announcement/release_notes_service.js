/** @odoo-module **/

import {registry} from "@web/core/registry";
import {reactive} from "@odoo/owl";

const ANNOUNCEMENT_NOTIFICATION = "release_notes/updated";

/**
 * What the main menu announces to the current user: the published release
 * notes they have neither opened nor skipped, newest first. Reloaded when the
 * server says a note was published, unpublished or seen.
 */
export const releaseNotesService = {
    dependencies: ["action", "bus_service", "menu", "orm"],

    start(env, {action, bus_service, menu, orm}) {
        const state = reactive({count: 0, noteIds: [], title: ""});
        let loaded = null;
        let lastLoad = 0;

        function load() {
            const thisLoad = ++lastLoad;
            loaded = orm.call("release.note", "get_announcement", []).then((announcement) => {
                if (thisLoad !== lastLoad) {
                    // a later load is under way: its answer is the newer one
                    return;
                }
                Object.assign(state, {
                    count: announcement.count,
                    noteIds: announcement.note_ids,
                    title: announcement.title,
                });
            });
            return loaded;
        }

        bus_service.addEventListener("notification", ({detail: notifications}) => {
            if (notifications.some((notification) => notification.type === ANNOUNCEMENT_NOTIFICATION)) {
                load();
            }
        });
        load();

        return {
            state,
            load,
            /**
             * Open the newest announced note in the Release Notes app, as a
             * click on its icon would; the form pager walks through the others.
             */
            async view() {
                const noteIds = [...state.noteIds];
                if (!noteIds.length) {
                    return;
                }
                await action.doAction("release_notes.release_note_action", {
                    clearBreadcrumbs: true,
                    viewType: "form",
                    props: {resId: noteIds[0], resIds: noteIds},
                });
                const app = menu.getAll().find((item) => item.xmlid === "release_notes.release_note_menu_root");
                if (app) {
                    menu.setCurrentMenu(app);
                }
            },
            /** Stop announcing the notes the panel shows. */
            async skip() {
                if (state.noteIds.length) {
                    await orm.call("release.note", "action_mark_seen", [[...state.noteIds]]);
                }
                await load();
            },
            /** A note was opened: it is no longer announced. */
            async markSeen(noteId) {
                await loaded;
                if (state.noteIds.includes(noteId)) {
                    await orm.call("release.note", "action_mark_seen", [[noteId]]);
                    await load();
                }
            },
        };
    },
};

registry.category("services").add("release_notes", releaseNotesService);
