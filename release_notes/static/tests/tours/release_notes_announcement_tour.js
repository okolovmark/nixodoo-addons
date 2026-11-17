/** @odoo-module */

import tour from "web_tour.tour";

tour.register(
    "release_notes_announcement_tour",
    {test: true, url: "/web#action=release_notes.release_note_action"},
    [
        {
            content: "Release Notes is open: opening the main menu earlier would race the action",
            trigger: ".o_list_view",
            run() {},
        },
        {
            content: "The main menu icon counts the new notes",
            trigger: ".o_navbar_apps_menu .o_release_notes_counter:contains(2)",
            run() {},
        },
        {
            // not stepUtils.showAppsMenuItem: web_responsive's tests patch it to a click that opens nothing
            content: "Open the main menu",
            trigger: ".o_navbar_apps_menu .dropdown-toggle",
        },
        {
            content: "The main menu announces both notes and names the newest",
            trigger: ".o_release_notes_announcement:contains(2 new updates):contains(Tour note newest)",
            run() {},
        },
        {
            content: "View opens the newest note",
            trigger: ".o_release_notes_announcement .o_release_notes_view",
        },
        {
            content: "The newest note is open, the pager counts both",
            trigger: ".o_form_view .oe_title:contains(Tour note newest)",
            extra_trigger: ".o_pager_counter:contains(1 / 2)",
            run() {},
        },
        {
            content: "Go to the other new note",
            trigger: ".o_pager_next",
        },
        {
            content: "The older note is open; both are seen",
            trigger: ".o_form_view .oe_title:contains(Tour note older)",
            extra_trigger: ".o_navbar_apps_menu:not(:has(.o_release_notes_counter))",
            run() {},
        },
    ]
);
