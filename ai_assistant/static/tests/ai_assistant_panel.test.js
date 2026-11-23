import { defineMailModels, openFormView, start, startServer } from "@mail/../tests/mail_test_helpers";
import { describe, expect, test } from "@odoo/hoot";
import { press, queryOne, waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { contains, defineActions, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { session } from "@web/session";

import { APP_ACTION } from "@ai_assistant/components/ai_assistant_panel/ai_assistant_panel";

defineMailModels();
defineActions([
    { id: 990, xml_id: APP_ACTION, name: "AI Assistant", tag: "ai_assistant.app", type: "ir.actions.client" },
]);

const PANEL = ".o_ai_assistant_panel";

function makeMessage(values = {}) {
    return {
        id: 50,
        question: "Where is this company?",
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
    return { id: 5, name: "Where is this company?", date_last: "2026-11-20 10:00:00", ...values };
}

/** The server of the panel, and of the app it opens: `conversation` is the one the panel continues. */
function servePanel({ conversation = false, messages = [makeMessage()] } = {}) {
    onRpc("ai.conversation", "app_panel", ({ kwargs }) => {
        expect.step(["app_panel", kwargs.screen]);
        return { conversation, screen_name: "Contact: Panel Partner" };
    });
    onRpc("ai.conversation", "app_messages", ({ args }) => {
        expect.step(["app_messages", args[0][0]]);
        return messages;
    });
    onRpc("ai.conversation", "app_conversations", ({ kwargs }) => {
        expect.step(["app_conversations", kwargs.conversation_id]);
        return [makeConversation(), makeConversation({ id: 7 })];
    });
    onRpc("ai.report", "app_reports", () => [
        {
            id: 3,
            name: "Partners by country",
            owner_name: "Mitchell Admin",
            is_owner: true,
            data_date: "2026-11-20 10:00:00",
            is_stale: false,
            shared_count: 0,
            can_refresh: true,
            can_share: true,
            can_delete: true,
        },
    ]);
}

async function openPartnerForm() {
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Panel Partner" });
    await start();
    await openFormView("res.partner", partnerId);
    return partnerId;
}

describe("on desktop", () => {
    describe.current.tags("desktop");

    test("the systray offers the AI Assistant to its group only", async () => {
        patchWithCleanup(session, { can_ask_ai: false });
        await start();
        await waitFor(".o_menu_systray");
        expect(".o_ai_assistant_systray").toHaveCount(0);
    });

    test("the panel names the record on screen and carries it to a new chat's question", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel({ messages: [makeMessage({ id: 70, answer_html: "", state: "pending" })] });
        onRpc("ai.conversation", "app_ask", ({ args, kwargs }) => {
            expect.step(["app_ask", args[0], kwargs.conversation_id, kwargs.screen]);
            return {
                conversation: makeConversation({ id: 7 }),
                message: makeMessage({ id: 70, question: args[0], answer_html: "", state: "pending" }),
            };
        });
        const partnerId = await openPartnerForm();
        await contains(".o_ai_assistant_systray").click();
        const screen = { res_model: "res.partner", res_id: partnerId };
        expect.verifySteps([["app_panel", screen]]);
        expect(`${PANEL} .o_ai_assistant_screen`).toHaveText("Contact: Panel Partner");
        expect(`${PANEL} .o_ai_assistant_empty`).toHaveCount(1);
        expect(`${PANEL} .o_ai_assistant_question_input`).toBeFocused();

        await contains(`${PANEL} .o_ai_assistant_question_input`).edit("Where is this company?");
        await contains(`${PANEL} .o_ai_assistant_question_input`).press("Enter");
        await waitFor(`${PANEL} .o_ai_assistant_pending`);
        expect.verifySteps([["app_ask", "Where is this company?", false, screen], ["app_messages", 7]]);
    });

    test("the panel opens on the record the form's pager shows now", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel();
        const pyEnv = await startServer();
        const [firstId, secondId] = pyEnv["res.partner"].create([
            { name: "First Partner" },
            { name: "Second Partner" },
        ]);
        await start();
        await openFormView("res.partner", firstId, { resIds: [firstId, secondId] });
        await contains(".o_pager_next").click();
        await waitFor(".o_last_breadcrumb_item:contains(Second Partner)");
        await contains(".o_ai_assistant_systray").click();
        expect.verifySteps([["app_panel", { res_model: "res.partner", res_id: secondId }]]);
    });

    test("the panel continues the latest conversation of the same screen, and New chat leaves it", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel({ conversation: makeConversation() });
        const partnerId = await openPartnerForm();
        await contains(".o_ai_assistant_systray").click();
        await waitFor(`${PANEL} .o_ai_assistant_answer_body:contains(In Brussels.)`);
        expect.verifySteps([["app_panel", { res_model: "res.partner", res_id: partnerId }], ["app_messages", 5]]);

        await contains(`${PANEL} .o_ai_assistant_panel_new_chat`).click();
        expect(`${PANEL} .o_ai_assistant_empty`).toHaveCount(1);
    });

    test("Escape, the backdrop, the close button and the systray close the panel", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel();
        const partnerId = await openPartnerForm();
        for (const close of [
            () => press("Escape"),
            () => contains(".offcanvas-backdrop").click(),
            () => contains(`${PANEL} .btn-close`).click(),
            () => contains(".o_ai_assistant_systray").click(),
        ]) {
            await contains(".o_ai_assistant_systray").click();
            await waitFor(PANEL);
            expect(".o_ai_assistant_systray").toHaveClass("active");
            await close();
            await animationFrame();
            expect(PANEL).toHaveCount(0);
            expect(".o_ai_assistant_systray").not.toHaveClass("active");
        }
        expect(".o_form_view").toHaveCount(1);
        expect.verifySteps(Array(4).fill(["app_panel", { res_model: "res.partner", res_id: partnerId }]));
    });

    test("the panel hands its conversation to the app, and closes", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel({ conversation: makeConversation() });
        const partnerId = await openPartnerForm();
        await contains(".o_ai_assistant_systray").click();
        await waitFor(`${PANEL} .o_ai_assistant_answer_body`);
        await contains(`${PANEL} .o_ai_assistant_open_app`).click();
        await waitFor(".o_ai_assistant_app .o_ai_assistant_conversation.active");
        expect.verifySteps([
            ["app_panel", { res_model: "res.partner", res_id: partnerId }],
            ["app_messages", 5],
            ["app_conversations", 5],
            ["app_messages", 5],
        ]);
        expect(PANEL).toHaveCount(0);
        expect(".o_ai_assistant_app .o_ai_assistant_title").toHaveText("Where is this company?");
    });

    test("a report card in the panel opens the app on the report, and closes the panel", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel({
            conversation: makeConversation(),
            messages: [makeMessage({ reports: [{ id: 3, name: "Partners by country" }] })],
        });
        onRpc("ai.report", "app_report", ({ args }) => {
            expect.step(["app_report", args[0][0]]);
            return {
                id: 3,
                name: "Partners by country",
                owner_name: "Mitchell Admin",
                is_owner: true,
                data_date: "2026-11-20 10:00:00",
                conclusions_date: "2026-11-20 10:00:00",
                is_stale: false,
                shared_count: 0,
                shared_user_names: [],
                can_refresh: true,
                can_share: true,
                can_delete: true,
                frame_html: "<p>The frame.</p>",
            };
        });
        const partnerId = await openPartnerForm();
        await contains(".o_ai_assistant_systray").click();
        await contains(`${PANEL} .o_ai_assistant_report_card_open`).click();
        await waitFor(".o_ai_assistant_app:has(.o_ai_report_frame) .o_ai_assistant_title:contains(Partners by country)");
        expect(PANEL).toHaveCount(0);
        expect.verifySteps([
            ["app_panel", { res_model: "res.partner", res_id: partnerId }],
            ["app_messages", 5],
            ["app_conversations", false],
            ["app_report", 3],
        ]);
    });
});

describe("on a phone", () => {
    describe.current.tags("mobile");

    test("the panel fits the screen, its composer unfocused and its buttons in reach", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        servePanel();
        const partnerId = await openPartnerForm();
        await contains(".o_ai_assistant_systray").click();
        await waitFor(PANEL);
        expect.verifySteps([["app_panel", { res_model: "res.partner", res_id: partnerId }]]);
        const panel = queryOne(PANEL).getBoundingClientRect();
        expect(panel.left).toBeGreaterThan(-1);
        expect(panel.right).toBeLessThan(window.innerWidth + 1);
        for (const button of [".o_ai_assistant_panel_new_chat", ".o_ai_assistant_open_app", ".btn-close"]) {
            const { right } = queryOne(`${PANEL} ${button}`).getBoundingClientRect();
            expect(right).toBeLessThan(window.innerWidth + 1);
        }
        expect(`${PANEL} .o_ai_assistant_question_input`).toBeVisible();
        expect(`${PANEL} .o_ai_assistant_question_input`).not.toBeFocused();
    });
});
