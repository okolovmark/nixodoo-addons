/** @odoo-module */

import tour from "web_tour.tour";

tour.register("ai_assistant_ask_ai_tour", { test: true }, [
    {
        content: "Open Ask AI from the chatter",
        trigger: ".o_ChatterTopbar_buttonAskAi",
    },
    {
        content: "Write the question",
        trigger: ".modal-body textarea.o_ask_ai_question",
        run: "text Who is the contact person of this company?",
    },
    {
        content: "Ask",
        trigger: ".modal-footer .o_ask_ai_submit:not([disabled])",
    },
    {
        content: "The question is a note in the chatter",
        trigger: '.o_Message:contains("Who is the contact person of this company?")',
        run: () => {},
    },
]);

tour.register("ai_assistant_ask_ai_hidden_tour", { test: true }, [
    {
        content: "The chatter of the assistants' own record offers no Ask AI",
        trigger: ".o_ChatterTopbar:has(.o_ChatterTopbar_buttonLogNote):not(:has(.o_ChatterTopbar_buttonAskAi))",
        run: () => {},
    },
]);
