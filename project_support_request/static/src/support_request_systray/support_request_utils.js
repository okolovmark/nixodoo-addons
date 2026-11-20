/** @odoo-module **/

import { deserializeDateTime } from "@web/core/l10n/dates";

const { DateTime } = luxon;

// By priority_level, "0" Lowest to "4" Highest: the higher the level, the stronger its colour.
// Grey is bg-600: bg-secondary is white in the 16.0 backend.
const PRIORITY_DOTS = {
    0: { className: "bg-600 opacity-50", label: "Lowest" },
    1: { className: "bg-600", label: "Low" },
    2: { className: "bg-info", label: "Medium" },
    3: { className: "bg-warning", label: "High" },
    4: { className: "bg-danger", label: "Highest" },
};

/**
 * The class and the label of a request's priority dot; a level it does not know is Low, the
 * field's default.
 */
export function priorityDot(priorityLevel) {
    return PRIORITY_DOTS[priorityLevel] || PRIORITY_DOTS[1];
}

/**
 * How long ago a request was created, from the server's UTC create date ("YYYY-MM-DD HH:MM:SS");
 * a client clock behind the server's makes it 0m, not a negative age.
 */
export function ticketAge(createDate, now = DateTime.now()) {
    if (!createDate) {
        return "";
    }
    const minutes = Math.max(0, Math.floor(now.diff(deserializeDateTime(createDate), "minutes").minutes));
    if (minutes < 60) {
        return `${minutes}m ago`;
    }
    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
        return `${hours}h ago`;
    }
    return `${Math.floor(hours / 24)}d ago`;
}
