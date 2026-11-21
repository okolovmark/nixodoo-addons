import { registry } from "@web/core/registry";

const quickCreate = ".o_kanban_quick_create";

registry.category("web_tour.tours").add("project_ai_driven_sharing_quick_create_server_error", {
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
            run: "edit Reported while the server fails",
        },
        {
            trigger: `${quickCreate} .o_kanban_add`,
            run: "click",
        },
        {
            content: "The failure shows its message, without the technical details",
            trigger: ".modal .o_error_dialog:contains(The estimate check is down):not(:has(details))",
        },
        {
            trigger: ".modal .o-default-button",
            run: "click",
        },
        {
            content: "The quick create keeps what was typed",
            trigger: `body:not(:has(.modal)) ${quickCreate} .o_field_widget[name=name] input:value(Reported while the server fails)`,
        },
        {
            trigger: `${quickCreate} .o_kanban_cancel`,
            run: "click",
        },
        {
            trigger: `.o_kanban_renderer:not(:has(${quickCreate}))`,
        },
    ],
});
