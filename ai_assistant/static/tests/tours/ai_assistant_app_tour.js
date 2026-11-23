import { BusPlugin } from "@bus/services/bus_plugin";
import { registry } from "@web/core/registry";

const tours = registry.category("web_tour.tours");
const APP = ".o_ai_assistant_app";
const PANEL = ".o_ai_assistant_panel";

// The test answers as soon as the question is asked, faster than a step is checked: an observer
// records the pending message instead. On window, so a page reload loses it.
async function watchPendingAndPressEnter(helpers) {
    window.testAiAssistantSeen = { pending: false };
    new MutationObserver(() => {
        if (document.querySelector(`${APP} .o_ai_assistant_pending`)) {
            window.testAiAssistantSeen.pending = true;
        }
    }).observe(document.body, { childList: true, subtree: true });
    this.anchor.focus();
    await helpers.press("Enter");
}

function checkPendingWasReplaced() {
    if (!window.testAiAssistantSeen?.pending) {
        throw new Error("The answer did not replace a pending question on the same page.");
    }
    checkNothingPending();
}

function checkNothingPending() {
    if (document.querySelector(`${APP} .o_ai_assistant_pending`)) {
        throw new Error("A question still shows as pending.");
    }
}

/**
 * Brings each question's answer over the bus, client side, before the app_ask response reaches the
 * thread: the order a fast answer job may take. Set before the thread takes the orm service.
 */
async function answerEachQuestionBeforeItsResponse(helpers) {
    const { root } = odoo.__WOWL_DEBUG__;
    const { orm } = root.env.services;
    const bus = root.__owl__.pluginManager.getPlugin(BusPlugin);
    const call = orm.call;
    let answerCount = 0;
    orm.call = async function (model, method, ...args) {
        const result = await call.call(this, model, method, ...args);
        if (method === "app_ask") {
            answerCount += 1;
            const answer_html = `<p>Early answer ${answerCount}.</p>`;
            // As the bus worker hands a notification to the page.
            bus.handleMessage({
                data: {
                    type: "BUS:NOTIFICATION",
                    data: [
                        {
                            id: answerCount,
                            message: {
                                type: "ai_assistant/app_answer",
                                payload: {
                                    conversation_id: result.conversation.id,
                                    exchange_id: result.message.id,
                                    message: { ...result.message, state: "answered", answer_html },
                                },
                            },
                        },
                    ],
                },
            });
        }
        return result;
    };
    await helpers.click();
}

function askInANewChat(question, openAppsMenu = "click") {
    return [
        {
            content: "Open the apps menu",
            trigger: ".o_navbar_apps_menu button",
            run: openAppsMenu,
        },
        {
            content: "Open the AI Assistant app",
            trigger: '.o_app[data-menu-xmlid="ai_assistant.ai_assistant_app_menu"]',
            run: "click",
        },
        {
            content: "Start a new chat",
            trigger: `${APP} .o_ai_assistant_new_chat`,
            run: "click",
        },
        {
            content: "Write the question",
            trigger: `${APP} .o_ai_assistant_question_input`,
            run: `edit ${question}`,
        },
        {
            content: "Send it with Enter, watching for its pending message",
            trigger: `${APP} .o_ai_assistant_question_input`,
            run: watchPendingAndPressEnter,
        },
    ];
}

tours.add("ai_assistant_app_tour", {
    url: "/odoo",
    steps: () => [
        ...askInANewChat("How many contacts do we have in Belgium?"),
        {
            content: "The question shows, waiting for its answer",
            trigger: `${APP}:has(.o_ai_assistant_pending) .o_ai_assistant_question:contains("How many contacts do we have in Belgium?")`,
        },
        {
            content: "The conversation is listed under its first question",
            trigger: '.o_ai_assistant_conversation.active:contains("How many contacts do we have in Belgium?")',
        },
    ],
});

tours.add("ai_assistant_app_answer_tour", {
    url: "/odoo",
    steps: () => [
        ...askInANewChat("Which country is AI Assistant Tour Acme in?"),
        {
            content: "The answer replaces the pending question, without a reload",
            trigger: `${APP} .o_ai_assistant_answer_body:contains("Tour Acme is in Belgium.")`,
            run: checkPendingWasReplaced,
        },
        {
            content: "The composer takes the next question",
            trigger: `${APP} .o_ai_assistant_question_input`,
            run: "edit And its capital?",
        },
        {
            content: "Send it with Enter",
            trigger: `${APP} .o_ai_assistant_question_input`,
            run: "press Enter",
        },
        {
            content: "The next question was sent",
            trigger: `${APP} .o_ai_assistant_question:contains("And its capital?")`,
        },
    ],
});

