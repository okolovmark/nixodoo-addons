import { Component, t, usePlugin, useProps } from "@odoo/owl";
import { useDropdownCloser } from "@web/core/dropdown/dropdown_hooks";
import { NavBar } from "@web/webclient/navbar/navbar";
import { ReleaseNotesPlugin } from "./release_notes_plugin";

/** Panel at the top of the main menu: how many notes are new, the newest title, View and Skip. */
export class ReleaseNotesAnnouncement extends Component {
    static template = "release_notes.Announcement";
    props = useProps({ onView: t.function().optional() });
    releaseNotes = usePlugin(ReleaseNotesPlugin);
    dropdown = useDropdownCloser();

    onView() {
        // the apps menu stays open on a click inside it: close it before leaving
        this.dropdown.closeAll();
        this.props.onView?.();
        this.releaseNotes.view();
    }

    onSkip() {
        this.releaseNotes.skip();
    }
}

/** Red counter of announced notes, on the main menu icon. */
export class ReleaseNotesCounter extends Component {
    static template = "release_notes.Counter";
    releaseNotes = usePlugin(ReleaseNotesPlugin);
}

Object.assign(NavBar.components, { ReleaseNotesAnnouncement, ReleaseNotesCounter });
