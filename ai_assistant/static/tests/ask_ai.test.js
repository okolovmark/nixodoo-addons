import { defineMailModels, openFormView, openListView, start, startServer } from "@mail/../tests/mail_test_helpers";
import { describe, expect, test } from "@odoo/hoot";
import { waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import {
    contains,
    defineModels,
    fields,
    getService,
    MockServer,
    models,
    onRpc,
    patchWithCleanup,
    serverState,
} from "@web/../tests/web_test_helpers";
import { session } from "@web/session";
import { BusPlugin } from "@bus/services/bus_plugin";

import { ANSWER_NOTIFICATION } from "@ai_assistant/components/chatter/chatter_patch";

class AiConversation extends models.Model {
    _name = "ai.conversation";
    _inherit = ["mail.thread"];

    name = fields.Char();

    _views = {
        form: /* xml */ `
            <form>
                <sheet>
                    <field name="name"/>
                </sheet>
                <chatter/>
            </form>`,
    };
}

defineMailModels();
defineModels({ AiConversation });

const QUESTION = "Who is the contact person of this company?";

async function openPartner({ canAskAi = true } = {}) {
    patchWithCleanup(session, { can_ask_ai: canAskAi });
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Ask AI Partner" });
    await start();
    await openFormView("res.partner", partnerId);
    await waitFor(".o-mail-Chatter-topbar .o-mail-Chatter-logNote");
    return { pyEnv, partnerId };
}

function postNote(pyEnv, model, resId, body) {
    return pyEnv["mail.message"].create({
        body,
        model,
        res_id: resId,
        message_type: "comment",
        author_id: serverState.partnerId,
    });
}

describe("on desktop", () => {
    describe.current.tags("desktop");

    test("Ask AI shows next to Log note on a saved record, for the AI Assistant's group", async () => {
        await openPartner();
        expect(".o-mail-Chatter-logNote + .o-mail-Chatter-askAi").toHaveCount(1);
        expect(".o-mail-Chatter-askAi").toHaveText("Ask AI");
    });

    test("no Ask AI outside the AI Assistant's group", async () => {
        await openPartner({ canAskAi: false });
        expect(".o-mail-Chatter-askAi").toHaveCount(0);
    });

    test("no Ask AI on a record not saved yet", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        await start();
        await openFormView("res.partner");
        await waitFor(".o-mail-Chatter-topbar");
        expect(".o-mail-Chatter-askAi").toHaveCount(0);
    });

    test("no Ask AI on the assistants' own records", async () => {
        patchWithCleanup(session, { can_ask_ai: true });
        const pyEnv = await startServer();
        const conversationId = pyEnv["ai.conversation"].create({ name: "Where is our office?" });
        await start();
        await openFormView("ai.conversation", conversationId);
        await waitFor(".o-mail-Chatter-topbar .o-mail-Chatter-logNote");
        expect(".o-mail-Chatter-askAi").toHaveCount(0);
    });

    test("the dialog asks about the record, closes, and the chatter loads the question's note", async () => {
        const { pyEnv, partnerId } = await openPartner();
        onRpc("ai.assistant.exchange", "ask_ai", ({ args, kwargs }) => {
            expect.step([...args, kwargs.allowed_company_ids]);
            postNote(pyEnv, "res.partner", partnerId, `<p>${args[2]}</p>`);
            return true;
        });
        await contains(".o-mail-Chatter-askAi").click();
        expect(".modal .o_ask_ai_question").toBeFocused();
        expect(".modal .o_ask_ai_submit").not.toBeEnabled();
        await contains(".modal .o_ask_ai_question").edit("   ");
        expect(".modal .o_ask_ai_submit").not.toBeEnabled();

        await contains(".modal .o_ask_ai_question").edit(QUESTION);
        await contains(".modal .o_ask_ai_submit").click();
        expect.verifySteps([["res.partner", partnerId, QUESTION, [serverState.companies[0].id]]]);
        await waitFor(`.o-mail-Message:contains(${QUESTION})`);
        expect(".modal").toHaveCount(0);
        expect(".o_notification").toHaveText(/The AI Assistant is answering/);
    });

    test("Cancel closes the dialog and asks nothing", async () => {
        await openPartner();
        onRpc("ai.assistant.exchange", "ask_ai", () => expect.step("ask_ai"));
        await contains(".o-mail-Chatter-askAi").click();
        await contains(".modal .o_ask_ai_question").edit(QUESTION);
        await contains(".modal-footer .btn-secondary").click();
        expect(".modal").toHaveCount(0);
        expect.verifySteps([]);
    });

    test("an answer posted on the record shows in its open chatter, and one on another record fetches nothing", async () => {
        const { pyEnv, partnerId } = await openPartner();
        const otherId = pyEnv["res.partner"].create({ name: "Another partner" });
        patchWithCleanup(getService("mail.store"), {
            fetchStoreData(name, params) {
                if (name === "/mail/thread/messages") {
                    expect.step([params.thread_model, params.thread_id]);
                }
                return super.fetchStoreData(...arguments);
            },
        });
        postNote(pyEnv, "res.partner", partnerId, "<p>The contact is Jane Doe.</p>");
        const send = (model, id) =>
            MockServer.env["bus.bus"]._sendone(serverState.partnerId, ANSWER_NOTIFICATION, { model, id });
        getService(BusPlugin).subscribe(ANSWER_NOTIFICATION, ({ model, id }) => expect.step(`${model},${id}`));
        send("res.partner", otherId);
        send("res.users", partnerId);
        await expect.waitForSteps([`res.partner,${otherId}`, `res.users,${partnerId}`]);
        await animationFrame();
        expect(".o-mail-Message:contains(The contact is Jane Doe.)").toHaveCount(0);
        expect.verifySteps([]);

        send("res.partner", partnerId);
        await waitFor(".o-mail-Message:contains(The contact is Jane Doe.)");
        expect.verifySteps([`res.partner,${partnerId}`, ["res.partner", partnerId]], { ignoreOrder: true });
    });

    test("after leaving the record, an answer posted on it fetches nothing", async () => {
        const { partnerId } = await openPartner();
        patchWithCleanup(getService("mail.store"), {
            fetchStoreData(name, params) {
                if (name === "/mail/thread/messages") {
                    expect.step([params.thread_model, params.thread_id]);
                }
                return super.fetchStoreData(...arguments);
            },
        });
        await openListView("res.partner");
        await waitFor(".o_list_view");
        expect(".o-mail-Chatter").toHaveCount(0);

        getService(BusPlugin).subscribe(ANSWER_NOTIFICATION, ({ model, id }) => expect.step(`${model},${id}`));
        MockServer.env["bus.bus"]._sendone(serverState.partnerId, ANSWER_NOTIFICATION, {
            model: "res.partner",
            id: partnerId,
        });
        await expect.waitForSteps([`res.partner,${partnerId}`]);
        await animationFrame();
        expect.verifySteps([]);
    });
});

