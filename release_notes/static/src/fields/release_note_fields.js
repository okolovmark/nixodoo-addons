import { Component, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

/** The note's key, a link to its ticket for the users the note's ticket_url is set for. */
export class ReleaseNoteKeyField extends CharField {
    static template = "release_notes.KeyField";

    get ticketUrl() {
        return this.props.readonly && this.props.record.data.ticket_url;
    }
}

/** The note's demo video, played in the form. */
export class ReleaseNoteVideoField extends Component {
    static template = "release_notes.VideoField";
    props = useProps({ ...standardFieldProps });

    get video() {
        return this.props.record.data[this.props.name];
    }

    get videoUrl() {
        return `/web/content/${this.video.id}`;
    }
}

registry.category("fields").add("release_note_key", { ...charField, component: ReleaseNoteKeyField });
registry.category("fields").add("release_note_video", {
    component: ReleaseNoteVideoField,
    displayName: _t("Video"),
    supportedTypes: ["many2one"],
});
