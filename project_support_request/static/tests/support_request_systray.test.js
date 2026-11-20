import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { expect, test } from "@odoo/hoot";
import { queryAll, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame, mockDate } from "@odoo/hoot-mock";
import {
    contains,
    mockService,
    mountWithCleanup,
    onRpc,
    patchTranslations,
} from "@web/../tests/web_test_helpers";

import { SupportRequestSystray } from "@project_support_request/support_request_systray/support_request_systray";

defineMailModels();

const CONFIG_URL = "/project_support_request/config";
const CREATE_URL = "/project_support_request/create";
const SYSTRAY_URL = "/project_support_request/systray";
const PRIORITY_LEVELS = { 4: "Highest", 3: "High", 2: "Medium", 1: "Low", 0: "Lowest" };
const CLOSED_LIST_VIEW_ID = 42;

function mockRequests(tickets, priorityLevels = PRIORITY_LEVELS, closedListViewId = CLOSED_LIST_VIEW_ID) {
    onRpc(CONFIG_URL, () => ({
        project_id: 1,
        uid: 7,
        closed_list_view_id: closedListViewId,
        task_types: [],
        priority_levels: priorityLevels,
    }));
    onRpc(SYSTRAY_URL, () => ({
        configured: true,
        bugs_count: tickets.length,
        others_count: 0,
        tickets: tickets.map((ticket, index) => ({
            id: index + 1,
            key: "",
            name: `Request ${index + 1}`,
            stage_name: "Backlog",
            priority_level: "1",
            create_date: "2026-11-19 10:00:00",
            ...ticket,
        })),
        total: tickets.length,
    }));
}

async function openSupportItem() {
    await mountWithCleanup(SupportRequestSystray);
    await animationFrame();
    await contains(".o_support_request_systray").click();
}

async function viewClosedRequests() {
    let openedAction;
    mockService("action", {
        doAction(action) {
            openedAction = action;
            expect.step("doAction");
        },
    });
    await openSupportItem();
    await contains(".o_support_request_menu button[name=view_closed]").click();
    await expect.waitForSteps(["doAction"]);
    return openedAction;
}

function priorityDots() {
    return queryAll(".o_support_request_ticket [data-icon=circle]").map((dot) => ({
        colour: [...dot.classList].filter((name) => /^(text|opacity)-/.test(name)).join(" "),
        title: dot.title,
    }));
}

test("the priority dot grows stronger from the Lowest level to the Highest and names its level", async () => {
    mockRequests(["0", "1", "2", "3", "4"].map((level) => ({ priority_level: level })));
    await openSupportItem();

    expect(priorityDots()).toEqual([
        { colour: "text-muted opacity-50", title: "Priority: Lowest" },
        { colour: "text-muted", title: "Priority: Low" },
        { colour: "text-info", title: "Priority: Medium" },
        { colour: "text-warning", title: "Priority: High" },
        { colour: "text-danger", title: "Priority: Highest" },
    ]);
});

test("a request of an unknown priority level is shown as Low", async () => {
    mockRequests([{ priority_level: false }, { priority_level: "9" }]);
    await openSupportItem();

    expect(priorityDots()).toEqual([
        { colour: "text-muted", title: "Priority: Low" },
        { colour: "text-muted", title: "Priority: Low" },
    ]);
});

test("a priority dot is an image named after its level only once the level has a label", async () => {
    mockRequests([{ priority_level: "4" }, { priority_level: "1" }], { 4: "Highest" });
    await openSupportItem();

    expect(
        queryAll(".o_support_request_ticket [data-icon=circle]").map((dot) =>
            ["role", "aria-label", "title"].map((name) => dot.getAttribute(name))
        )
    ).toEqual([
        ["img", "Priority: Highest", "Priority: Highest"],
        [null, null, null],
    ]);
});

test("the age of a request counts from its creation date in UTC, whatever the time zone", async () => {
    mockDate("2026-11-19 12:00:00", +5);
    mockRequests([{ create_date: "2026-11-19 11:30:00" }, { create_date: "2026-11-19 08:30:00" }]);
    await openSupportItem();

    expect(queryAllTexts(".o_support_request_ticket small")).toEqual([
        "Backlog · 30m ago",
        "Backlog · 3h ago",
    ]);
});

