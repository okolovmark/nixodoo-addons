import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

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

function companySelection() {
    return `${user.activeCompanies.map((company) => company.id)} (cookie ${cookie.get("cids")})`;
}

// Filed in a company the user has not selected, or is not allowed in.
registry.category("web_tour.tours").add("project_support_request_other_company_tour", {
    url: "/odoo",
    steps: () => {
        let startSelection;
        // The form is read-only unless an access gives employees write on tasks, as project_todo's
        // (auto-installed) does: the project shows as a link then, as an input otherwise.
        const projectField =
            ".o_form_view:has(.o_breadcrumb:contains(Request from another company)) .o_field_widget[name=project_id]";
        return [
            {
                content: "Open the Support item",
                trigger: ".o_support_request_systray",
                run(helpers) {
                    startSelection = companySelection();
                    return helpers.click();
                },
            },
            {
                content: "Open the new ticket form",
                trigger: ".o_support_request_menu button[name=new_ticket]",
                run: "click",
            },
            {
                content: "Give the request a title",
                trigger: ".o_support_request_form input[name=name]",
                run: "edit Request from another company",
            },
            {
                content: "Create it",
                trigger: ".o_support_request_form button[name=create]",
                run: "click",
            },
            {
                content: "The new request opens on its task form, its project named",
                trigger: `${projectField}:contains(Project That Develops This Odoo), ${projectField} input:value(Project That Develops This Odoo)`,
            },
            {
                content: "Open the Support item again",
                trigger: ".o_support_request_systray",
                run: "click",
            },
            {
                content: "Open the request from the list",
                trigger: ".o_support_request_ticket:contains(Request from another company)",
                run: "click",
            },
            {
                content: "It opens on its task form too",
                trigger: ".o_form_view .o_breadcrumb .active:contains(Request from another company)",
            },
            {
                content: "Open the Support item for the closed requests",
                trigger: ".o_support_request_systray",
                run: "click",
            },
            {
                content: "View the closed requests",
                trigger: ".o_support_request_menu button[name=view_closed]",
                run: "click",
            },
            {
                content: "The closed request is listed, in a list grouped by nothing",
                trigger:
                    ".o_list_view:not(:has(.o_group_header)) .o_data_row .o_data_cell:contains(Closed request from another company)",
                run: "click",
            },
            {
                content: "It opens with its chatter, its message and the message's attachment",
                trigger:
                    ".o_form_view .o-mail-Message:contains(Screenshot of the error) .o-mail-AttachmentContainer:contains(error_screenshot.txt)",
            },
            {
                content: "The company selection is the one the user started with",
                trigger: ".o_form_view",
                run() {
                    const endSelection = companySelection();
                    if (endSelection !== startSelection) {
                        throw new Error(`The company selection changed from ${startSelection} to ${endSelection}`);
                    }
                },
            },
        ];
    },
});
