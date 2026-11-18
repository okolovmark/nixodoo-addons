import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("project_support_request_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "The Support item counts the user's open requests",
            trigger: ".o_support_request_systray .badge:contains(2)",
        },
        {
            content: "Open the Support item",
            trigger: ".o_support_request_systray",
            run: "click",
        },
        {
            content: "The Bugs tab lists the open bug with its key",
            trigger: ".o_support_request_ticket:contains(ZQT-1):contains(Tour open bug)",
        },
        {
            content: "The closed bug is not listed",
            trigger: ".o_support_request_menu:not(:has(.o_support_request_ticket:contains(Tour closed bug)))",
        },
        {
            content: "Switch to Others",
            trigger: ".o_support_request_menu button[name=others]",
            run: "click",
        },
        {
            content: "The Others tab lists the open feature",
            trigger: ".o_support_request_ticket:contains(Tour open feature)",
        },
        {
            content: "Open the new ticket form",
            trigger: ".o_support_request_menu button[name=new_ticket]",
            run: "click",
        },
        {
            content: "Give the request a title",
            trigger: ".o_support_request_form input[name=name]",
            run: "edit Tour new request",
        },
        {
            content: "Pick its type",
            trigger: ".o_support_request_form select[name=task_type]",
            run: "select improvement",
        },
        {
            content: "Create it",
            trigger: ".o_support_request_form button[name=create]",
            run: "click",
        },
        {
            content: "The new request opens on its task form",
            trigger: "body:has(.o_form_view) .o_breadcrumb:contains(Tour new request)",
        },
    ],
});
