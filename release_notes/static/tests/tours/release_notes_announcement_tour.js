import { registry } from "@web/core/registry";
import { stepUtils } from "@web_tour/tour_utils";

registry.category("web_tour.tours").add("release_notes_announcement_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "The main menu icon counts the new notes",
            trigger: ".o_navbar_apps_menu .o_release_notes_counter:contains(2)",
        },
        stepUtils.showAppsMenuItem(),
        {
            content: "The main menu announces both notes and names the newest",
            trigger: ".o_release_notes_announcement:contains(2 new updates):contains(Tour note newest)",
        },
        {
            content: "View opens the newest note",
            trigger: ".o_release_notes_announcement .o_release_notes_view",
            run: "click",
        },
        {
            content: "The pager counts both new notes",
            trigger: ".o_pager_counter:contains(1 / 2)",
        },
        {
            content: "The newest note is open",
            trigger: ".o_form_view .oe_title:contains(Tour note newest)",
        },
        {
            content: "Go to the other new note",
            trigger: ".o_pager_next",
            run: "click",
        },
        {
            content: "The older note is open",
            trigger: ".o_form_view .oe_title:contains(Tour note older)",
        },
        {
            content: "Both are seen: the counter is gone",
            trigger: ".o_navbar_apps_menu:not(:has(.o_release_notes_counter))",
        },
    ],
});
