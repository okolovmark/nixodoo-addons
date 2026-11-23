import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { describe, expect, test } from "@odoo/hoot";
import { queryAllTexts, waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { BusPlugin } from "@bus/services/bus_plugin";
import {
    contains,
    getService,
    MockServer,
    mountWithCleanup,
    onRpc,
    serverState,
} from "@web/../tests/web_test_helpers";

import {
    APP_ANSWER_NOTIFICATION,
    AiAssistantThread,
} from "@ai_assistant/components/ai_assistant_thread/ai_assistant_thread";

defineMailModels();

const COMPOSER = ".o_ai_assistant_question_input";

function makeMessage(values = {}) {
    return {
        id: 1,
        question: "Where is our office?",
        answer_html: "<p>In Brussels.</p>",
        state: "answered",
        is_refused: false,
        feedback: false,
        reports: [],
        reply_to: false,
        ...values,
    };
}

function makeConversation(values = {}) {
    return {
        id: 5,
        name: "Where is our office?",
        date_last: "2026-11-20 10:00:00",
        res_model: false,
        res_id: false,
        ...values,
    };
}

function serveMessages(messages, conversationId = 5) {
    onRpc("ai.conversation", "app_messages", ({ args }) => {
        expect(args).toEqual([[conversationId]]);
        return messages;
    });
}

function sendAnswer(conversationId, message) {
    MockServer.env["bus.bus"]._sendone(serverState.partnerId, APP_ANSWER_NOTIFICATION, {
        conversation_id: conversationId,
        exchange_id: message.id,
        message,
    });
}

function mountThread(props = {}) {
    return mountWithCleanup(AiAssistantThread, {
        props: { conversationId: 5, onAsked: () => {}, onOpenReport: () => {}, ...props },
    });
}

describe("on desktop", () => {
    describe.current.tags("desktop");

    test("a conversation shows its questions, their lines and their answers, oldest first", async () => {
        serveMessages([
            makeMessage(),
            makeMessage({ id: 2, question: "And the warehouse?\nIn which city?", answer_html: "<p>In Ghent.</p>" }),
        ]);
        await mountThread();
        expect(".o_ai_assistant_question").toHaveCount(2);
        expect(".o_ai_assistant_question:first").toHaveText("Where is our office?");
        expect(".o_ai_assistant_question:last").toHaveText(/And the warehouse\?\s+In which city\?/);
        expect(".o_ai_assistant_question:last br").toHaveCount(1);
        expect(queryAllTexts(".o_ai_assistant_answer_body")).toEqual(["In Brussels.", "In Ghent."]);
        expect(".o_ai_assistant_pending").toHaveCount(0);
    });

    test("a new chat offers an empty page and the composer, focused", async () => {
        await mountThread({ conversationId: false });
        expect(".o_ai_assistant_empty").toHaveCount(1);
        expect(COMPOSER).toBeFocused();
        expect(".o_ai_assistant_send").not.toBeEnabled();
    });

    test("Enter sends the question with its conversation and screen, Shift+Enter starts a new line", async () => {
        serveMessages([makeMessage()]);
        onRpc("ai.conversation", "app_ask", ({ args, kwargs }) => {
            expect.step({ args, conversation_id: kwargs.conversation_id, screen: kwargs.screen });
            return {
                conversation: makeConversation(),
                message: makeMessage({ id: 2, question: args[0], answer_html: "", state: "pending" }),
            };
        });
        await mountThread({ screen: { res_model: "res.partner", res_id: 3 } });
        await contains(COMPOSER).edit("Who is the contact person?");
        await contains(COMPOSER).press(["Shift", "Enter"]);
        await contains(COMPOSER).fill("Since when?");
        expect.verifySteps([]);

        await contains(COMPOSER).press("Enter");
        expect.verifySteps([
            {
                args: ["Who is the contact person?\nSince when?"],
                conversation_id: 5,
                screen: { res_model: "res.partner", res_id: 3 },
            },
        ]);
        await waitFor(".o_ai_assistant_pending");
        expect(".o_ai_assistant_question:last").toHaveText(/Who is the contact person\?\s+Since when\?/);
        expect(COMPOSER).toHaveValue("");
        expect(".o_ai_assistant_send").not.toBeEnabled();
    });

    test("the answer the bus carries replaces its pending question, and the composer takes the next", async () => {
        serveMessages([makeMessage({ answer_html: "", state: "pending" })]);
        await mountThread();
        expect(".o_ai_assistant_pending").toHaveCount(1);

        sendAnswer(5, makeMessage());
        await waitFor(".o_ai_assistant_answer_body:contains(In Brussels.)");
        expect(".o_ai_assistant_pending").toHaveCount(0);
        expect(".o_ai_assistant_question").toHaveCount(1);
        await contains(COMPOSER).edit("And the warehouse?");
        expect(".o_ai_assistant_send").toBeEnabled();
    });

    test("an answer to another conversation stays out of this one", async () => {
        serveMessages([makeMessage({ answer_html: "", state: "pending" })]);
        await mountThread();
        sendAnswer(6, makeMessage({ id: 9, answer_html: "<p>Another conversation's answer.</p>" }));
        sendAnswer(5, makeMessage());
        await waitFor(".o_ai_assistant_answer_body:contains(In Brussels.)");
        expect(".o_ai_assistant_question").toHaveCount(1);
        expect(".o_ai_assistant_answer_body:contains(Another conversation)").toHaveCount(0);
    });

    test("an answer that comes before its question's response is kept, in a new chat too", async () => {
        onRpc("ai.conversation", "app_ask", ({ args }) => {
            // The mock server sends its notifications after the response: the bus worker's hand-over
            // to the page is played here, while the response is still on its way.
            const answer = makeMessage({ id: 2, question: args[0], answer_html: "<p>Early answer.</p>" });
            getService(BusPlugin).handleMessage({
                data: {
                    type: "BUS:NOTIFICATION",
                    data: [
                        {
                            id: 1,
                            message: {
                                type: APP_ANSWER_NOTIFICATION,
                                payload: { conversation_id: 7, exchange_id: 2, message: answer },
                            },
                        },
                    ],
                },
            });
            return {
                conversation: makeConversation({ id: 7, name: args[0] }),
                message: { ...answer, answer_html: "", state: "pending" },
            };
        });
        await mountThread({ conversationId: false, onAsked: (conversation) => expect.step(conversation.id) });
        await contains(COMPOSER).edit("Which country is it in?");
        await contains(COMPOSER).press("Enter");
        expect.verifySteps([7]);
        await animationFrame();
        expect(".o_ai_assistant_answer_body").toHaveText("Early answer.");
        expect(".o_ai_assistant_pending").toHaveCount(0);
    });

    test("a new answer quotes the question it answers again, with its date", async () => {
        serveMessages([
            makeMessage({
                id: 2,
                answer_html: "<p>The right answer.</p>",
                reply_to: { id: 1, question: "Where is our office?", date: "2026-11-20 10:00:00" },
            }),
        ]);
        await mountThread();
        expect(".o_ai_assistant_reply_quote").toHaveCount(1);
        expect(".o_ai_assistant_reply_quote").toHaveText(/New answer to your question of .*Where is our office\?/);
        expect(".o_ai_assistant_question").toHaveCount(0);
    });

    test("rating an answer sends it and shows the choice", async () => {
        serveMessages([makeMessage()]);
        onRpc("ai.conversation", "app_feedback", ({ args }) => {
            expect.step(args);
            return makeMessage({ feedback: args[2] });
        });
        await mountThread();
        expect(".o_ai_assistant_rate_up .oi-filled").toHaveCount(0);
        await contains(".o_ai_assistant_rate_up").click();
        expect.verifySteps([[[5], 1, "up"]]);
        expect(".o_ai_assistant_rate_up .oi-filled").toHaveCount(1);
        expect(".o_ai_assistant_rate_down .oi-filled").toHaveCount(0);
    });

    test("a report card of an answer opens its report", async () => {
        serveMessages([makeMessage({ reports: [{ id: 3, name: "Partners by country" }] })]);
        await mountThread({ onOpenReport: (reportId) => expect.step(reportId) });
        expect(".o_ai_assistant_report_card").toHaveText(/Partners by country/);
        await contains(".o_ai_assistant_report_card_open").click();
        expect.verifySteps([3]);
    });
});

describe("on a phone", () => {
    describe.current.tags("mobile");

    test("a new chat leaves the composer unfocused, so no keyboard covers the page", async () => {
        await mountThread({ conversationId: false });
        expect(COMPOSER).toHaveCount(1);
        expect(COMPOSER).not.toBeFocused();
    });
});
