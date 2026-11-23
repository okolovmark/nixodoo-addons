/** @odoo-module */

import tour from "web_tour.tour";

// The tour manager of 16 misses a removed `disabled` attribute, so Send is not
// awaited as a button; Enter sends from the state, whatever the DOM shows.
function pressEnter() {
    this.$anchor[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
}

// The test answers as soon as the question is asked, faster than a step is checked: an observer
// records the pending message instead. On window, so a page reload loses it.
function watchPendingAndPressEnter() {
    window.testAiAssistantSeen = { pending: false };
    new MutationObserver(() => {
        if (document.querySelector(".o_ai_assistant_app .o_ai_assistant_pending")) {
            window.testAiAssistantSeen.pending = true;
        }
    }).observe(document.body, { childList: true, subtree: true });
    pressEnter.call(this);
}

function checkPendingWasReplaced() {
    if (!window.testAiAssistantSeen || !window.testAiAssistantSeen.pending) {
        throw new Error("The answer did not replace a pending question on the same page.");
    }
    if (document.querySelector(".o_ai_assistant_app .o_ai_assistant_pending")) {
        throw new Error("The question still shows as pending.");
    }
}

// Brings each question's answer over the bus, client side, before the app_ask response reaches the
// thread: the order a fast answer job may take. Set before the thread takes the orm service.
function answerEachQuestionBeforeItsResponse(actions) {
    const { orm, bus_service: bus } = odoo.__WOWL_DEBUG__.root.env.services;
    const call = orm.call;
    let answerCount = 0;
    orm.call = async function (model, method, ...args) {
        const result = await call.call(this, model, method, ...args);
        if (method === "app_ask") {
            answerCount += 1;
            const answer_html = `<p>Early answer ${answerCount}.</p>`;
            bus.trigger("notification", [
                {
                    type: "ai_assistant/app_answer",
                    payload: {
                        conversation_id: result.conversation.id,
                        exchange_id: result.message.id,
                        message: { ...result.message, state: "answered", answer_html },
                    },
                },
            ]);
        }
        return result;
    };
    actions.click();
}

function checkNothingPending() {
    if (document.querySelector(".o_ai_assistant_app .o_ai_assistant_pending")) {
        throw new Error("A question still shows as pending.");
    }
}

function askInANewChat(question, openAppsMenu = undefined) {
    return [
        {
            content: "Open the apps menu",
            trigger: ".o_navbar_apps_menu button",
            run: openAppsMenu,
        },
        {
            content: "Open the AI Assistant app",
            trigger: '.o_app[data-menu-xmlid="ai_assistant.ai_assistant_app_menu"]',
        },
        {
            content: "Start a new chat",
            trigger: ".o_ai_assistant_app .o_ai_assistant_new_chat",
        },
        {
            content: "Write the question",
            trigger: ".o_ai_assistant_app .o_ai_assistant_question_input",
            run: `text ${question}`,
        },
        {
            content: "Send it with Enter, watching for its pending message",
            trigger: ".o_ai_assistant_app .o_ai_assistant_question_input",
            run: watchPendingAndPressEnter,
        },
    ];
}

tour.register("ai_assistant_app_tour", { test: true }, [
    ...askInANewChat("How many contacts do we have in Belgium?"),
    {
        content: "The question shows, waiting for its answer",
        trigger:
            '.o_ai_assistant_app .o_ai_assistant_question:contains("How many contacts do we have in Belgium?")',
        extra_trigger: ".o_ai_assistant_app .o_ai_assistant_pending",
        run: () => {},
    },
    {
        content: "The conversation is listed under its first question",
        trigger:
            '.o_ai_assistant_conversation.active:contains("How many contacts do we have in Belgium?")',
        run: () => {},
    },
]);

tour.register("ai_assistant_app_answer_tour", { test: true }, [
    ...askInANewChat("Which country is AI Assistant Tour Acme in?"),
    {
        content: "The answer replaces the pending question, without a reload",
        trigger: '.o_ai_assistant_app .o_ai_assistant_answer_body:contains("Tour Acme is in Belgium.")',
        run: checkPendingWasReplaced,
    },
    {
        content: "The composer takes the next question",
        trigger: ".o_ai_assistant_app .o_ai_assistant_question_input",
        run: "text And its capital?",
    },
    {
        content: "Send it with Enter",
        trigger: ".o_ai_assistant_app .o_ai_assistant_question_input",
        run: pressEnter,
    },
    {
        content: "The next question was sent",
        trigger: '.o_ai_assistant_app .o_ai_assistant_question:contains("And its capital?")',
        run: () => {},
    },
]);

tour.register("ai_assistant_app_early_answer_tour", { test: true }, [
    ...askInANewChat("Which country is AI Assistant Tour Acme in?", answerEachQuestionBeforeItsResponse),
    {
        content: "A new chat keeps the answer that came first, once its conversation has loaded",
        trigger: '.o_ai_assistant_app .o_ai_assistant_answer_body:contains("Early answer 1.")',
        extra_trigger: ".o_ai_assistant_conversation.active",
        run: checkNothingPending,
    },
    {
        content: "Write the next question",
        trigger: ".o_ai_assistant_app .o_ai_assistant_question_input",
        run: "text And its capital?",
    },
    {
        content: "Send it with Enter: the composer waits for nothing",
        trigger: ".o_ai_assistant_app .o_ai_assistant_question_input",
        run: pressEnter,
    },
    {
        content: "The conversation keeps the answer that came first, once",
        trigger: '.o_ai_assistant_app .o_ai_assistant_answer_body:contains("Early answer 2.")',
        extra_trigger: '.o_ai_assistant_app .o_ai_assistant_question:contains("And its capital?")',
        run: () => {
            checkNothingPending();
            if (document.querySelectorAll(".o_ai_assistant_app .o_ai_assistant_question").length !== 2) {
                throw new Error("A question shows twice.");
            }
        },
    },
]);

tour.register("ai_assistant_app_refused_tour", { test: true }, [
    ...askInANewChat("Which country is AI Assistant Tour Acme in?"),
    {
        content: "The refusal shows in place of the answer",
        trigger:
            '.o_ai_assistant_app .o_ai_assistant_answer_body:contains("you no longer have access to the AI Assistant")',
        run: checkPendingWasReplaced,
    },
    {
        // A step's trigger is looked for in an open dialog: an error dialog would hide this button.
        content: "No error dialog: start a new chat",
        trigger: ".o_ai_assistant_app .o_ai_assistant_new_chat",
    },
    {
        content: "The new chat is empty, with no dialog open",
        trigger: ".o_ai_assistant_app .o_ai_assistant_empty",
        run: () => {
            if (document.querySelector(".o_dialog")) {
                throw new Error("A dialog opened after the refusal.");
            }
        },
    },
]);

function checkReplyIsAQuoteAboveItsAnswer() {
    const questions = document.querySelectorAll(".o_ai_assistant_app .o_ai_assistant_question");
    if (questions.length !== 1) {
        throw new Error("The new answer shows its question as a new one.");
    }
    const answerBody = this.$anchor[0]
        .closest(".o_ai_assistant_answer")
        .querySelector(".o_ai_assistant_answer_body");
    if (!answerBody.textContent.includes("The right answer.")) {
        throw new Error("The quote is not above the new answer.");
    }
}

tour.register("ai_assistant_app_reply_tour", { test: true }, [
    {
        content: "The notification's link opens the app, by its name, on the conversation",
        trigger: '.o_ai_assistant_conversation.active:contains("Which country is AI Assistant Tour Acme in?")',
        extra_trigger: '.o_control_panel .breadcrumb-item.active:contains("AI Assistant")',
        run: () => {},
    },
    {
        content: "The new answer quotes the question it answers again, with its date",
        trigger:
            '.o_ai_assistant_app .o_ai_assistant_reply_quote:contains("New answer to your question of"):contains("Which country is AI Assistant Tour Acme in?")',
        run: checkReplyIsAQuoteAboveItsAnswer,
    },
]);

tour.register("ai_assistant_inbox_tour", { test: true }, [
    {
        content: "Open the conversation the new answer was posted on, from the Inbox",
        trigger: '.o_Message:contains("has a new answer to your question") .o_Message_originThreadLink',
    },
    {
        content: "The app opens on the conversation, with the new answer, and not its form",
        trigger: '.o_ai_assistant_conversation.active:contains("Which country is AI Assistant Tour Acme in?")',
        extra_trigger: ".o_ai_assistant_app .o_ai_assistant_reply_quote",
        run: () => {
            if (document.querySelector(".o_form_view, .o_dialog")) {
                throw new Error("The conversation's form or a dialog opened.");
            }
        },
    },
]);

tour.register("ai_assistant_app_gone_tour", { test: true }, [
    {
        content: "A link to a deleted conversation opens the app on its list, a new chat, and no error",
        trigger: ".o_ai_assistant_app .o_ai_assistant_empty",
        extra_trigger: ".o_ai_assistant_app .o_ai_assistant_sidebar",
        run: () => {
            if (document.querySelector(".o_dialog, .o_ai_assistant_conversation.active")) {
                throw new Error("A dialog opened, or a conversation shows as open.");
            }
        },
    },
]);

tour.register("ai_assistant_app_older_tour", { test: true }, [
    {
        content: "A link to a conversation older than the list holds opens it, listed and named",
        trigger: '.o_ai_assistant_app .o_ai_assistant_title:contains("Older question?")',
        extra_trigger: '.o_ai_assistant_conversation.active:contains("Older question?")',
        run: () => {},
    },
    {
        content: "Its questions and answers show",
        trigger: '.o_ai_assistant_app .o_ai_assistant_question:contains("Older question?")',
        run: () => {},
    },
]);

tour.register("ai_assistant_panel_tour", { test: true }, [
    {
        content: "Open the quick panel from the systray",
        trigger: ".o_ai_assistant_systray a",
        extra_trigger: ".o_form_view",
    },
    {
        content: "The panel names the record on screen",
        trigger: '.o_ai_assistant_panel .o_ai_assistant_screen:contains("AI Assistant Tour Partner")',
        run: () => {},
    },
    {
        content: "Write the question",
        trigger: ".o_ai_assistant_panel .o_ai_assistant_question_input",
        run: "text Where is this company?",
    },
    {
        content: "Send it with Enter",
        trigger: ".o_ai_assistant_panel .o_ai_assistant_question_input",
        run: pressEnter,
    },
    {
        content: "The question shows, waiting for its answer",
        trigger: '.o_ai_assistant_panel .o_ai_assistant_question:contains("Where is this company?")',
        extra_trigger: ".o_ai_assistant_panel .o_ai_assistant_pending",
        run: () => {},
    },
]);
