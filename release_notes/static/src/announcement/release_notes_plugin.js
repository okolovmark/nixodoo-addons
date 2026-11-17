import { Plugin, signal, usePlugin } from "@odoo/owl";
import { BusPlugin } from "@bus/services/bus_plugin";
import { ORM } from "@web/core/orm_plugin";
import { services } from "@web/core/services";
import { ActionPlugin } from "@web/webclient/actions/action_plugin";
import { useEnv } from "@web/owl2/utils";

const ANNOUNCEMENT_NOTIFICATION = "release_notes/updated";

/**
 * What the main menu announces to the current user: the published release
 * notes they have neither opened nor skipped, newest first. Reloaded when the
 * server says a note was published, unpublished or seen.
 */
export class ReleaseNotesPlugin extends Plugin {
    bus = usePlugin(BusPlugin);
    orm = usePlugin(ORM);
    action = usePlugin(ActionPlugin);
    env = useEnv();

    count = signal(0);
    noteIds = signal([]);
    title = signal("");
    _loaded = null;
    _lastLoad = 0;

    setup() {
        this.bus.subscribe(ANNOUNCEMENT_NOTIFICATION, () => this.load());
        this.bus.start();
        this.load();
    }

    load() {
        const thisLoad = ++this._lastLoad;
        this._loaded = this.orm
            .call("release.note", "get_announcement", [])
            .then((announcement) => {
                if (thisLoad !== this._lastLoad) {
                    // a later load is under way: its answer is the newer one
                    return;
                }
                this.count.set(announcement.count);
                this.noteIds.set(announcement.note_ids);
                this.title.set(announcement.title);
            });
        return this._loaded;
    }

    /**
     * Open the newest announced note in the Release Notes app, as a click on
     * its icon would; the form pager walks through the others.
     */
    async view() {
        const noteIds = [...this.noteIds()];
        if (!noteIds.length) {
            return;
        }
        const menu = this.env.services.menu;
        const app = menu.getAll().find((item) => item.xmlid === "release_notes.release_note_menu_root");
        await this.action.doAction("release_notes.release_note_action", {
            clearBreadcrumbs: true,
            viewType: "form",
            props: { resId: noteIds[0], resIds: noteIds },
            onActionReady: () => app && menu.setCurrentMenu(app),
        });
    }

    /** Stop announcing the notes the panel shows. */
    async skip() {
        const noteIds = [...this.noteIds()];
        if (noteIds.length) {
            await this.orm.call("release.note", "action_mark_seen", [noteIds]);
        }
        await this.load();
    }

    /** A note was opened: it is no longer announced. */
    async markSeen(noteId) {
        await this._loaded;
        if (this.noteIds().includes(noteId)) {
            await this.orm.call("release.note", "action_mark_seen", [[noteId]]);
            await this.load();
        }
    }
}

services.add(ReleaseNotesPlugin);
