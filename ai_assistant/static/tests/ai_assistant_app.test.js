import { defineMailModels, openFormView, start } from "@mail/../tests/mail_test_helpers";
import { describe, expect, test } from "@odoo/hoot";
import { queryAll, queryAllTexts, queryOne, waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { BusPlugin } from "@bus/services/bus_plugin";
import {
    contains,
    getService,
    MockServer,
    onRpc,
    patchWithCleanup,
    serverState,
} from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";

import { AiAssistantApp } from "@ai_assistant/components/ai_assistant_app/ai_assistant_app";
import {
    APP_ANSWER_NOTIFICATION,
    AiAssistantThread,
} from "@ai_assistant/components/ai_assistant_thread/ai_assistant_thread";
import { REPORT_XLSX_ACTION } from "@ai_assistant/components/ai_report_view/ai_report_actions";

defineMailModels();

const CONVERSATIONS = [
    { id: 5, name: "Where is our office?", date_last: "2026-11-20 10:00:00" },
    { id: 6, name: "Chart our partners by country", date_last: "2026-11-19 10:00:00" },
].map((conversation) => ({ ...conversation, res_model: false, res_id: false }));

function makeReport(values = {}) {
    return {
        id: 3,
        name: "Partners by country",
        owner_name: "Mitchell Admin",
        is_owner: true,
        data_date: "2026-11-20 10:00:00",
        is_stale: false,
        shared_count: 1,
        can_refresh: true,
        can_share: true,
        can_delete: true,
        ...values,
    };
}

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

/**
 * The server of the app: `conversations` and `reports` are what the lists read at each call, so a
 * test changes them to change the next answer.
 */
function serveApp({ conversations = CONVERSATIONS, reports = [] } = {}) {
    const server = { conversations: [...conversations], reports: [...reports] };
    onRpc("ai.conversation", "app_conversations", ({ kwargs }) => {
        expect.step(["app_conversations", kwargs.conversation_id]);
        return server.conversations;
    });
    onRpc("ai.report", "app_reports", () => {
        expect.step("app_reports");
        return server.reports;
    });
    onRpc("ai.conversation", "app_messages", ({ args }) => {
        expect.step(["app_messages", args[0][0]]);
        return [makeMessage({ id: args[0][0] * 10, question: `Question of ${args[0][0]}` })];
    });
    onRpc("ai.report", "app_report", ({ args }) => {
        const report = server.reports.find(({ id }) => id === args[0][0]);
        return {
            ...report,
            conclusions_date: "2026-11-19 10:00:00",
            shared_user_names: report.is_owner ? ["Marc Demo"] : [],
            frame_html: "<html><body><p>The frame.</p></body></html>",
        };
    });
    return server;
}

async function openApp(params = {}) {
    await start();
    await getService("action").doAction({ type: "ir.actions.client", tag: "ai_assistant.app", params });
    await waitFor(".o_ai_assistant_app");
}

function sidebarConversations() {
    return queryAllTexts(".o_ai_assistant_conversation_open .text-truncate");
}

function openSidebarConversation(index) {
    return contains(`.o_ai_assistant_conversation:eq(${index}) .o_ai_assistant_conversation_open`).click();
}

function sendAnswer(conversationId, message) {
    MockServer.env["bus.bus"]._sendone(serverState.partnerId, APP_ANSWER_NOTIFICATION, {
        conversation_id: conversationId,
        exchange_id: message.id,
        message,
    });
}

/** Holds back the messages of the conversation until the returned deferred is resolved. */
function holdMessages(conversationId) {
    const heldMessages = Promise.withResolvers();
    onRpc("ai.conversation", "app_messages", async ({ args, parent }) => {
        if (args[0][0] === conversationId) {
            await heldMessages.promise;
        }
        return parent();
    });
    return heldMessages;
}

describe("on desktop", () => {
    describe.current.tags("desktop");

    test("the app is the client action of its tag", () => {
        expect(registry.category("actions").get("ai_assistant.app")).toBe(AiAssistantApp);
    });

    test("opened on a conversation, the app lists them all, shows that one and names itself in the breadcrumb", async () => {
        serveApp();
        await openApp({ conversation_id: 6 });
        expect.verifySteps([["app_conversations", 6], "app_reports", ["app_messages", 6]]);
        expect(sidebarConversations()).toEqual(["Where is our office?", "Chart our partners by country"]);
        expect(".o_ai_assistant_conversation.active").toHaveText(/Chart our partners by country/);
        expect(".o_ai_assistant_title").toHaveText("Chart our partners by country");
        expect(".o_ai_assistant_question").toHaveText("Question of 6");
        expect(".o_last_breadcrumb_item").toHaveText("AI Assistant");
    });

    test("a link to a deleted conversation opens the list and a new chat, with no error", async () => {
        serveApp();
        await openApp({ conversation_id: 99 });
        expect.verifySteps([["app_conversations", 99], "app_reports"]);
        expect(".o_ai_assistant_conversation.active").toHaveCount(0);
        expect(".o_ai_assistant_empty").toHaveCount(1);
        expect(".o_ai_assistant_title").toHaveText("New chat");
    });

    test("the first question of a new chat lists its conversation and opens it", async () => {
        const server = serveApp();
        onRpc("ai.conversation", "app_ask", ({ args, kwargs }) => {
            expect.step(["app_ask", kwargs.conversation_id]);
            server.conversations.unshift({ ...CONVERSATIONS[0], id: 7, name: args[0] });
            return {
                conversation: server.conversations[0],
                message: makeMessage({ id: 70, question: args[0], answer_html: "", state: "pending" }),
            };
        });
        await openApp();
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        await contains(".o_ai_assistant_new_chat").click();
        await contains(".o_ai_assistant_question_input").edit("How many contacts do we have?");
        await contains(".o_ai_assistant_question_input").press("Enter");
        await waitFor(".o_ai_assistant_conversation.active:contains(How many contacts do we have?)");
        expect.verifySteps([["app_ask", false], ["app_conversations", 7], ["app_messages", 7]]);
        expect(".o_ai_assistant_title").toHaveText("How many contacts do we have?");
    });

    test("deleting a conversation asks first, then deletes it and leaves its chat", async () => {
        const server = serveApp();
        onRpc("ai.conversation", "unlink", ({ args }) => {
            expect.step(["unlink", args[0]]);
            server.conversations = server.conversations.filter(({ id }) => !args[0].includes(id));
            return true;
        });
        await openApp({ conversation_id: 5 });
        expect.verifySteps([["app_conversations", 5], "app_reports", ["app_messages", 5]]);
        await contains(".o_ai_assistant_conversation.active .o_ai_assistant_delete").click();
        expect(".modal").toHaveText(/Its questions and answers are deleted for good/);
        expect.verifySteps([]);
        await contains(".modal-footer .btn-primary").click();
        await animationFrame();
        expect.verifySteps([["unlink", [5]], ["app_conversations", false]]);
        expect(sidebarConversations()).toEqual(["Chart our partners by country"]);
        expect(".o_ai_assistant_empty").toHaveCount(1);
    });

    test("a conversation opened while another still loads keeps its messages when the other answers last", async () => {
        serveApp();
        const heldMessages = holdMessages(5);
        await openApp();
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        await openSidebarConversation(0);
        await openSidebarConversation(1);
        await waitFor(".o_ai_assistant_question:contains(Question of 6)");

        heldMessages.resolve();
        await expect.waitForSteps([["app_messages", 6], ["app_messages", 5]]);
        await animationFrame();
        expect(".o_ai_assistant_question").toHaveText("Question of 6");
        expect(".o_ai_assistant_title").toHaveText("Chart our partners by country");
    });

    test("a conversation still loading is fetched once, however often the app renders meanwhile", async () => {
        serveApp();
        const heldMessages = holdMessages(5);
        await openApp();
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        await openSidebarConversation(0);
        sendAnswer(6, makeMessage({ id: 61, answer_html: "<p>In Belgium.</p>" }));
        await expect.waitForSteps([["app_conversations", 5], "app_reports"]);
        await animationFrame();

        heldMessages.resolve();
        await waitFor(".o_ai_assistant_question:contains(Question of 5)");
        expect.verifySteps([["app_messages", 5]]);
    });

    test("an answer over the bus reloads the lists, unless its asker was refused", async () => {
        serveApp();
        await openApp({ conversation_id: 6 });
        expect.verifySteps([["app_conversations", 6], "app_reports", ["app_messages", 6]]);
        sendAnswer(6, makeMessage({ id: 60, answer_html: "<p>You no longer have access.</p>", is_refused: true }));
        await waitFor(".o_ai_assistant_answer_body:contains(You no longer have access.)");
        await animationFrame();
        expect.verifySteps([]);

        sendAnswer(6, makeMessage({ id: 61, answer_html: "<p>In Belgium.</p>" }));
        await waitFor(".o_ai_assistant_answer_body:contains(In Belgium.)");
        await expect.waitForSteps([["app_conversations", 6], "app_reports"]);
    });

    test("after leaving the app, an answer over the bus reaches neither the app nor its conversation", async () => {
        serveApp();
        // A destroyed component's orm call never reaches the server: only these spies show a listener left behind.
        patchWithCleanup(AiAssistantApp.prototype, {
            loadConversations() {
                expect.step("loadConversations");
                return super.loadConversations();
            },
        });
        patchWithCleanup(AiAssistantThread.prototype, {
            onAnswer(payload) {
                expect.step(["onAnswer", payload.conversation_id]);
                return super.onAnswer(payload);
            },
        });
        await openApp({ conversation_id: 6 });
        expect.verifySteps(["loadConversations", ["app_conversations", 6], "app_reports", ["app_messages", 6]]);
        await openFormView("res.partner", serverState.partnerId);
        await waitFor(".o_form_view");
        expect(".o_ai_assistant_app").toHaveCount(0);

        getService(BusPlugin).subscribe(APP_ANSWER_NOTIFICATION, () => expect.step("notified"));
        sendAnswer(6, makeMessage({ id: 61, answer_html: "<p>In Belgium.</p>" }));
        await expect.waitForSteps(["notified"]);
        await animationFrame();
        expect.verifySteps([]);
    });

    test("My reports lists the reports with who shares them", async () => {
        serveApp({
            reports: [makeReport(), makeReport({ id: 4, name: "Sales by month", is_owner: false, shared_count: 0 })],
        });
        await openApp();
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        await contains(".o_ai_assistant_reports").click();
        expect(".o_ai_assistant_reports").toHaveClass("active");
        expect(".o_ai_assistant_title").toHaveText("My reports");
        expect(queryAllTexts(".o_ai_assistant_report_open .fw-bold")).toEqual([
            "Partners by country",
            "Sales by month",
        ]);
        expect(".o_ai_assistant_report:eq(0)").toHaveText(/Shared with 1/);
        expect(".o_ai_assistant_report:eq(1)").toHaveText(/Shared by Mitchell Admin/);

        await contains(".o_ai_assistant_report:eq(1) .o_ai_assistant_report_open").click();
        await waitFor(".o_ai_report_frame");
        expect(".o_ai_assistant_title").toHaveText("Sales by month");
        await contains(".o_ai_assistant_back").click();
        expect(".o_ai_assistant_report").toHaveCount(2);
    });

    test("My reports says how to get one when there is none", async () => {
        serveApp();
        await openApp();
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        await contains(".o_ai_assistant_reports").click();
        expect(".o_ai_assistant_reports_empty").toHaveText(/No AI reports yet/);
    });

    test("a report opens in its sandboxed frame with the owner's actions", async () => {
        serveApp({ reports: [makeReport({ is_stale: true })] });
        await openApp({ report_id: 3 });
        await waitFor(".o_ai_assistant_title:contains(Partners by country)");
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        const frame = queryOne(".o_ai_report_frame");
        expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
        expect(frame.getAttribute("srcdoc")).toInclude("The frame.");
        expect(".o_ai_report_dates").toHaveText(/Shared with Marc Demo/);
        expect(".o_ai_report_stale").toHaveText(/The conclusions were written on the data of/);
        for (const button of ["refresh", "share", "export", "delete"]) {
            expect(`.o_ai_report_${button}`).toHaveCount(1);
        }
    });

    test("a report shared with the employee offers its export alone", async () => {
        serveApp({
            reports: [makeReport({ is_owner: false, can_refresh: false, can_share: false, can_delete: false })],
        });
        await openApp({ report_id: 3 });
        await waitFor(".o_ai_report_frame");
        expect.verifySteps([["app_conversations", false], "app_reports"]);
        expect(".o_ai_report_dates").toHaveText(/Shared by Mitchell Admin/);
        expect(".o_ai_report_export").toHaveCount(1);
        expect(".o_ai_report_refresh, .o_ai_report_share, .o_ai_report_delete").toHaveCount(0);
    });

    test("a report's refresh, share and export call the server, and its deletion asks first", async () => {
        const server = serveApp({ reports: [makeReport()] });
        onRpc("ai.report", "action_refresh", ({ args }) => {
            expect.step(["action_refresh", args[0]]);
            return { ...makeReport({ data_date: "2026-11-21 10:00:00" }), shared_user_names: [], frame_html: "" };
        });
        onRpc("ai.report", "action_share", ({ args }) => {
            expect.step(["action_share", args[0]]);
            return { type: "ir.actions.act_window_close" };
        });
        onRpc("ai.report", "unlink", ({ args }) => {
            expect.step(["unlink", args[0]]);
            server.reports = [];
            return true;
        });
        onRpc("/web/action/load", async (request) => {
            const { params } = await request.json();
            expect.step(["load", params.action_id, params.context.active_model, params.context.active_ids]);
            return { type: "ir.actions.act_window_close" };
        });
        await openApp({ report_id: 3 });
        await waitFor(".o_ai_report_frame");
        expect.verifySteps([["app_conversations", false], "app_reports"]);

        await contains(".o_ai_report_refresh").click();
        await waitFor(".o_notification:contains(The report's data is refreshed.)");
        await contains(".o_ai_report_share").click();
        await contains(".o_ai_report_export").click();
        await expect.waitForSteps([
            ["action_refresh", [3]],
            "app_reports",
            ["action_share", [3]],
            "app_reports",
            ["load", REPORT_XLSX_ACTION, "ai.report", [3]],
        ]);

        await contains(".o_ai_report_delete").click();
        expect(".modal").toHaveText(/Delete the report Partners by country\?/);
        await contains(".modal-footer .btn-primary").click();
        await waitFor(".o_ai_assistant_reports_empty");
        expect.verifySteps([["unlink", [3]], "app_reports"]);
        expect(".o_ai_assistant_title").toHaveText("My reports");
    });
});

describe("on a phone", () => {
    describe.current.tags("mobile");

    function overflowing() {
        const screenRight = window.innerWidth;
        return queryAll(".o_ai_assistant_app *")
            .filter((element) => element.getBoundingClientRect().right > screenRight + 1)
            .map((element) => element.className);
    }

    test("the list and a conversation take the screen in turn, and both fit it", async () => {
        serveApp();
        await openApp();
        expect(".o_ai_assistant_sidebar").toBeVisible();
        expect(".o_ai_assistant_main").not.toBeVisible();
        expect(overflowing()).toEqual([]);

        await contains(".o_ai_assistant_conversation:eq(0) .o_ai_assistant_conversation_open").click();
        await waitFor(".o_ai_assistant_question:contains(Question of 5)");
        expect(".o_ai_assistant_sidebar").not.toBeVisible();
        expect(".o_ai_assistant_question_input").toBeVisible();
        expect(".o_ai_assistant_question_input").not.toBeFocused();
        expect(overflowing()).toEqual([]);

        await contains(".o_ai_assistant_back").click();
        expect(".o_ai_assistant_sidebar").toBeVisible();
        expect(".o_ai_assistant_main").not.toBeVisible();
        expect.verifySteps([["app_conversations", false], "app_reports", ["app_messages", 5]]);
    });
});
