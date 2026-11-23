import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("ai_assistant_ask_ai_tour", {
    steps: () => [
        {
            content: "Open Ask AI from the chatter",
            trigger: ".o-mail-Chatter-askAi",
            run: "click",
        },
        {
            content: "Write the question",
            trigger: ".modal-body textarea.o_ask_ai_question",
            run: "edit Who is the contact person of this company?",
        },
        {
            content: "Ask",
            trigger: ".modal-footer .o_ask_ai_submit:enabled",
            run: "click",
        },
        {
            content: "The question is a note in the chatter, and the dialog is closed",
            trigger: 'body:not(:has(.modal)) .o-mail-Message:contains("Who is the contact person of this company?")',
        },
    ],
});

registry.category("web_tour.tours").add("ai_assistant_ask_ai_hidden_tour", {
    steps: () => [
        {
            content: "The chatter of the assistants' own record offers no Ask AI",
            trigger: ".o-mail-Chatter-topbar:has(.o-mail-Chatter-logNote):not(:has(.o-mail-Chatter-askAi))",
        },
    ],
});