tours.add("ai_assistant_app_early_answer_tour", {
    url: "/odoo",
    steps: () => [
        ...askInANewChat("Which country is AI Assistant Tour Acme in?", answerEachQuestionBeforeItsResponse),
        {
            content: "A new chat keeps the answer that came first, once its conversation has loaded",
            trigger: `${APP}:has(.o_ai_assistant_conversation.active) .o_ai_assistant_answer_body:contains("Early answer 1.")`,
            run: checkNothingPending,
        },
        {
            content: "Write the next question",
            trigger: `${APP} .o_ai_assistant_question_input`,
            run: "edit And its capital?",
        },
        {
            content: "Send it with Enter: the composer waits for nothing",
            trigger: `${APP} .o_ai_assistant_question_input`,
            run: "press Enter",
        },
        {
            content: "The conversation keeps the answer that came first, once",
            trigger: `${APP}:has(.o_ai_assistant_question:contains("And its capital?")) .o_ai_assistant_answer_body:contains("Early answer 2.")`,
            run() {
                checkNothingPending();
                if (document.querySelectorAll(`${APP} .o_ai_assistant_question`).length !== 2) {
                    throw new Error("A question shows twice.");
                }
            },
        },
    ],
});

tours.add("ai_assistant_app_refused_tour", {
    url: "/odoo",
    steps: () => [
        ...askInANewChat("Which country is AI Assistant Tour Acme in?"),
        {
            content: "The refusal shows in place of the answer",
            trigger: `${APP} .o_ai_assistant_answer_body:contains("you no longer have access to the AI Assistant")`,
            run: checkPendingWasReplaced,
        },
        {
            // An action on a step is refused under an open dialog: an error dialog would stop this one.
            content: "No error dialog: start a new chat",
            trigger: `${APP} .o_ai_assistant_new_chat`,
            run: "click",
        },
        {
            content: "The new chat is empty, with no dialog open",
            trigger: `${APP} .o_ai_assistant_empty`,
            run() {
                if (document.querySelector(".modal")) {
                    throw new Error("A dialog opened after the refusal.");
                }
            },
        },
    ],
});

function checkReplyIsAQuoteAboveItsAnswer() {
    const questions = document.querySelectorAll(`${APP} .o_ai_assistant_question`);
    if (questions.length !== 1) {
        throw new Error("The new answer shows its question as a new one.");
    }
    const answerBody = this.anchor
        .closest(".o_ai_assistant_answer")
        .querySelector(".o_ai_assistant_answer_body");
    if (!answerBody.textContent.includes("The right answer.")) {
        throw new Error("The quote is not above the new answer.");
    }
}

tours.add("ai_assistant_app_reply_tour", {
    steps: () => [
        {
            content: "The notification's link opens the app, by its name, on the conversation",
            trigger: '.o_control_panel .o_last_breadcrumb_item:contains("AI Assistant")',
        },
        {
            content: "The conversation is open",
            trigger: '.o_ai_assistant_conversation.active:contains("Which country is AI Assistant Tour Acme in?")',
        },
        {
            content: "The new answer quotes the question it answers again, with its date",
            trigger: `${APP} .o_ai_assistant_reply_quote:contains("New answer to your question of"):contains("Which country is AI Assistant Tour Acme in?")`,
            run: checkReplyIsAQuoteAboveItsAnswer,
        },
    ],
});

tours.add("ai_assistant_inbox_tour", {
    url: "/odoo",
    steps: () => [
        {
            content: "Open the messaging menu",
            trigger: ".o_menu_systray i[aria-label='Messages']",
            run: "click",
        },
        {
            content: "Open its notifications",
            trigger: ".o-mail-MessagingMenu-tab[data-id='notification']",
            run: "click",
        },
        {
            content: "Open the conversation the new answer was posted on, from its notification",
            trigger: '.o-mail-NotificationItem:contains("has a new answer to your question")',
            run: "click",
        },
        {
            content: "The app opens on the conversation, with the new answer, and not its form",
            trigger: `${APP}:has(.o_ai_assistant_reply_quote) .o_ai_assistant_conversation.active:contains("Which country is AI Assistant Tour Acme in?")`,
            run() {
                if (document.querySelector(".o_form_view, .modal")) {
                    throw new Error("The conversation's form or a dialog opened.");
                }
            },
        },
    ],
});