test("a request created ahead of the browser's clock is 0 minutes old", async () => {
    mockDate("2026-11-19 12:00:00");
    mockRequests([{ create_date: "2026-11-19 12:05:00" }]);
    await openSupportItem();

    expect(queryAllTexts(".o_support_request_ticket small")).toEqual(["Backlog · 0m ago"]);
});

test("View Closed lists the user's closed requests in the list view the config names", async () => {
    mockRequests([]);
    const openedAction = await viewClosedRequests();

    expect(openedAction.views).toEqual([
        [CLOSED_LIST_VIEW_ID, "list"],
        [false, "form"],
    ]);
    expect(openedAction.domain).toEqual([
        ["project_id", "=", 1],
        ["stage_id.fold", "=", true],
        ["uat_user_ids", "in", [7]],
    ]);
});

test("View Closed opens the default task list where the config names no list view", async () => {
    mockRequests([], PRIORITY_LEVELS, false);
    const openedAction = await viewClosedRequests();

    expect(openedAction.views).toEqual([
        [false, "list"],
        [false, "form"],
    ]);
});

test("its notifications and the closed requests action read in the user's language", async () => {
    patchTranslations({
        project_support_request: {
            "Closed Support Requests": "Demandes d'assistance closes",
            "Please enter a title for the support request.": "Saisissez un titre.",
            "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo on the AI-driven project that develops it, or restores the archived one that carries it.":
                "Aucun projet actif ne développe cet Odoo.",
            "Error opening closed support requests": "Erreur à l'ouverture des demandes closes",
            "Error creating the support request": "Erreur à la création de la demande",
            "Error loading support requests": "Erreur au chargement des demandes",
        },
    });
    let supportProjectId = 1;
    let failingUrl = null;
    const mockRoute = (url, answer) =>
        onRpc(url, () => {
            if (url === failingUrl) {
                throw new Error("The server is down.");
            }
            return answer();
        });
    mockRoute(CONFIG_URL, () => ({ project_id: supportProjectId, uid: 7, task_types: [], priority_levels: {} }));
    mockRoute(CREATE_URL, () => ({ error: "Not expected" }));
    mockRoute(SYSTRAY_URL, () => ({ configured: true, bugs_count: 0, others_count: 0, tickets: [], total: 0 }));
    mockService("notification", { add: (message) => expect.step(String(message)) });
    mockService("action", { doAction: (action) => expect.step(String(action.name)) });
    const systray = await mountWithCleanup(SupportRequestSystray);

    await systray.onViewClosed();
    await systray.onSubmitCreate();
    supportProjectId = false;
    await systray.onViewClosed();
    failingUrl = CONFIG_URL;
    await systray.onViewClosed();
    failingUrl = CREATE_URL;
    systray.state.createName = "A request";
    await systray.onSubmitCreate();
    failingUrl = SYSTRAY_URL;
    await systray.onShowMore();

    expect.verifySteps([
        "Demandes d'assistance closes",
        "Saisissez un titre.",
        "Aucun projet actif ne développe cet Odoo.",
        "Erreur à l'ouverture des demandes closes",
        "Erreur à la création de la demande",
        "Erreur au chargement des demandes",
    ]);
});

test("the age of a request and the title of its dot read in the user's language", async () => {
    patchTranslations({
        project_support_request: {
            "%sm ago": "il y a %s min",
            "%sh ago": "il y a %s h",
            "%sd ago": "il y a %s j",
            "Priority: %s": "Priorité : %s",
        },
    });
    mockDate("2026-11-19 12:00:00");
    mockRequests(
        ["2026-11-19 11:30:00", "2026-11-19 08:30:00", "2026-11-16 12:00:00"].map((createDate) => ({
            create_date: createDate,
            priority_level: "4",
        })),
        { 4: "Très haute" }
    );
    await openSupportItem();

    expect(queryAllTexts(".o_support_request_ticket small")).toEqual([
        "Backlog · il y a 30 min",
        "Backlog · il y a 3 h",
        "Backlog · il y a 3 j",
    ]);
    expect(queryAll(".o_support_request_ticket [data-icon=circle]").map((dot) => dot.title)).toEqual([
        "Priorité : Très haute",
        "Priorité : Très haute",
        "Priorité : Très haute",
    ]);
});