describe("on a phone", () => {
    describe.current.tags("mobile");

    test("Ask AI is an icon, so the chatter's topbar keeps its room", async () => {
        await openPartner();
        expect(".o-mail-Chatter-askAi .oi[data-icon=wand_stars]").toBeVisible();
        expect(".o-mail-Chatter-askAi span").not.toBeVisible();
    });
});

describe("a notification of an AI Assistant conversation", () => {
    describe.current.tags("desktop");

    function serveApp() {
        onRpc("ai.conversation", "app_conversations", () => [
            { id: 5, name: "Where is our office?", date_last: "2026-11-20 10:00:00", res_model: false, res_id: false },
        ]);
        onRpc("ai.report", "app_reports", () => []);
        onRpc("ai.conversation", "app_messages", () => [
            {
                id: 50,
                question: "Where is our office?",
                answer_html: "<p>The right answer.</p>",
                state: "answered",
                is_refused: false,
                feedback: false,
                reports: [],
                reply_to: { id: 49, question: "Where is our office?", date: "2026-11-19 10:00:00" },
            },
        ]);
    }

    test("opens the employee's own conversation in the app, not its form", async () => {
        const pyEnv = await startServer();
        const conversationId = pyEnv["ai.conversation"].create({ name: "Where is our office?" });
        serveApp();
        onRpc("ai.conversation", "app_open_action", ({ args }) => {
            expect.step(["app_open_action", args[0]]);
            return {
                type: "ir.actions.client",
                tag: "ai_assistant.app",
                name: "AI Assistant",
                params: { conversation_id: 5 },
            };
        });
        await start();
        const thread = getService("mail.store")["mail.thread"].insert({
            model: "ai.conversation",
            id: conversationId,
        });
        await thread.open({ focus: true, fromMessagingMenu: true });
        await waitFor(".o_ai_assistant_app .o_ai_assistant_reply_quote");
        expect.verifySteps([["app_open_action", [conversationId]]]);
        expect(".o_ai_assistant_conversation.active").toHaveText(/Where is our office\?/);
        expect(".o_form_view").toHaveCount(0);
    });

    test("opens its form for whoever the app does not open it for", async () => {
        const pyEnv = await startServer();
        const conversationId = pyEnv["ai.conversation"].create({ name: "Where is our office?" });
        onRpc("ai.conversation", "app_open_action", () => false);
        await start();
        const thread = getService("mail.store")["mail.thread"].insert({
            model: "ai.conversation",
            id: conversationId,
        });
        await thread.open({ focus: true, fromMessagingMenu: true });
        await waitFor(".o_form_view .o_field_widget[name=name] input");
        await animationFrame();
        expect(".o_field_widget[name=name] input").toHaveValue("Where is our office?");
        expect(".o_ai_assistant_app").toHaveCount(0);
    });

    test("leaves other records' notifications to their form", async () => {
        const pyEnv = await startServer();
        const partnerId = pyEnv["res.partner"].create({ name: "Notified Partner" });
        onRpc("ai.conversation", "app_open_action", () => expect.step("app_open_action"));
        await start();
        const thread = getService("mail.store")["mail.thread"].insert({ model: "res.partner", id: partnerId });
        await thread.open({ focus: true, fromMessagingMenu: true });
        await waitFor(".o_form_view .o_field_widget[name=name] input");
        expect.verifySteps([]);
    });
});
