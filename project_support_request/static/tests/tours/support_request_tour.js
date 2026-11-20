/** @odoo-module **/

import tour from "web_tour.tour";

const REQUEST_TITLE = "Request from the tour";

function openSupportItem() {
    return { content: "Open the Support item", trigger: ".o_odoo_requests_icon" };
}

// The user files and reads requests of the support project in a company they did not select.
tour.register("project_support_request_tour", { test: true, url: "/web" }, [
    openSupportItem(),
    {
        content: "Open the new ticket form",
        trigger: ".o_odoo_requests_dropdown button:contains(New Ticket)",
    },
    {
        content: "Give the request a title",
        trigger: ".o_odoo_requests_dropdown input[placeholder='Ticket title...']",
        run: `text ${REQUEST_TITLE}`,
    },
    {
        content: "Create it",
        trigger: ".o_odoo_requests_dropdown button:contains(Create)",
    },
    {
        content: "The new request opens on its task form",
        trigger: `.o_form_view .breadcrumb-item.active:contains(${REQUEST_TITLE})`,
        run: () => {},
    },
    openSupportItem(),
    {
        content: "Open the closed requests",
        trigger: ".o_odoo_requests_dropdown button:contains(View Closed)",
    },
    {
        content: "The user's closed request is listed",
        trigger: ".o_list_view .o_data_row:contains(Closed request of the filer)",
        run: () => {},
    },
    openSupportItem(),
    {
        content: "Open the new request from the panel",
        trigger: `.o_odoo_requests_ticket_item:contains(${REQUEST_TITLE})`,
    },
    {
        content: "It opens on its task form, and no error showed on the way",
        trigger: `body:not(:has(.modal)) .o_form_view .breadcrumb-item.active:contains(${REQUEST_TITLE})`,
        run: () => {},
    },
]);
