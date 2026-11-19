import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { expect, test } from "@odoo/hoot";
import { queryAll, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame, mockDate } from "@odoo/hoot-mock";
import { contains, mountWithCleanup, onRpc } from "@web/../tests/web_test_helpers";

import { SupportRequestSystray } from "@project_support_request/support_request_systray/support_request_systray";

defineMailModels();

const PRIORITY_LEVELS = { 4: "Highest", 3: "High", 2: "Medium", 1: "Low", 0: "Lowest" };

function mockRequests(tickets, priorityLevels = PRIORITY_LEVELS) {
    onRpc("/project_support_request/config", () => ({
        project_id: 1,
        uid: 7,
        task_types: [],
        priority_levels: priorityLevels,
    }));
    onRpc("/project_support_request/systray", () => ({
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
