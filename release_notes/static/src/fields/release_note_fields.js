/** @odoo-module **/

import {CharField} from "@web/views/fields/char/char_field";
import {Component} from "@odoo/owl";
import {_lt} from "@web/core/l10n/translation";
import {registry} from "@web/core/registry";
import {standardFieldProps} from "@web/views/fields/standard_field_props";

/** The note's key, a link to its ticket for the users the note's ticket_url is set for. */
export class ReleaseNoteKeyField extends CharField {
    get ticketUrl() {
        return this.props.readonly && this.props.record.data.ticket_url;
    }
}
ReleaseNoteKeyField.template = "release_notes.KeyField";

/** The note's demo video, played in the form. */
export class ReleaseNoteVideoField extends Component {
    get videoUrl() {
        return `/web/content/${this.props.value[0]}`;
    }
}
ReleaseNoteVideoField.template = "release_notes.VideoField";
ReleaseNoteVideoField.props = {...standardFieldProps};
ReleaseNoteVideoField.displayName = _lt("Video");
ReleaseNoteVideoField.supportedTypes = ["many2one"];

registry.category("fields").add("release_note_key", ReleaseNoteKeyField);
registry.category("fields").add("release_note_video", ReleaseNoteVideoField);