tours.add("ai_assistant_app_gone_tour", {
    steps: () => [
        {
            content: "A link to a deleted conversation opens the app on its list, a new chat, and no error",
            trigger: `${APP}:has(.o_ai_assistant_sidebar) .o_ai_assistant_empty`,
            run() {
                if (document.querySelector(".modal, .o_ai_assistant_conversation.active")) {
                    throw new Error("A dialog opened, or a conversation shows as open.");
                }
            },
        },
    ],
});

tours.add("ai_assistant_app_older_tour", {
    steps: () => [
        {
            content: "A link to a conversation older than the list holds opens it, listed and named",
            trigger: `${APP}:has(.o_ai_assistant_conversation.active:contains("Older question?")) .o_ai_assistant_title:contains("Older question?")`,
        },
        {
            content: "Its questions and answers show",
            trigger: `${APP} .o_ai_assistant_question:contains("Older question?")`,
        },
    ],
});

tours.add("ai_assistant_panel_tour", {
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
            content: "The panel names the record on screen",
            trigger: `${PANEL} .o_ai_assistant_screen:contains("AI Assistant Tour Partner")`,
        },
        {
            content: "Write the question",
            trigger: `${PANEL} .o_ai_assistant_question_input`,
            run: "edit Where is this company?",
        },
        {
            content: "Send it with Enter",
            trigger: `${PANEL} .o_ai_assistant_question_input`,
            run: "press Enter",
        },
        {
            content: "The question shows, waiting for its answer",
            trigger: `${PANEL}:has(.o_ai_assistant_pending) .o_ai_assistant_question:contains("Where is this company?")`,
        },
    ],
});

function checkNoSidewaysScroll() {
    if (document.documentElement.scrollWidth > window.innerWidth) {
        throw new Error(
            `The page scrolls sideways: ${document.documentElement.scrollWidth}px wide on a ${window.innerWidth}px screen.`
        );
    }
}

function checkHidden(selector) {
    const element = document.querySelector(selector);
    if (element && element.offsetParent !== null) {
        throw new Error(`${selector} shows where it should not.`);
    }
}

tours.add("ai_assistant_app_phone_tour", {
    steps: () => [
        {
            content: "On a phone the app opens on its list alone",
            trigger: `${APP} .o_ai_assistant_conversation:contains("Phone question?")`,
            run() {
                checkHidden(`${APP} .o_ai_assistant_main`);
                checkNoSidewaysScroll();
            },
        },
        {
            content: "Open the conversation",
            trigger: `${APP} .o_ai_assistant_conversation:contains("Phone question?") .o_ai_assistant_conversation_open`,
            run: "click",
        },
        {
            content: "The conversation takes the screen, with its composer and a way back",
            trigger: `${APP} .o_ai_assistant_main:has(.o_ai_assistant_question_input:visible) .o_ai_assistant_question:contains("Phone question?")`,
            run() {
                checkHidden(`${APP} .o_ai_assistant_sidebar`);
                checkNoSidewaysScroll();
            },
        },
        {
            content: "Go back to the list",
            trigger: `${APP} .o_ai_assistant_back:visible`,
            run: "click",
        },
        {
            content: "The list is back, with My reports",
            trigger: `${APP} .o_ai_assistant_sidebar .o_ai_assistant_reports:visible`,
            run: "click",
        },
        {
            content: "My reports takes the screen",
            trigger: `${APP} .o_ai_assistant_reports_empty:visible`,
            run: checkNoSidewaysScroll,
        },
    ],
});

tours.add("ai_assistant_panel_phone_tour", {
    steps: () => [
        {
            content: "The record's form is open",
            trigger: ".o_form_view",
        },
        {
            content: "Open the quick panel from the systray",
            trigger: ".o_ai_assistant_systray:visible",
            run: "click",
        },
        {
            content: "The panel fits the phone, with its composer and its buttons in reach",
            trigger: `${PANEL}:has(.o_ai_assistant_open_app:visible):has(.btn-close:visible) .o_ai_assistant_question_input:visible`,
            run() {
                const panel = document.querySelector(PANEL).getBoundingClientRect();
                if (panel.left < 0 || panel.right > window.innerWidth) {
                    throw new Error(`The panel spans ${panel.left}px to ${panel.right}px of a ${window.innerWidth}px screen.`);
                }
                checkNoSidewaysScroll();
            },
        },
        {
            content: "Close the panel",
            trigger: `${PANEL} .btn-close`,
            run: "click",
        },
        {
            content: "The panel is closed",
            trigger: `body:not(:has(${PANEL})) .o_form_view`,
        },
    ],
});
