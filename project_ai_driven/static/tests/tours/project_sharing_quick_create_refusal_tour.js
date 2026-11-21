import { registry } from "@web/core/registry";

const quickCreate = ".o_kanban_quick_create";

registry.category("web_tour.tours").add("project_ai_driven_sharing_quick_create_refusal", {
    steps: () => [
        {
            trigger: ".o_project_sharing .o_kanban_renderer",
        },
        {
            trigger: "button.o-kanban-button-new",
            run: "click",
        },
        {
            trigger: `${quickCreate} .o_field_widget[name=name] input`,
            run: "edit Reported without an estimate",
        },
        {
            trigger: `${quickCreate} .o_field_widget[name=task_type] input`,
            run: "click",
        },
        {
            trigger: ".o_select_menu_item:contains(Bug)",
            run: "click",
        },
        {
            content: "Add with the Estimate left at zero",
            trigger: `${quickCreate} .o_kanban_add`,
            run: "click",
        },
        {
            content: "The server's refusal shows",
            trigger: ".modal .o_error_dialog:contains(Enter an Estimate above zero hours)",
        },
        {
            trigger: ".modal .o-default-button",
            run: "click",
        },
        {
            content: "The quick create keeps what was typed",
            trigger: `body:not(:has(.modal)) ${quickCreate} .o_field_widget[name=name] input:value(Reported without an estimate)`,
        },
        {
            trigger: `${quickCreate} .o_field_widget[name=allocated_hours] input`,
            run: "edit 2:00",
        },
        {
            trigger: `${quickCreate} .o_kanban_add`,
            run: "click",
        },
        {
            trigger: ".o_kanban_record:contains(Reported without an estimate)",
        },
    ],
});
