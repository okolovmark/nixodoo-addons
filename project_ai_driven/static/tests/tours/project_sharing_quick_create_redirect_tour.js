import { registry } from "@web/core/registry";

const quickCreate = ".o_kanban_quick_create";

function addAndCloseTheRedirect(content) {
    return [
        {
            content,
            trigger: `body:not(:has(.modal)) ${quickCreate} .o_kanban_add`,
            run: "click",
        },
        {
            content: "The redirect shows its message and its button",
            trigger: ".modal .o_error_dialog:contains(Pick the project in the backend)",
        },
        {
            trigger: ".modal .modal-footer .btn-primary:contains(Open the Projects)",
        },
        {
            trigger: ".modal .modal-footer .btn-secondary",
            run: "click",
        },
        {
            content: "The quick create keeps what was typed",
            trigger: `body:not(:has(.modal)) ${quickCreate} .o_field_widget[name=name] input:value(Reported elsewhere)`,
        },
    ];
}

registry.category("web_tour.tours").add("project_ai_driven_sharing_quick_create_redirect", {
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
            run: "edit Reported elsewhere",
        },
        ...addAndCloseTheRedirect("Add"),
        ...addAndCloseTheRedirect("Add again: the first refusal left the quick create ready"),
        {
            trigger: `${quickCreate} .o_kanban_cancel`,
            run: "click",
        },
        {
            trigger: `.o_kanban_renderer:not(:has(${quickCreate}))`,
        },
    ],
});
