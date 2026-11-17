/** @odoo-module **/

import {Component, useState} from "@odoo/owl";
import {DROPDOWN} from "@web/core/dropdown/dropdown";
import {NavBar} from "@web/webclient/navbar/navbar";

/**
 * The announcement, or nothing to announce where the service is not started:
 * the web client's own tests render the navbar with their own list of services.
 */
function useAnnouncement(env) {
    const service = env.services.release_notes;
    return useState(service ? service.state : {count: 0, noteIds: [], title: ""});
}

/** Panel at the top of the main menu: how many notes are new, the newest title, View and Skip. */
export class ReleaseNotesAnnouncement extends Component {
    setup() {
        this.releaseNotes = this.env.services.release_notes;
        this.state = useAnnouncement(this.env);
    }

    onView() {
        // Odoo's apps menu is a dropdown that stays open on a click inside it
        this.env[DROPDOWN]?.closeAllParents();
        this.releaseNotes.view();
    }

    onSkip() {
        this.releaseNotes.skip();
    }
}
ReleaseNotesAnnouncement.template = "release_notes.Announcement";

/** Red counter of announced notes, on the main menu icon. */
export class ReleaseNotesCounter extends Component {
    setup() {
        this.state = useAnnouncement(this.env);
    }
}
ReleaseNotesCounter.template = "release_notes.Counter";

Object.assign(NavBar.components, {ReleaseNotesAnnouncement, ReleaseNotesCounter});
