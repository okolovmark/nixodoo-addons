import { describe, expect, test } from "@odoo/hoot";
import { queryAll, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { animationFrame, mockDate } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { Component, t, toRaw, useProps, xml } from "@odoo/owl";
import {
    contains,
    getService,
    makeServerError,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { registry } from "@web/core/registry";
import { pick } from "@web/core/utils/objects";
import { CallbackRecorder } from "@web/search/action_hook";
import { WebClient } from "@web/webclient/webclient";
import { GanttView, makeGanttSettings } from "@project_team_dashboard/gantt/gantt_view";
import { TeamDashboardAction } from "@project_team_dashboard/team_dashboard/team_dashboard_action";

defineMailModels();

const PROJECTS = [
    { id: 1, name: "Website", key: "WEB" },
    { id: 2, name: "Mobile App", key: false },
];
const USERS = [
    { id: 9, name: "Boss" },
    { id: 7, name: "Mitchell Admin" },
];
// In the server's own order (by name), which is not the order of the full names.
const DEPARTMENTS = [
    { id: 5, name: "Backend", complete_name: "Company / R&D / Backend" },
    { id: 3, name: "R&D", complete_name: "Company / R&D" },
    { id: 4, name: "Sales", complete_name: "Company / Sales" },
];
const THIS_WEEK = { date_start: "2026-11-16", date_end: "2026-11-22" };
const LAST_WEEK = { date_start: "2026-11-09", date_end: "2026-11-15" };
const THIS_MONTH = { date_start: "2026-11-01", date_end: "2026-11-30" };
const LAST_MONTH = { date_start: "2026-10-01", date_end: "2026-10-31" };

function makeTask(values) {
    return {
        key: "",
        stage_name: "In Progress",
        planned_hours: 0,
        remaining_hours: 0,
        planned_date_end: "",
        is_overdue: false,
        is_subtask: false,
        parent_key: "",
        project_name: "WEB - Website",
        ...values,
    };
}

function makePayload() {
    return {
        project_name: "Website",
        period_start: THIS_WEEK.date_start,
        period_end: THIS_WEEK.date_end,
        members: [
            {
                user_id: 7,
                user_name: "Mitchell Admin",
                user_avatar_url: "/web/image/res.users/7/avatar_128",
                current_task: makeTask({
                    id: 31,
                    key: "WEB-31",
                    name: "Checkout page",
                    planned_hours: 8,
                    remaining_hours: 2,
                    planned_date_end: "2026-11-18",
                    is_overdue: true,
                }),
                queue: [makeTask({ id: 32, key: "WEB-32", name: "Cart", remaining_hours: 3 })],
                backlog: [
                    makeTask({ id: 33, key: "WEB-33", name: "Search", remaining_hours: 4 }),
                    makeTask({ id: 34, name: "Wishlist", remaining_hours: 1.5 }),
                ],
                other: [],
                total_open_tasks: 4,
                total_planned_hours: 16.5,
                total_remaining_hours: 10.5,
                hours_logged_period: 6.25,
                hours_logged_today: 1,
                estimated_completion: "2026-11-25",
            },
            {
                user_id: false,
                user_name: "Unassigned",
                user_avatar_url: false,
                current_task: false,
                queue: [],
                backlog: [makeTask({ id: 35, key: "WEB-35", name: "Footer", remaining_hours: 2 })],
                other: [],
                total_open_tasks: 1,
                total_planned_hours: 2,
                total_remaining_hours: -1.5,
                hours_logged_period: 0,
                hours_logged_today: 0,
                estimated_completion: false,
            },
        ],
    };
}

function serveDirectory({ defaultProjectId = false } = {}) {
    onRpc("project.project", "search_read", ({ kwargs }) => {
        expect(kwargs.domain).toEqual([["is_template", "=", false]]);
        return PROJECTS;
    });
    onRpc("res.users", "search_read", () => USERS);
    onRpc("hr.department", "search_read", ({ kwargs }) => {
        // The mock server orders on any field; the real one refuses the computed complete_name.
        expect(kwargs.order || "").not.toMatch(/complete_name/);
        return DEPARTMENTS;
    });
    onRpc("res.users", "read", ({ args }) => {
        if (!args[1]?.includes("dashboard_default_project_id")) {
            return;
        }
        const project = PROJECTS.find((project) => project.id === defaultProjectId);
        return [
            {
                id: 7,
                department_id: [3, "Company / R&D"],
                dashboard_default_project_id: project ? [project.id, project.name] : false,
            },
        ];
    });
}

/** Answers the dashboard methods and records each call with the arguments the screen sent. */
function serveDashboard(answer = makePayload) {
    onRpc(
        "project.project",
        [
            "get_team_dashboard_data",
            "get_team_dashboard_by_manager",
            "get_team_dashboard_by_department",
        ],
        ({ method, args, kwargs }) => {
            const sent = pick(
                kwargs,
                "project_id",
                "user_id",
                "department_id",
                "max_depth",
                "date_start",
                "date_end"
            );
            expect.step({ method, args, ...sent });
            return answer(sent);
        }
    );
}

/**
 * @param {Object} [context] the action's
 * @param {Object} [options]
 * @param {Object} [options.state] the state the action service hands back on a breadcrumb return
 * @param {CallbackRecorder} [options.localState] collects the state the dashboard exports
 */
function mountDashboard(context = { default_project_id: 1 }, { state, localState } = {}) {
    mockDate("2026-11-19 10:00:00");
    const action = { type: "ir.actions.client", tag: "project_team_dashboard", context };
    return mountWithCleanup(TeamDashboardAction, {
        props: state ? { action, state } : { action },
        componentEnv: localState ? { __getLocalState__: localState } : {},
    });
}

function projectCall(values = {}) {
    return { method: "get_team_dashboard_data", args: [], project_id: 1, ...THIS_WEEK, ...values };
}

function managerCall(values = {}) {
    return {
        method: "get_team_dashboard_by_manager",
        args: [],
        user_id: 7,
        max_depth: 0,
        ...THIS_WEEK,
        ...values,
    };
}

function departmentCall(values = {}) {
    return {
        method: "get_team_dashboard_by_department",
        args: [],
        department_id: 3,
        ...THIS_WEEK,
        ...values,
    };
}

const MEMBER = '.o_team_dashboard_member[data-user-id="7"]';
const UNASSIGNED = '.o_team_dashboard_member[data-user-id="unassigned"]';

describe("on desktop", () => {
    describe.current.tags("desktop");

    test("the action is registered under the client action tag", () => {
        expect(registry.category("actions").get("project_team_dashboard")).toBe(
            TeamDashboardAction
        );
    });

    test("cards show the queues, hours, deadline, ETA and the unassigned card", async () => {
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);

        expect(".o_team_dashboard_member").toHaveCount(2);
        expect(MEMBER).toHaveClass(["border-danger", "border-2"]);
        expect(`${MEMBER} .o_team_dashboard_member_name`).toHaveText("Mitchell Admin");
        expect(`${MEMBER} .card-header small.text-muted`).toHaveText("4 tasks · 10.5h remaining");
        expect(`${MEMBER} .o_team_dashboard_hours_period`).toHaveText("6.3h");
        expect(`${MEMBER} .o_team_dashboard_hours_today`).toHaveText("Today: 1.0h");
        expect(`${MEMBER} .o_team_dashboard_current_task .o_team_dashboard_task_link`).toHaveText(
            "WEB-31 Checkout page"
        );
        expect(`${MEMBER} .o_team_dashboard_current_hours`).toHaveText(
            "2.0h remaining / 8.0h estimated"
        );
        expect(`${MEMBER} .progress`).toHaveAttribute("aria-valuenow", "75");
        expect(queryOne(`${MEMBER} .progress-bar`).style.width).toBe("75%");
        expect(`${MEMBER} .o_team_dashboard_deadline`).toHaveText("Deadline: 11/18/2026 (OVERDUE)");
        expect(`${MEMBER} .o_team_dashboard_planned_hours`).toHaveText("16.5h estimated");
        expect(`${MEMBER} .o_team_dashboard_eta`).toHaveText("ETA: 11/25/2026");
        expect(queryAllTexts(`${MEMBER} .o_team_dashboard_queue_toggle`)).toEqual([
            "In Progress Queue 1",
            "Backlog 2",
        ]);
        expect(`${MEMBER} .o_team_dashboard_member_backlog`).toHaveCount(1);
        expect(`${MEMBER} .o_team_dashboard_member_closed`).toHaveCount(1);
        expect(".o_team_dashboard_period_info").toHaveText("Website · 11/16/2026 to 11/22/2026");

        await contains(`${MEMBER} .o_team_dashboard_queue_toggle:contains(Backlog)`).click();
        expect(`${MEMBER} .o_team_dashboard_queue_list li`).toHaveCount(2);
        expect(
            queryAllTexts(`${MEMBER} .o_team_dashboard_queue_list .o_team_dashboard_task_link`)
        ).toEqual(["WEB-33 Search", "Wishlist"]);
        expect(queryAllTexts(`${MEMBER} .o_team_dashboard_queue_hours`)).toEqual(["4.0h", "1.5h"]);
        await contains(`${MEMBER} .o_team_dashboard_queue_toggle:contains(Backlog)`).click();
        expect(`${MEMBER} .o_team_dashboard_queue_list`).toHaveCount(0);

        expect(`${UNASSIGNED} .o_team_dashboard_member_name`).toHaveText("Unassigned");
        expect(`${UNASSIGNED} .card-header small.text-muted`).toHaveText(
            "1 tasks · 1.5h over estimate"
        );
        expect(`${UNASSIGNED} .o_team_dashboard_queue_toggle`).toHaveText("Backlog 1");
        expect(`${UNASSIGNED} .card-body`).toHaveText(/No active task/);
        expect(`${UNASSIGNED} .o_team_dashboard_member_backlog`).toHaveCount(0);
        expect(`${UNASSIGNED} .o_team_dashboard_member_closed`).toHaveCount(0);
    });

    test("without a project in the action the user's default project is shown", async () => {
        serveDirectory({ defaultProjectId: 2 });
        serveDashboard();
        await mountDashboard({});
        expect.verifySteps([projectCall({ project_id: 2 })]);
        expect(".o_team_dashboard_project_picker input").toHaveValue("Mobile App");
    });

    test("picking a project loads its cards", async () => {
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);
        expect(".o_team_dashboard_project_picker input").toHaveValue("WEB - Website");

        await contains(".o_team_dashboard_project_picker input").click();
        await contains(".o-autocomplete--dropdown-item:contains(Mobile App)").click();
        expect.verifySteps([projectCall({ project_id: 2 })]);
        expect(".o_team_dashboard_project_picker input").toHaveValue("Mobile App");
    });

    test("the modes send the manager and the department of the user", async () => {
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);

        await contains("button[data-mode=manager]").click();
        expect("button[data-mode=manager]").toHaveClass("btn-primary");
        expect.verifySteps([managerCall()]);
        expect(".o_team_dashboard_manager_picker input").toHaveValue("Mitchell Admin");
        expect(".o_team_dashboard_open_backlog").toHaveCount(0);
        expect(".o_team_dashboard_view_gantt").toHaveCount(0);

        await contains(".o_team_dashboard_depth").select("2");
        expect.verifySteps([managerCall({ max_depth: 2 })]);

        await contains("button[data-mode=department]").click();
        expect.verifySteps([departmentCall()]);
        expect(".o_team_dashboard_department_picker input").toHaveValue("Company / R&D");
        await contains(".o_team_dashboard_department_picker input").click();
        expect(queryAllTexts(".o-autocomplete--dropdown-item")).toEqual([
            "Company / R&D",
            "Company / R&D / Backend",
            "Company / Sales",
        ]);

        await contains("button[data-mode=project]").click();
        expect.verifySteps([projectCall()]);
        expect(".o_team_dashboard_open_backlog").toHaveCount(1);
    });

    test("each period sends its dates", async () => {
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);

        await contains(".o_team_dashboard_period").select("last_week");
        await contains(".o_team_dashboard_period").select("this_month");
        await contains(".o_team_dashboard_period").select("last_month");
        expect.verifySteps([
            projectCall({ date_start: "2026-11-09", date_end: "2026-11-15" }),
            projectCall({ date_start: "2026-11-01", date_end: "2026-11-30" }),
            projectCall({ date_start: "2026-10-01", date_end: "2026-10-31" }),
        ]);

        await contains(".o_team_dashboard_period").select("custom");
        await contains(".o_team_dashboard_date_start input").edit("09/03/2026");
        expect(".o_team_dashboard_date_start input").toHaveValue("09/03/2026");
        expect.verifySteps([]);
        await contains(".o_team_dashboard_date_end input").edit("09/17/2026");
        expect.verifySteps([projectCall({ date_start: "2026-09-03", date_end: "2026-09-17" })]);
    });

    for (const { mode, key, refused, kept, makeCall } of [
        {
            mode: "manager",
            key: "user_id",
            refused: { id: 9, name: "Boss" },
            kept: { id: 7, name: "Mitchell Admin" },
            makeCall: managerCall,
        },
        {
            mode: "department",
            key: "department_id",
            refused: { id: 4, name: "Company / Sales" },
            kept: { id: 3, name: "Company / R&D" },
            makeCall: departmentCall,
        },
    ]) {
        test(`a refused ${mode} shows the server's message and keeps the previous one`, async () => {
            expect.errors(1);
            serveDirectory();
            serveDashboard((sent) => {
                if (sent[key] === refused.id) {
                    throw makeServerError({
                        type: "AccessError",
                        message: "You can only see your own team.",
                    });
                }
                return { members: [] };
            });
            await mountDashboard();
            await contains(`button[data-mode=${mode}]`).click();
            expect.verifySteps([projectCall(), makeCall({ [key]: kept.id })]);
            const picker = `.o_team_dashboard_${mode}_picker input`;

            await contains(picker).click();
            await contains(`.o-autocomplete--dropdown-item:contains(${refused.name})`).click();
            await animationFrame();
            expect(".o_error_dialog").toHaveText(/You can only see your own team\./);
            expect(picker).toHaveValue(kept.name);
            expect.verifyErrors([/RPC_ERROR/]);

            await contains(".o_dialog .o-default-button").click();
            await contains(".o_team_dashboard_refresh").click();
            expect.verifySteps([makeCall({ [key]: refused.id }), makeCall({ [key]: kept.id })]);
        });
    }

    test("an accepted manager stays picked", async () => {
        serveDirectory();
        serveDashboard(() => ({ members: [], manager_name: "Boss" }));
        await mountDashboard();
        await contains("button[data-mode=manager]").click();
        await contains(".o_team_dashboard_manager_picker input").click();
        await contains(".o-autocomplete--dropdown-item:contains(Boss)").click();
        expect(".o_team_dashboard_manager_picker input").toHaveValue("Boss");
        expect(".o_team_dashboard_period_info").toHaveText(/Subordinates of Boss/);
        await contains(".o_team_dashboard_refresh").click();
        expect.verifySteps([
            projectCall(),
            managerCall(),
            managerCall({ user_id: 9 }),
            managerCall({ user_id: 9 }),
        ]);
    });

    test("member and toolbar actions open the matching task views", async () => {
        serveDirectory();
        serveDashboard();
        mockService("action", {
            doAction(action, options) {
                expect.step(options ? [action, options] : action);
            },
        });
        await mountDashboard();
        expect.verifySteps([projectCall()]);

        await contains(`${MEMBER} .o_team_dashboard_current_task .o_team_dashboard_task_link`).click();
        await contains(`${MEMBER} .o_team_dashboard_member_backlog`).click();
        await contains(`${MEMBER} .o_team_dashboard_member_kanban`).click();
        await contains(`${MEMBER} .o_team_dashboard_member_closed`).click();
        await contains(".o_team_dashboard_open_backlog").click();
        await contains(".o_team_dashboard_open_kanban").click();
        await contains(".o_team_dashboard_open_my_tasks").click();
        await contains(".o_team_dashboard_open_project").click();
        const kanbanViews = [
            [false, "kanban"],
            [false, "form"],
        ];
        const projectTasks = [
            ["project_id", "=", 1],
            ["has_template_ancestor", "=", false],
        ];
        expect.verifySteps([
            {
                type: "ir.actions.act_window",
                res_model: "project.task",
                res_id: 31,
                views: [[false, "form"]],
                target: "current",
            },
            [
                "project_team_dashboard.action_project_task_backlog",
                {
                    additionalContext: {
                        search_default_project_id: 1,
                        default_project_id: 1,
                        search_default_user_ids: 7,
                    },
                },
            ],
            {
                type: "ir.actions.act_window",
                name: "Kanban",
                res_model: "project.task",
                views: kanbanViews,
                domain: [
                    ["project_id", "=", 1],
                    ["has_template_ancestor", "=", false],
                    ["user_ids", "in", [7]],
                ],
                context: { default_project_id: 1 },
            },
            {
                type: "ir.actions.act_window",
                name: "Closed Tasks",
                res_model: "project.task",
                views: [
                    [false, "list"],
                    [false, "form"],
                ],
                domain: [
                    ["user_ids", "in", [7]],
                    ["is_closed", "=", true],
                    ["has_template_ancestor", "=", false],
                    ["project_id", "=", 1],
                ],
                target: "current",
            },
            [
                "project_team_dashboard.action_project_task_backlog",
                {
                    additionalContext: {
                        search_default_project_id: 1,
                        default_project_id: 1,
                        search_default_unassigned: true,
                    },
                },
            ],
            {
                type: "ir.actions.act_window",
                name: "Kanban",
                res_model: "project.task",
                views: kanbanViews,
                domain: projectTasks,
                context: { default_project_id: 1 },
            },
            {
                type: "ir.actions.act_window",
                name: "My Tasks",
                res_model: "project.task",
                views: [
                    [false, "kanban"],
                    [false, "list"],
                    [false, "form"],
                ],
                domain: projectTasks,
                context: { search_default_my_tasks: 1, default_project_id: 1 },
            },
            {
                type: "ir.actions.act_window",
                res_model: "project.project",
                res_id: 1,
                views: [[false, "form"]],
                target: "current",
            },
        ]);
    });

    test("outside the project mode the closed tasks of a member span all projects", async () => {
        serveDirectory();
        serveDashboard();
        mockService("action", {
            doAction(action) {
                expect.step(action.domain);
            },
        });
        await mountDashboard();
        await contains("button[data-mode=manager]").click();
        expect.verifySteps([projectCall(), managerCall()]);
        expect(`${MEMBER} .o_team_dashboard_member_closed`).toHaveCount(1);
        expect(`${MEMBER} .o_team_dashboard_member_backlog`).toHaveCount(0);

        await contains(`${MEMBER} .o_team_dashboard_member_closed`).click();
        expect.verifySteps([
            [
                ["user_ids", "in", [7]],
                ["is_closed", "=", true],
                ["has_template_ancestor", "=", false],
                ["project_id", "!=", false],
                ["has_project_template", "=", false],
            ],
        ]);
    });

    test("the Gantt view replaces the cards and the filters, Refresh reloads it in place, and its settings outlive it", async () => {
        expect(TeamDashboardAction.components.GanttView).toBe(GanttView);
        // The Gantt has its own suite: here a stand-in with the same props checks what it is given.
        const ganttMounts = [];
        const ganttSettings = [];
        class GanttStandIn extends Component {
            static template = xml`
                <div class="o_gantt_stand_in" t-att-data-reload-count="this.props.reloadCount"
                     t-att-data-scale="this.props.settings.scale"/>`;
            props = useProps({
                projectId: t.or([t.number(), t.literal(false)]),
                projects: t.array(),
                reloadCount: t.number(),
                settings: t.object(),
            });

            setup() {
                ganttMounts.push({ projectId: this.props.projectId, projects: this.props.projects });
                ganttSettings.push(this.props.settings);
            }
        }
        patchWithCleanup(TeamDashboardAction.components, { GanttView: GanttStandIn });
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);
        expect(".o_team_dashboard_filters").toHaveCount(1);
        expect(".o_team_dashboard_cards").toHaveCount(1);

        await contains(".o_team_dashboard_view_gantt").click();
        expect(".o_team_dashboard_view_gantt").toHaveClass("active");
        expect(".o_gantt_stand_in").toHaveCount(1);
        expect(".o_team_dashboard_filters").toHaveCount(0);
        expect(".o_team_dashboard_cards").toHaveCount(0);
        expect(ganttMounts).toEqual([{ projectId: 1, projects: PROJECTS }]);
        expect(".o_gantt_stand_in").toHaveAttribute("data-reload-count", "0");
        // what the chart is left at, as its scale buttons set it
        ganttSettings[0].scale = "month";

        await contains(".o_team_dashboard_refresh").click();
        expect.verifySteps([projectCall()]);
        expect(ganttMounts).toHaveLength(1);
        expect(".o_gantt_stand_in").toHaveAttribute("data-reload-count", "1");

        await contains(".o_team_dashboard_view_cards").click();
        expect(".o_team_dashboard_cards").toHaveCount(1);
        expect(".o_gantt_stand_in").toHaveCount(0);
        await contains(".o_team_dashboard_refresh").click();
        expect.verifySteps([projectCall()]);

        await contains(".o_team_dashboard_view_gantt").click();
        expect(ganttMounts).toHaveLength(2);
        expect(".o_gantt_stand_in").toHaveAttribute("data-reload-count", "1");
        expect(toRaw(ganttSettings[1])).toBe(toRaw(ganttSettings[0]));
        expect(".o_gantt_stand_in").toHaveAttribute("data-scale", "month");
        await contains("button[data-mode=manager]").click();
        expect(".o_team_dashboard_cards").toHaveCount(1);
        expect(".o_gantt_stand_in").toHaveCount(0);
        expect(".o_team_dashboard_view_gantt").toHaveCount(0);
        expect.verifySteps([managerCall()]);
    });

    test("a breadcrumb return brings the dashboard back as it was left", async () => {
        class Elsewhere extends Component {
            static template = xml`<div class="o_elsewhere"/>`;
            props = useProps();
        }
        registry.category("actions").add("project_team_dashboard_test_elsewhere", Elsewhere);
        serveDirectory();
        serveDashboard();
        mockDate("2026-11-19 10:00:00");
        await mountWithCleanup(WebClient);
        await getService("action").doAction({
            type: "ir.actions.client",
            tag: "project_team_dashboard",
            context: { default_project_id: 1 },
        });
        await contains("button[data-mode=manager]").click();
        await contains(".o_team_dashboard_manager_picker input").click();
        await contains(".o-autocomplete--dropdown-item:contains(Boss)").click();
        await contains(".o_team_dashboard_depth").select("2");
        await contains(".o_team_dashboard_period").select("last_month");
        const lastMonthOfBoss = managerCall({ user_id: 9, max_depth: 2, ...LAST_MONTH });
        expect.verifySteps([
            projectCall(),
            managerCall(),
            managerCall({ user_id: 9 }),
            managerCall({ user_id: 9, max_depth: 2 }),
            lastMonthOfBoss,
        ]);

        await getService("action").doAction("project_team_dashboard_test_elsewhere");
        expect(".o_elsewhere").toHaveCount(1);
        expect(".o_team_dashboard").toHaveCount(0);
        await getService("action").restore();
        await animationFrame();
        expect.verifySteps([lastMonthOfBoss]);
        expect("button[data-mode=manager]").toHaveClass("btn-primary");
        expect(".o_team_dashboard_manager_picker input").toHaveValue("Boss");
        expect(".o_team_dashboard_depth").toHaveValue("2");
        expect(".o_team_dashboard_period").toHaveValue("last_month");
    });

    test("the state handed back is the one shown, the Gantt's settings included, and the one exported", async () => {
        const ganttSettings = [];
        class GanttStandIn extends Component {
            static template = xml`<div class="o_gantt_stand_in"/>`;
            props = useProps({
                projectId: t.or([t.number(), t.literal(false)]),
                projects: t.array(),
                reloadCount: t.number(),
                settings: t.object(),
            });

            setup() {
                ganttSettings.push(this.props.settings);
            }
        }
        patchWithCleanup(TeamDashboardAction.components, { GanttView: GanttStandIn });
        serveDirectory();
        serveDashboard();
        const keptDashboard = {
            mode: "project",
            viewMode: "gantt",
            projectId: 2,
            managerId: 9,
            maxDepth: 3,
            departmentId: 4,
            period: "last_week",
            customStart: false,
            customEnd: false,
        };
        const keptGantt = {
            ...makeGanttSettings(),
            projectIds: [2, 1],
            scale: "month",
            groupBy: "stage",
            showCompleted: true,
            facets: [{ field: "user_names", fieldLabel: "Assignee", value: "Bob" }],
            window: { start: Date.UTC(2026, 9, 5), end: Date.UTC(2026, 9, 31) },
        };
        const localState = new CallbackRecorder();
        await mountDashboard({}, { state: { dashboard: keptDashboard, gantt: keptGantt }, localState });
        expect.verifySteps([projectCall({ project_id: 2, ...LAST_WEEK })]);
        expect(".o_team_dashboard_view_gantt").toHaveClass("active");
        expect(ganttSettings).toHaveLength(1);
        expect(toRaw(ganttSettings[0])).toEqual(keptGantt);
        const [exportState] = localState.callbacks;
        expect(exportState()).toEqual({ dashboard: keptDashboard, gantt: keptGantt });

        await contains(".o_team_dashboard_view_cards").click();
        expect(".o_team_dashboard_project_picker input").toHaveValue("Mobile App");
        expect(".o_team_dashboard_period").toHaveValue("last_week");
        await contains("button[data-mode=department]").click();
        expect.verifySteps([departmentCall({ department_id: 4, ...LAST_WEEK })]);
        expect(".o_team_dashboard_department_picker input").toHaveValue("Company / Sales");
        await contains(".o_team_dashboard_period").select("this_month");
        expect.verifySteps([departmentCall({ department_id: 4, ...THIS_MONTH })]);
        expect(exportState()).toEqual({
            dashboard: { ...keptDashboard, mode: "department", viewMode: "cards", period: "this_month" },
            gantt: keptGantt,
        });
    });

    test("a refused manager whose answer a later change overtook gives way to the accepted one", async () => {
        expect.errors(1);
        serveDirectory();
        const firstAnswer = Promise.withResolvers();
        let refusals = 0;
        serveDashboard((sent) => {
            if (sent.user_id !== 9) {
                return { members: [] };
            }
            const refusal = makeServerError({ type: "AccessError", message: "You can only see your own team." });
            refusals++;
            if (refusals === 1) {
                return firstAnswer.promise.then(() => Promise.reject(refusal));
            }
            throw refusal;
        });
        const localState = new CallbackRecorder();
        await mountDashboard(undefined, { localState });
        await contains("button[data-mode=manager]").click();
        await contains(".o_team_dashboard_manager_picker input").click();
        await contains(".o-autocomplete--dropdown-item:contains(Boss)").click();
        // a manager the server has not accepted yet is not one to come back to
        expect(localState.callbacks[0]().dashboard.managerId).toBe(7);

        await contains(".o_team_dashboard_depth").select("2");
        await animationFrame();
        expect(".o_error_dialog").toHaveText(/You can only see your own team\./);
        expect(".o_team_dashboard_manager_picker input").toHaveValue("Mitchell Admin");
        expect.verifyErrors([/RPC_ERROR/]);
        firstAnswer.resolve();
        await animationFrame();
        expect(".o_team_dashboard_manager_picker input").toHaveValue("Mitchell Admin");

        await contains(".o_dialog .o-default-button").click();
        await contains(".o_team_dashboard_refresh").click();
        expect.verifySteps([
            projectCall(),
            managerCall(),
            managerCall({ user_id: 9 }),
            managerCall({ user_id: 9, max_depth: 2 }),
            managerCall({ max_depth: 2 }),
        ]);
    });

    test("every CSS variable the stylesheet sets is one the web client reads, and the hours bar is thin", async () => {
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);
        const readNames = new Set();
        const dashboardRules = [];
        for (const sheet of document.styleSheets) {
            for (const rule of sheet.cssRules) {
                for (const [, name] of rule.cssText.matchAll(/var\((--[\w-]+)/g)) {
                    readNames.add(name);
                }
                if (rule.selectorText?.includes(".o_team_dashboard")) {
                    dashboardRules.push(rule);
                }
            }
        }
        const setNames = dashboardRules.flatMap((rule) => [...rule.style].filter((name) => name.startsWith("--")));
        const dashboardReads = dashboardRules.flatMap((rule) =>
            [...rule.cssText.matchAll(/var\((--[\w-]+)/g)].map(([, name]) => name)
        );
        const rootStyle = getComputedStyle(document.documentElement);
        expect(setNames.length).toBeGreaterThan(1);
        expect(setNames.filter((name) => !readNames.has(name))).toEqual([]);
        expect(
            dashboardReads.filter((name) => !setNames.includes(name) && !rootStyle.getPropertyValue(name))
        ).toEqual([]);

        const barHeight = queryOne(`${MEMBER} .progress`).getBoundingClientRect().height;
        expect(barHeight).toBeCloseTo(0.375 * parseFloat(rootStyle.fontSize), { margin: 0.5 });
    });

    test("a dashboard error is shown in place of the cards", async () => {
        serveDirectory();
        serveDashboard(() => ({ error: "Project not found" }));
        await mountDashboard();
        expect.verifySteps([projectCall()]);
        expect(".o_team_dashboard_cards .alert-warning").toHaveText("Project not found");
        expect(".o_team_dashboard_mode").toHaveCount(3);
    });
});

describe("on a phone", () => {
    describe.current.tags("mobile");

    test("the toolbar, the filters and the cards fit the screen width", async () => {
        serveDirectory();
        serveDashboard();
        await mountDashboard();
        expect.verifySteps([projectCall()]);
        expect(".o_team_dashboard_member").toHaveCount(2);
        expect(".o_team_dashboard_project_picker input").toBeVisible();
        expect(".o_team_dashboard_open_backlog span").not.toBeVisible();
        const screenRight = queryOne(".o_team_dashboard").getBoundingClientRect().right;
        const overflowing = queryAll(".o_team_dashboard *")
            .filter((element) => element.getBoundingClientRect().right > screenRight + 1)
            .map((element) => element.className);
        expect(overflowing).toEqual([]);
    });
});
