/** @odoo-module **/

import { deserializeDateTime } from "@web/core/l10n/dates";
import { _lt, _t } from "@web/core/l10n/translation";
import { sprintf } from "@web/core/utils/strings";

const { DateTime } = luxon;

// By priority_level, "0" Lowest to "4" Highest: the higher the level, the stronger its colour.
// Grey is bg-600: bg-secondary is white in the 16.0 backend.
const PRIORITY_DOTS = {
    0: { className: "bg-600 opacity-50", label: _lt("Lowest") },
    1: { className: "bg-600", label: _lt("Low") },
    2: { className: "bg-info", label: _lt("Medium") },
    3: { className: "bg-warning", label: _lt("High") },
    4: { className: "bg-danger", label: _lt("Highest") },
};

/**
 * The class and the label of a request's priority dot; a level it does not know is Low, the
 * field's default.
 */
export function priorityDot(priorityLevel) {
    const { className, label } = PRIORITY_DOTS[priorityLevel] || PRIORITY_DOTS[1];
    return { className, label: label.toString() };
}

/**
 * How long ago a request was created, from the server's UTC create date as `str()` of a Python
 * datetime: "YYYY-MM-DD HH:MM:SS", with ".ffffff" when it has microseconds. A client clock behind
 * the server's makes it 0m, not a negative age.
 */
export function ticketAge(createDate, now = DateTime.now()) {
    const minutes = Math.max(0, Math.floor(now.diff(deserializeDateTime(createDate), "minutes").minutes));
    if (minutes < 60) {
        return sprintf(_t("%sm ago"), minutes);
    }
    const hours = Math.floor(minutes / 60);
    if (hours < 24) {
        return sprintf(_t("%sh ago"), hours);
    }
    return sprintf(_t("%sd ago"), Math.floor(hours / 24));
}
