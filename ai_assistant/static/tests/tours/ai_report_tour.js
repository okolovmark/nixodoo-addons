import { registry } from "@web/core/registry";

const tours = registry.category("web_tour.tours");

const openApp = [
    {
        content: "Open the apps menu",
        trigger: ".o_navbar_apps_menu button",
        run: "click",
    },
    {
        content: "Open the AI Assistant app",
        trigger: '.o_app[data-menu-xmlid="ai_assistant.ai_assistant_app_menu"]',
        run: "click",
    },
];

tours.add("ai_assistant_report_owner_tour", {
    steps: () => [
        ...openApp,
        {
            content: "Open the conversation that built the report",
            trigger: '.o_ai_assistant_conversation:contains("Chart our partners by country") .o_ai_assistant_conversation_open',
            run: "click",
        },
        {
            content: "Open the report from its card",
            trigger: '.o_ai_assistant_report_card:contains("Partners by country") .o_ai_assistant_report_card_open',
            run: "click",
        },
        {
            content: "The report shows in its sandboxed frame, with the owner's actions",
            trigger: '.o_ai_assistant_app:has(.o_ai_assistant_title:contains("Partners by country")) .o_ai_report_view:has(.o_ai_report_frame[sandbox="allow-scripts"]):has(.o_ai_report_share):has(.o_ai_report_export):has(.o_ai_report_delete)',
        },
        {
            content: "The conclusions are as fresh as the data",
            trigger: ".o_ai_report_view:not(:has(.o_ai_report_stale))",
        },
        {
            content: "Refresh the figures",
            trigger: ".o_ai_report_refresh",
            run: "click",
        },
        {
            content: "The figures are refreshed",
            trigger: '.o_notification:contains("refreshed")',
        },
        {
            content: "The page says its conclusions were written on older data",
            trigger: '.o_ai_report_stale:contains("The conclusions were written on the data of")',
        },
        {
            content: "Back to My reports",
            trigger: ".o_ai_assistant_back",
            run: "click",
        },
        {
            content: "My reports lists the report, shared with one colleague",
            trigger: '.o_ai_assistant_report:contains("Partners by country"):contains("Shared with 1")',
        },
    ],
});

tours.add("ai_assistant_report_recipient_tour", {
    steps: () => [
        ...openApp,
        {
            content: "Open My reports",
            trigger: ".o_ai_assistant_reports",
            run: "click",
        },
        {
            content: "Open the report the owner shared",
            trigger: '.o_ai_assistant_report:contains("Partners by country"):contains("Shared by Olga Owner") .o_ai_assistant_report_open',
            run: "click",
        },
        {
            content: "The recipient sees the owner's snapshot and may only export it",
            trigger: '.o_ai_assistant_app:has(.o_ai_report_dates:contains("Shared by Olga Owner")) .o_ai_report_view:has(.o_ai_report_frame[sandbox="allow-scripts"]):has(.o_ai_report_export):not(:has(.o_ai_report_refresh)):not(:has(.o_ai_report_share)):not(:has(.o_ai_report_delete))',
        },
    ],
});

tours.add("ai_assistant_report_panel_tour", {
    steps: () => [
        {
            content: "The record's form is open",
            trigger: ".o_form_view",
        },
        {
            content: "Open the quick panel from the systray",
            trigger: ".o_ai_assistant_systray",
            run: "click",
        },
        {
            content: "Open the report from its card in the panel",
            trigger: '.o_ai_assistant_panel .o_ai_assistant_report_card:contains("Partners by country") .o_ai_assistant_report_card_open',
            run: "click",
        },
        {
            content: "The app opens on the report, and the panel is closed",
            trigger: 'body:not(:has(.o_ai_assistant_panel)) .o_ai_assistant_app:has(.o_ai_report_frame[sandbox="allow-scripts"]) .o_ai_assistant_title:contains("Partners by country")',
        },
    ],
});

tours.add("ai_assistant_report_admin_tour", {
    steps: () => [
        {
            content: "Open the report in the AI Assistant app",
            trigger: '.o_form_view button[name="action_open"]',
            run: "click",
        },
        {
            content: "The app opens on the report, which an administrator may delete but not refresh",
            trigger: '.o_ai_assistant_app:has(.o_ai_assistant_title:contains("Partners by country")) .o_ai_report_view:has(.o_ai_report_frame[sandbox="allow-scripts"]):has(.o_ai_report_delete):not(:has(.o_ai_report_refresh))',
        },
    ],
});
