import { before, beforeEach, describe, expect, test } from "@odoo/hoot";
import {
    click,
    drag,
    edit,
    hover,
    manuallyDispatchProgrammaticEvent,
    pointerDown,
    pointerUp,
    press,
    queryAll,
    queryAllTexts,
    queryOne,
    waitFor,
} from "@odoo/hoot-dom";
import { advanceTime, animationFrame, mockDate, mockTimeZone } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { Component, proxy, xml } from "@odoo/owl";
import {
    allowTranslations,
    contains,
    makeServerError,
    mockService,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
    serverState,
} from "@web/../tests/web_test_helpers";
import { downloadFile } from "@web/core/network/download";

import {
    DAY_MS,
    activeColumns,
    analyzeDependencies,
    assignStageColors,
    buildGanttModel,
    buildStageHistoryRows,
    buildTaskTree,
    dateToUtc,
    elbowPath,
    filterTasksByFacets,
    findGanttTasks,
    findOverlappingBarIds,
    ganttBarRules,
    ganttPanBounds,
    groupTasksByStage,
    isAncestorPair,
    localDayLabel,
    outsideParentOf,
    searchValues,
    shiftByWholeDays,
    shiftGanttWindow,
    shownColumnsWidth,
    sidebarMaxWidth,
    stageHistorySpan,
    utcToDate,
} from "@project_team_dashboard/gantt/gantt_model";
import { GanttView, makeGanttSettings } from "@project_team_dashboard/gantt/gantt_view";
import { visTimeline } from "@project_team_dashboard/gantt/vis_timeline";

const { DateTime } = luxon;

defineMailModels();

const PROJECTS = [
    { id: 1, name: "Website", key: "PROJ" },
    { id: 2, name: "Mobile App", key: false },
];

function makeTask(values) {
    return {
        name: "Task",
        key: "",
        parent_id: false,
        child_ids: [],
        planned_date_start: "2026-11-16 08:00:00",
        planned_date_end: "2026-11-18 17:00:00",
        has_planned_date_start: true,
        has_planned_date_end: true,
        plan_date_start: "2026-11-16",
        plan_date_end: "2026-11-18",
        stage_id: 11,
        stage_name: "In Progress",
        stage_sequence: 2,
        is_closed: false,
        is_overdue: false,
        user_names: "Alice",
        user_list: [{ id: 7, name: "Alice" }],
        progress: 0,
        planned_hours: 0,
        remaining_hours: 0,
        effective_hours: 0,
        depend_on_ids: [],
        depend_on_lags: {},
        task_type: false,
        parent_label: "",
        project_id: 1,
        date_deadline: false,
        can_write: true,
        is_planned_from_subtasks: false,
        is_deadline_only: false,
        baseline_date_start: false,
        baseline_date_end: false,
        baseline_shift_days: false,
        stage_history: [],
        ...values,
    };
}

/** Alice designs, Alice and Bob build after the design, Bob builds the API under the build. */
function makeTasks() {
    return [
        makeTask({
            id: 1,
            key: "PROJ-1",
            name: "Design",
            planned_hours: 10,
            remaining_hours: 6,
            progress: 40,
            date_deadline: "2026-11-17",
            is_overdue: true,
        }),
        makeTask({
            id: 2,
            key: "PROJ-2",
            name: "Build",
            child_ids: [3],
            planned_date_start: "2026-11-19 08:00:00",
            planned_date_end: "2026-11-25 17:00:00",
            plan_date_start: "2026-11-19",
            plan_date_end: "2026-11-25",
            user_names: "Alice, Bob",
            user_list: [
                { id: 7, name: "Alice" },
                { id: 8, name: "Bob" },
            ],
            depend_on_ids: [1],
            depend_on_lags: { 1: 0 },
        }),
        makeTask({
            id: 3,
            key: "PROJ-3",
            name: "Build API",
            parent_id: 2,
            parent_label: "PROJ-2",
            planned_date_start: "2026-11-20 08:00:00",
            planned_date_end: "2026-11-22 17:00:00",
            plan_date_start: "2026-11-20",
            plan_date_end: "2026-11-22",
            user_names: "Bob",
            user_list: [{ id: 8, name: "Bob" }],
            stage_id: 12,
            stage_name: "Review",
            stage_sequence: 3,
        }),
        makeTask({
            id: 4,
            key: "PROJ-4",
            name: "Launch",
            task_type: "milestone",
            planned_date_start: "2026-11-27 08:00:00",
            planned_date_end: "2026-11-27 08:00:00",
            plan_date_start: "2026-11-27",
            plan_date_end: "2026-11-27",
            user_names: "",
            user_list: [],
        }),
    ];
}

/** Leaves the task its start or end date alone, the other padded by a day as get_gantt_chart_data does. */
function withOneDate(task, realSide) {
    const [realKey, paddedKey, padding] =
        realSide === "start"
            ? ["planned_date_start", "planned_date_end", DAY_MS]
            : ["planned_date_end", "planned_date_start", -DAY_MS];
    Object.assign(task, {
        [paddedKey]: dateToUtc(utcToDate(task[realKey]).getTime() + padding),
        [`has_${paddedKey}`]: false,
        plan_date_start: false,
        plan_date_end: false,
    });
}

const CLOSED_TASK = makeTask({
    id: 5,
    key: "PROJ-5",
    name: "Kick-off",
    planned_date_start: "2026-11-10 08:00:00",
    planned_date_end: "2026-11-12 17:00:00",
    stage_id: 13,
    stage_name: "Done",
    stage_sequence: 9,
    is_closed: true,
});

const UNSCHEDULED_TASK = {
    id: 6,
    key: "PROJ-6",
    name: "Someday",
    stage_name: "In Progress",
    is_closed: false,
    user_names: "",
    planned_hours: 0,
    task_type: false,
};

function makePayload(values = {}) {
    return {
        tasks: makeTasks(),
        today: "2026-11-18",
        tasks_without_dates: 1,
        user_tz: "Europe/Brussels",
        columns: ["deadline"],
        is_project_manager: true,
        unscheduled: [UNSCHEDULED_TASK],
        ...values,
    };
}

/** Hoot fills the parts a Date constructor leaves out from the mocked clock: give them all. */
function localDate(year, monthIndex, day) {
    return new Date(year, monthIndex, day, 0, 0, 0, 0);
}

/** The bytes of a downloaded file, read natively: Hoot's Blob mock answers `text()` with its parts. */
function readBlob(blob) {
    return new Promise((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.readAsText(blob);
    });
}

function labelTexts() {
    return queryAllTexts(".o_gantt_timeline .vis-labelset .vis-label .o_gantt_label_name");
}

/** Serves the Gantt payload and records each load's keyword arguments. */
function serveGanttData(loads, payloadFor = () => makePayload()) {
    onRpc("project.project", "get_gantt_chart_data", ({ kwargs }) => {
        loads.push({
            project_ids: kwargs.project_ids,
            show_completed: kwargs.show_completed,
            include_stage_history: kwargs.include_stage_history,
        });
        return payloadFor(kwargs);
    });
}

async function mountGantt(props = { projectId: 1, projects: PROJECTS }) {
    const gantt = await mountWithCleanup(GanttView, { props });
    await waitFor(".o_gantt_timeline .vis-timeline, .o_gantt_empty_state, .o_gantt_error");
    // vis-timeline redraws on the next frame, and the arrows and columns follow that redraw
    await animationFrame();
    await animationFrame();
    return gantt;
}

/**
 * Drags a bar by whole days the way a user does, so that vis-timeline itself calls the chart's
 * onMove: the bar is selected first, as vis drags selected items only. Given an edge, "left" or
 * "right", drags that edge's resize handle instead. Returns the bar's left edge from before the
 * drag and the helper that drops it.
 */
async function dragBarByDays(gantt, itemId, days, { edge = null } = {}) {
    gantt.timeline.setSelection([itemId]);
    await animationFrame();
    const bar = queryOne(`.o_gantt_timeline .vis-item[data-id='${itemId}']`);
    const { left } = bar.getBoundingClientRect();
    const target = edge ? queryOne(`.vis-drag-${edge}`, { root: bar }) : bar;
    const { left: targetLeft, top, width, height } = target.getBoundingClientRect();
    const start = gantt.itemsDataSet.get(itemId).start;
    const toScreen = gantt.timeline.body.util.toScreen;
    const dayWidth = toScreen(new Date(start.getTime() + DAY_MS)) - toScreen(start);
    const from = { x: targetLeft + width / 2, y: top + height / 2 };
    const { moveTo, drop } = await drag(target, { position: from });
    // the first move only starts vis-timeline's drag: the bar follows the moves after it
    await moveTo(target, { position: { x: from.x + Math.sign(days) * 10, y: from.y } });
    await moveTo(target, { position: { x: from.x + days * dayWidth, y: from.y } });
    await animationFrame();
    return { left, drop };
}

function barLeft(itemId) {
    return queryOne(`.o_gantt_timeline .vis-item[data-id='${itemId}']`).getBoundingClientRect().left;
}

/** Shows and hides the chart and keeps its settings meanwhile, as the dashboard does. */
class GanttKeeper extends Component {
    static components = { GanttView };
    static template = xml`
        <GanttView t-if="this.state.isShown" projectId="1" projects="this.projects" settings="this.settings"/>`;

    setup() {
        this.projects = PROJECTS;
        this.settings = proxy(makeGanttSettings());
        this.state = proxy({ isShown: true });
    }
}

describe.tags("desktop");
describe("gantt model", () => {
    beforeEach(() => {
        allowTranslations();
        mockDate("2026-11-18 10:00:00", 1);
    });

    test("the week scale moves the window by one month, the month scale by three", () => {
        let shifted = shiftGanttWindow(localDate(2026, 6, 1), localDate(2026, 8, 15), "week", 1);
        expect(shifted.start.getTime()).toBe(localDate(2026, 7, 1).getTime());
        expect(shifted.end.getTime()).toBe(localDate(2026, 9, 15).getTime());
        shifted = shiftGanttWindow(localDate(2026, 6, 1), localDate(2026, 8, 15), "month", -1);
        expect(shifted.start.getTime()).toBe(localDate(2026, 3, 1).getTime());
        expect(shifted.end.getTime()).toBe(localDate(2026, 5, 15).getTime());
    });

    test("the day scale moves the window by one week", () => {
        const shifted = shiftGanttWindow(localDate(2026, 6, 1), localDate(2026, 6, 15), "day", 1);
        expect(shifted.start.getTime()).toBe(localDate(2026, 6, 8).getTime());
        expect(shifted.end.getTime()).toBe(localDate(2026, 6, 22).getTime());
    });

    test("the chart can be panned a year beyond the tasks", () => {
        const bounds = ganttPanBounds(localDate(2026, 6, 1), localDate(2026, 8, 15));
        expect(bounds.min.getTime()).toBe(localDate(2025, 6, 1).getTime());
        expect(bounds.max.getTime()).toBe(localDate(2027, 8, 15).getTime());
    });

    test("stages follow their sequence and tasks their start", () => {
        const stages = groupTasksByStage([
            { id: 1, stage_id: 43, stage_name: "Development", stage_sequence: 35, planned_date_start: "2026-08-10 00:00:00" },
            { id: 2, stage_id: 41, stage_name: "Backlog", stage_sequence: 4, planned_date_start: "2026-08-01 00:00:00" },
            { id: 3, stage_id: 43, stage_name: "Development", stage_sequence: 35, planned_date_start: "2026-08-02 00:00:00" },
            { id: 4, stage_id: false, stage_name: "", planned_date_start: "2026-07-01 00:00:00" },
        ]);
        expect(stages.map((stage) => stage.name)).toEqual(["Backlog", "Development", "No Stage"]);
        expect(stages[1].tasks.map((task) => task.id)).toEqual([3, 1]);
        expect(stages[2].tasks.map((task) => task.id)).toEqual([4]);
    });

    test("sub-tasks nest under their parent at every depth", () => {
        const rows = buildTaskTree([
            { id: 1, parent_id: false },
            { id: 4, parent_id: false },
            { id: 3, parent_id: 2 },
            { id: 2, parent_id: 1 },
        ]);
        expect(rows.map((row) => [row.task.id, row.depth, row.childIds, row.outsideParentId])).toEqual([
            [1, 0, [2], false],
            [2, 1, [3], false],
            [3, 2, [], false],
            [4, 0, [], false],
        ]);
    });

    test("a sub-task whose parent is outside the group is a root naming its parent", () => {
        const rows = buildTaskTree([
            { id: 5, parent_id: 9 },
            { id: 6, parent_id: 5 },
        ]);
        expect(rows.map((row) => [row.task.id, row.depth, row.childIds, row.outsideParentId])).toEqual([
            [5, 0, [6], 9],
            [6, 1, [], false],
        ]);
    });

    test("siblings keep the group's order and a parent cycle lists each task once", () => {
        const rows = buildTaskTree([
            { id: 1, parent_id: false },
            { id: 8, parent_id: 1 },
            { id: 7, parent_id: 1 },
        ]);
        expect(rows[0].childIds).toEqual([8, 7]);
        expect(rows.map((row) => row.task.id)).toEqual([1, 8, 7]);
        const cycle = buildTaskTree([
            { id: 1, parent_id: 2 },
            { id: 2, parent_id: 1 },
        ]);
        expect(cycle.map((row) => row.task.id).sort()).toEqual([1, 2]);
    });

    test("a root names its parent from another group, or a hidden parent by its label", () => {
        const taskMap = { 1: { id: 1, key: "PROJ-1" }, 6: { id: 6, key: "", name: "Unkeyed parent" } };
        const row = (task, depth, outsideParentId) => ({ task, depth, outsideParentId });
        expect(outsideParentOf(row({ id: 2, parent_id: 1, parent_label: "PROJ-1" }, 0, 1), taskMap)).toBe("PROJ-1");
        expect(outsideParentOf(row({ id: 7, parent_id: 6, parent_label: "Unkeyed parent" }, 0, 6), taskMap)).toBe(
            "Unkeyed parent"
        );
        expect(outsideParentOf(row({ id: 3, parent_id: false, parent_label: "PROJ-13" }, 0, false), taskMap)).toBe(
            "PROJ-13"
        );
        expect(outsideParentOf(row({ id: 4, parent_id: false, parent_label: "" }, 0, false), taskMap)).toBe(null);
        expect(outsideParentOf(row({ id: 5, parent_id: 1, parent_label: "PROJ-1" }, 1, false), taskMap)).toBe(null);
    });

    test("a parent covering its sub-tasks is not an overlap, unrelated tasks are", () => {
        const taskMap = {
            1: { id: 1, parent_id: false },
            2: { id: 2, parent_id: 1 },
            3: { id: 3, parent_id: 2 },
            4: { id: 4, parent_id: false },
        };
        expect(isAncestorPair(taskMap, 3, 1)).toBe(true);
        expect(isAncestorPair(taskMap, 3, 4)).toBe(false);
        const family = [
            { id: "p", taskId: 1, start: "2026-03-01", end: "2026-03-20" },
            { id: "c", taskId: 2, start: "2026-03-02", end: "2026-03-05" },
            { id: "g", taskId: 3, start: "2026-03-03", end: "2026-03-04" },
        ];
        expect([...findOverlappingBarIds(family, taskMap)]).toEqual([]);
        const withStranger = [...family, { id: "o", taskId: 4, start: "2026-03-04", end: "2026-03-06" }];
        expect([...findOverlappingBarIds(withStranger, taskMap)].sort()).toEqual(["c", "o", "p"]);
        const touching = [
            { id: "a", taskId: 1, start: "2026-03-01", end: "2026-03-05" },
            { id: "b", taskId: 4, start: "2026-03-05", end: "2026-03-08" },
        ];
        expect([...findOverlappingBarIds(touching, taskMap)]).toEqual([]);
    });

    test("a milestone is a point unless sub-task rows give it a length", () => {
        const milestone = {
            task_type: "milestone",
            planned_date_start: "2026-03-01 09:00:00",
            planned_date_end: "2026-03-06 18:00:00",
        };
        expect(ganttBarRules(milestone, true).isPoint).toBe(false);
        expect(ganttBarRules(milestone, false).isPoint).toBe(true);
        expect(ganttBarRules({ ...milestone, planned_date_end: milestone.planned_date_start }, true).isPoint).toBe(true);
    });

    test("dates rolled up from sub-tasks or a deadline alone cannot be dragged", () => {
        const task = { can_write: true, planned_date_start: "2026-03-01", planned_date_end: "2026-03-02" };
        expect(ganttBarRules(task, false).canDrag).toBe(true);
        expect(ganttBarRules({ ...task, is_planned_from_subtasks: true }, true).canDrag).toBe(false);
        expect(ganttBarRules({ can_write: true, is_deadline_only: true }, false).canDrag).toBe(false);
        expect(ganttBarRules({ ...task, can_write: false }, false).canDrag).toBe(false);
    });

    test("each open stage gets its own colour and closed stages are grey", () => {
        const colors = assignStageColors([
            { id: 1, stage_id: 41, stage_name: "Backlog", stage_sequence: 4, is_closed: false },
            { id: 2, stage_id: 43, stage_name: "Development", stage_sequence: 35, is_closed: false },
            { id: 3, stage_id: 45, stage_name: "Delivered", stage_sequence: 54, is_closed: true },
            { id: 4, stage_id: 42, stage_name: "Specifications", stage_sequence: 1, is_closed: false },
        ]);
        expect(colors.map((color) => color.name)).toEqual(["Specifications", "Backlog", "Development", "Delivered"]);
        const solids = colors.map((color) => color.solid);
        expect(new Set(solids.slice(0, 3)).size).toBe(3);
        expect(solids[3]).toBe("#aab7b8");
        expect(colors[0].light).toBe("rgba(142,124,195,0.3)");
    });

    test("an arrow reaches a later successor by one step down, an earlier one around the gap", () => {
        expect(elbowPath(100, 20, 200, 60)).toBe("M100,20 H110 V60 H200");
        expect(elbowPath(100, 20, 105, 60)).toBe("M100,20 H110 V40 H95 V60 H105");
    });

    test("each stage visit is a row chained to the next one, one day wide at least", () => {
        const history = buildStageHistoryRows({
            id: 7,
            stage_history: [
                { stage_id: false, stage_name: "Retired", is_closed: false, start: "2026-09-17 02:00:00", end: "2026-09-17 02:47:00" },
                { stage_id: 43, stage_name: "Development", is_closed: false, start: "2026-09-19 02:46:00", end: "2026-09-25 09:00:00" },
                { stage_id: 45, stage_name: "Done", is_closed: true, start: "2026-09-30 23:30:00", end: "2026-09-30 23:30:00" },
            ],
        });
        expect(history.rows.map((row) => row.id)).toEqual(["sh_7_0", "sh_7_1", "sh_7_2"]);
        expect(history.rows.map((row) => row.stageId)).toEqual([0, 43, 45]);
        expect(history.rows[0].end).toBe("2026-09-18 02:00:00");
        expect(history.rows[0].visitEnd).toBe("2026-09-17 02:47:00");
        expect(history.rows[1].end).toBe("2026-09-25 09:00:00");
        expect(history.rows[2].isClosed).toBe(true);
        expect(history.edges).toEqual([
            { from: "sh_7_0", to: "sh_7_1" },
            { from: "sh_7_1", to: "sh_7_2" },
        ]);
        expect(buildStageHistoryRows({ id: 3 })).toEqual({ rows: [], edges: [] });
        expect(buildStageHistoryRows({ id: 7, stage_history: history.rows.slice(0, 1) }, 5).rows[0].id).toBe("sh_5_7_0");
    });

    test("the stage summary spans the visits, one day at least", () => {
        expect(
            stageHistorySpan({
                stage_history: [
                    { stage_id: 1, start: "2026-08-27 03:24:39", end: "2026-09-02 10:00:00" },
                    { stage_id: 2, start: "2026-09-02 10:00:00", end: "2026-10-14 08:00:00" },
                ],
            })
        ).toEqual({ start: "2026-08-27 03:24:39", end: "2026-10-14 08:00:00" });
        const short = stageHistorySpan({ stage_history: [{ stage_id: 1, start: "2026-09-07 09:48:17", end: "2026-09-07 09:50:00" }] });
        expect(short.end).toBe("2026-09-08 09:48:17");
        expect(stageHistorySpan({})).toBe(null);
    });

    test("go-to-task ranks a key match above a name match, capped at eight", () => {
        const tasks = [
            { id: 1, key: "PROJ-20", name: "Fix PROJ-1684 regression" },
            { id: 2, key: "PROJ-1684", name: "Gantt improvements" },
            { id: 3, key: "PROJ-16841", name: "Other" },
            { id: 4, key: "PROJ-5", name: "Unrelated" },
        ];
        expect(findGanttTasks(tasks, " proj-1684 ").map((task) => task.id)).toEqual([2, 3, 1]);
        const named = [
            { id: 1, key: "", name: "Export the gantt" },
            { id: 2, key: false, name: "Gantt paging" },
        ];
        expect(findGanttTasks(named, "gantt").map((task) => task.id)).toEqual([2, 1]);
        const many = Array.from({ length: 12 }, (_, index) => ({ id: index, key: `T-${index}`, name: "Task" }));
        expect(findGanttTasks(many, "  ")).toEqual([]);
        expect(findGanttTasks(many, "task")).toHaveLength(8);
    });

    test("facets keep the matches with their parent and sub-tasks, values are split per person", () => {
        const tasks = makeTasks();
        const shown = filterTasksByFacets(tasks, [{ field: "user_names", value: "bob" }]);
        expect(shown.map((task) => task.id)).toEqual([2, 3]);
        expect(filterTasksByFacets(tasks, [{ field: "key", value: "PROJ-1" }]).map((task) => task.id)).toEqual([1]);
        expect(searchValues(tasks, "user_names")).toEqual(["Alice", "Bob"]);
        expect(searchValues(tasks, "task_type")).toEqual(["milestone"]);
    });

    test("a successor starting before its predecessor's end plus the lag is violated", () => {
        const tasks = makeTasks();
        expect(analyzeDependencies(tasks).edges).toEqual({ "1_2": { lag: 0, violated: false } });
        tasks[1].depend_on_lags = { 1: 2 };
        expect(analyzeDependencies(tasks).edges).toEqual({ "1_2": { lag: 2, violated: true } });
    });

    test("the grouping column is never shown and columns give way to narrow charts", () => {
        expect(activeColumns(["assignees", "deadline"], "user").map((column) => column.key)).toEqual(["deadline"]);
        expect(activeColumns(["assignees", "stage"], "task").map((column) => column.key)).toEqual(["assignees", "stage"]);
        const columns = activeColumns(["deadline", "planned_hours"], "task");
        expect(shownColumnsWidth(columns, 1200)).toBe(174);
        expect(shownColumnsWidth(columns, 400)).toBe(0);
    });

    test("the name sidebar takes a quarter of the chart, the names' minimum on a phone, 60% at most", () => {
        expect(sidebarMaxWidth(1200, 174)).toBe(474);
        expect(sidebarMaxWidth(360, 0)).toBe(140);
        expect(sidebarMaxWidth(200, 0)).toBe(120);
    });

    test("stored UTC datetimes are read and written as instants, shown as the local day", () => {
        mockTimeZone(-5);
        expect(utcToDate("2026-11-17 02:00:00").getTime()).toBe(Date.UTC(2026, 10, 17, 2));
        expect(dateToUtc(new Date(Date.UTC(2026, 10, 17, 2)))).toBe("2026-11-17 02:00:00");
        expect(dateToUtc(utcToDate("2026-11-12 01:00:00"))).toBe("2026-11-12 01:00:00");
        expect(localDayLabel("2026-11-17 02:00:00")).toBe("Nov 16");
    });

    test("a drag moves by whole days and keeps the time of day", () => {
        mockTimeZone(1);
        const original = utcToDate("2026-11-16 08:00:00");
        const dropped = new Date(original.getTime() + 2 * DAY_MS + 5 * 3600 * 1000);
        expect(dateToUtc(shiftByWholeDays(original, dropped))).toBe("2026-11-18 08:00:00");
        expect(dateToUtc(shiftByWholeDays(original, new Date(original.getTime() - 0.4 * DAY_MS)))).toBe(
            "2026-11-16 08:00:00"
        );
    });

    test("grouped by assignee, each person's tasks form a tree and a shared task is in both", () => {
        const model = buildGanttModel(makePayload(), { groupBy: "user", facets: [], columns: ["deadline"] });
        const byId = Object.fromEntries(model.groups.map((group) => [group.id, group]));
        expect(model.groups.map((group) => group.id)).toEqual([
            "user_7",
            "ut_7_1",
            "ut_7_2",
            "user_8",
            "ut_8_2",
            "ut_8_3",
            "user_unassigned",
            "ut_0_4",
            "unscheduled",
            "us_6",
        ]);
        expect(byId.user_7.nestedGroups).toEqual(["ut_7_1", "ut_7_2"]);
        expect(byId.user_8.nestedGroups).toEqual(["ut_8_2"]);
        expect(byId.ut_8_2.nestedGroups).toEqual(["ut_8_3"]);
        expect(byId.ut_8_3.label.isNestedLeaf).toBe(true);
        expect(byId.ut_8_2.label.isNestedLeaf).toBe(false);
        expect(byId.unscheduled.showNested).toBe(false);
        expect(byId.ut_7_1.label.cells).toEqual([
            {
                key: "deadline",
                text: "Nov 17",
                title: "Deadline: Nov 17",
                width: 90,
                isNumeric: false,
                isOverdue: true,
            },
        ]);
        expect(model.edges).toEqual([{ pred: 1, succ: 2, from: "ut_7_1", to: "ut_7_2" }]);
        const milestone = model.items.find((item) => item.id === "ut_0_4");
        expect(milestone.type).toBe("point");
        expect(milestone.view.kind).toBe("milestone");
        const design = model.items.find((item) => item.id === "ut_7_1");
        expect(design.className).toInclude("gantt-overdue");
        expect(design.view).toEqual({ kind: "bar", progress: 40, showProgress: true, canLink: true });
    });

    test("grouped by task or stage, sub-tasks nest under their parent", () => {
        const byTask = buildGanttModel(makePayload({ unscheduled: [] }), { groupBy: "task", facets: [], columns: [] });
        expect(byTask.groups.map((group) => group.id)).toEqual(["t_1", "t_2", "t_3", "t_4"]);
        expect(byTask.groups[1].nestedGroups).toEqual(["t_3"]);
        expect(byTask.edges).toEqual([{ pred: 1, succ: 2, from: "t_1", to: "t_2" }]);
        const byStage = buildGanttModel(makePayload({ unscheduled: [] }), { groupBy: "stage", facets: [], columns: [] });
        expect(byStage.groups.filter((group) => group.label.kind === "stage").map((group) => group.label)).toEqual([
            { kind: "stage", name: "In Progress", count: 3 },
            { kind: "stage", name: "Review", count: 1 },
        ]);
        const review = byStage.groups.find((group) => group.id === "t_3");
        expect(review.label.outsideParentLabel).toBe("PROJ-2");
    });

    test("a deadline-only task is a point on its local deadline day", () => {
        mockTimeZone(1);
        const task = makeTask({
            id: 9,
            name: "Report",
            is_deadline_only: true,
            date_deadline: "2026-11-20",
            planned_date_start: "2026-11-20 00:00:00",
            planned_date_end: "2026-11-20 00:00:00",
        });
        const model = buildGanttModel(makePayload({ tasks: [task], unscheduled: [] }), { groupBy: "task", facets: [], columns: [] });
        expect(model.items[0].type).toBe("point");
        expect(DateTime.fromJSDate(model.items[0].start).toFormat("yyyy-MM-dd HH:mm")).toBe("2026-11-20 00:00");
        expect(model.items[0].editable.updateTime).toBe(false);
    });

    test("the baseline is a thin bar under each task that has one", () => {
        const tasks = makeTasks();
        tasks[0].baseline_date_start = "2026-11-15 08:00:00";
        tasks[0].baseline_date_end = "2026-11-17 17:00:00";
        const payload = makePayload({ tasks, unscheduled: [] });
        const hidden = buildGanttModel(payload, { groupBy: "task", facets: [], columns: [] });
        expect(hidden.items.filter((item) => item.className === "gantt-baseline")).toHaveLength(0);
        const shown = buildGanttModel(payload, { groupBy: "task", facets: [], columns: [], showBaseline: true });
        const baselines = shown.items.filter((item) => item.className === "gantt-baseline");
        expect(baselines.map((item) => [item.id, item.group])).toEqual([["bl_t_1", "t_1"]]);
        expect(baselines[0].start.getTime()).toBe(Date.UTC(2026, 10, 15, 8));
    });
});

describe.tags("desktop");
describe("GanttView", () => {
    before(() => visTimeline.load());

    beforeEach(() => {
        mockDate("2026-11-18 10:00:00", 1);
    });

    test("loads the project's tasks and lays out each person's rows", async () => {
        const loads = [];
        serveGanttData(loads);
        await mountGantt();
        expect(loads).toEqual([{ project_ids: [1], show_completed: false, include_stage_history: false }]);
        expect(labelTexts()).toEqual([
            "Alice",
            "↳ PROJ-1 Design",
            "↳ PROJ-2 Build",
            "Bob",
            "PROJ-2 Build",
            "↳ PROJ-3 Build API",
            "Unassigned",
            "↳ PROJ-4 Launch",
            "Unscheduled (1)",
        ]);
        expect(".o_gantt_timeline .vis-item[data-id='ut_7_1'] .gantt-progress-label").toHaveText("40%");
        expect(".o_gantt_timeline .vis-item[data-id='ut_0_4'] .gantt-milestone-diamond").toHaveCount(1);
        expect(".o_gantt_timeline .vis-item[data-id='ut_7_1']").toHaveClass("gantt-overdue");
        expect(".o_gantt_no_dates").toHaveText("1 task without dates");
        expect(queryAllTexts(".o_gantt_legend_stage")).toEqual(["In Progress", "Review"]);
        expect(".o_gantt_arrows_svg .o_gantt_dep_hit").toHaveCount(1);
        expect(".o_gantt_save_baseline").toHaveCount(1);
    });

    test("bars and tooltips keep the chart's look over the stylesheet vis-timeline adds after the bundle", async () => {
        serveGanttData([]);
        await mountGantt();
        const bar = queryOne(".o_gantt_timeline .vis-item[data-id='ut_8_3']");
        expect(bar).not.toHaveClass("gantt-overdue");
        expect(bar).not.toHaveClass("gantt-overlap");
        expect(getComputedStyle(bar).borderTopStyle).toBe("none");
        const overdue = queryOne(".o_gantt_timeline .vis-item[data-id='ut_7_1']");
        expect(getComputedStyle(overdue).borderTopWidth).toBe("2px");

        await hover(bar);
        const tooltip = queryOne(".o_gantt_view .vis-tooltip");
        expect(getComputedStyle(tooltip).fontFamily).toBe(getComputedStyle(queryOne(".o_gantt_view")).fontFamily);
        expect(getComputedStyle(tooltip).borderTopStyle).toBe("none");
    });

    test("every CSS variable the stylesheet names is one the web client defines", () => {
        const names = new Set();
        for (const sheet of document.styleSheets) {
            for (const rule of sheet.cssRules) {
                if (rule.selectorText?.includes(".o_gantt")) {
                    for (const [, name] of rule.cssText.matchAll(/var\((--[\w-]+)/g)) {
                        names.add(name);
                    }
                }
            }
        }
        const rootStyle = getComputedStyle(document.documentElement);
        expect(names.size).toBeGreaterThan(5);
        expect([...names].filter((name) => !rootStyle.getPropertyValue(name))).toEqual([]);
    });

    test("the rules of vis-timeline are in the page once, injected by its script", () => {
        const majorGridRules = [...document.styleSheets].flatMap((sheet) =>
            [...sheet.cssRules].filter((rule) => rule.selectorText === ".vis-time-axis .vis-grid.vis-major")
        );
        expect(majorGridRules).toHaveLength(1);
        expect(majorGridRules[0].parentStyleSheet.ownerNode.tagName).toBe("STYLE");
    });

    test("without a project nothing is loaded", async () => {
        const loads = [];
        serveGanttData(loads);
        await mountGantt({ projectId: false, projects: PROJECTS });
        expect(loads).toEqual([]);
        expect(".o_gantt_empty_state").toHaveText("Add a project to see its tasks on the Gantt chart.");
        expect(".o_gantt_timeline .vis-timeline").toHaveCount(0);
    });

    test("a load the server refuses shows its message", async () => {
        onRpc("project.project", "get_gantt_chart_data", () => {
            throw makeServerError({ message: "No access to this project." });
        });
        await mountGantt();
        expect(".o_gantt_error p:first").toHaveText("The Gantt chart could not be loaded.");
        expect(".o_gantt_error p:last").toHaveText("No access to this project.");
    });

    test("projects are added and removed from the picker", async () => {
        const loads = [];
        serveGanttData(loads, () => makePayload());
        await mountGantt();
        await contains(".o_gantt_project_add .o_select_menu_toggler").click();
        await contains(".o_select_menu_item:contains(Mobile App)").click();
        await animationFrame();
        expect(loads.at(-1).project_ids).toEqual([1, 2]);
        expect(queryAllTexts(".o_gantt_project_picker .o_tag")).toEqual(["PROJ - Website", "Mobile App"]);
        await click(".o_gantt_project_picker .o_tag:first .o_delete");
        await animationFrame();
        expect(loads.at(-1).project_ids).toEqual([2]);
        await click(".o_gantt_project_picker .o_tag .o_delete");
        await animationFrame();
        expect(loads).toHaveLength(3);
        expect(".o_gantt_empty_state").toHaveCount(1);
    });

    test("show closed reloads with the closed tasks", async () => {
        const loads = [];
        serveGanttData(loads, (kwargs) => {
            const payload = makePayload();
            if (kwargs.show_completed) {
                payload.tasks.push(CLOSED_TASK);
            }
            return payload;
        });
        await mountGantt();
        expect(labelTexts()).not.toInclude("↳ PROJ-5 Kick-off");
        await contains(".o_gantt_show_completed").click();
        await animationFrame();
        await animationFrame();
        expect(loads.at(-1)).toEqual({ project_ids: [1], show_completed: true, include_stage_history: false });
        expect(".o_gantt_show_completed").toHaveClass("active");
        expect(labelTexts()).toInclude("↳ PROJ-5 Kick-off");
        expect(queryAllTexts(".o_gantt_legend_stage")).toEqual(["In Progress", "Review", "Done"]);
    });

    test("grouping by task or stage rebuilds the rows without a reload", async () => {
        const loads = [];
        serveGanttData(loads);
        await mountGantt();
        await contains(".o_gantt_groupings [data-group='stage']").click();
        await animationFrame();
        expect(labelTexts().slice(0, 1)).toEqual(["In Progress (3)"]);
        expect(labelTexts()).toInclude("Review (1)");
        await contains(".o_gantt_groupings [data-group='task']").click();
        await animationFrame();
        expect(labelTexts().slice(0, 4)).toEqual([
            "PROJ-1 Design",
            "PROJ-2 Build",
            "↳ PROJ-3 Build API",
            "PROJ-4 Launch",
        ]);
        expect(loads).toHaveLength(1);
    });

    test("a column toggled is saved on the user and shown beside the names", async () => {
        const writes = [];
        serveGanttData([]);
        onRpc("res.users", "write", ({ args }) => {
            writes.push(args);
            return true;
        });
        await mountGantt();
        expect(queryAll(".o_gantt_timeline .o_gantt_cell[data-column='planned_hours']")).toHaveLength(0);
        await contains(".o_gantt_columns_toggle").click();
        expect(queryAllTexts(".o_gantt_column_option")).toEqual(["Assignees", "Deadline", "Estimate", "Spent hours", "Stage"]);
        // grouped by person, the assignees column would repeat the group
        expect(".o_gantt_column_option:contains(Assignees) input").toHaveAttribute("disabled");
        await contains(".o_gantt_column_option:contains(Estimate)").click();
        await animationFrame();
        expect(writes).toEqual([[[serverState.userId], { gantt_columns: "deadline,planned_hours" }]]);
        expect(".o_gantt_column_option:contains(Estimate) input").toBeChecked();
        expect(".o_gantt_timeline .vis-label .o_gantt_cell[data-column='planned_hours']").toHaveCount(5);
        await contains(".o_gantt_column_option:contains(Assignees)").click();
        expect(writes).toHaveLength(1);
    });

    test("search facets narrow the chart and go-to-task selects the bar", async () => {
        serveGanttData([]);
        await mountGantt();
        await click(".o_gantt_search_input");
        await animationFrame();
        expect(queryAllTexts(".o_gantt_search_field")).toEqual(["Assignee", "Task", "Key", "Stage", "Type"]);
        await click(".o_gantt_search_field[data-field='user_names']");
        await animationFrame();
        expect(queryAllTexts(".o_gantt_search_value")).toEqual(["Alice", "Bob"]);
        await click(".o_gantt_search_value:contains(Bob)");
        await animationFrame();
        expect(".o_gantt_search .o_tag").toHaveText("Assignee: Bob");
        expect(labelTexts()).toEqual(["Alice", "↳ PROJ-2 Build", "Bob", "PROJ-2 Build", "↳ PROJ-3 Build API"]);
        await click(".o_gantt_search_input");
        await press("Backspace");
        await animationFrame();
        expect(".o_gantt_search .o_tag").toHaveCount(0);
        expect(labelTexts()).toHaveLength(9);

        await click(".o_gantt_search_input");
        await edit("proj-3");
        await animationFrame();
        expect(queryAllTexts(".o_gantt_search_task")).toEqual(["PROJ-3 Build API"]);
        await click(".o_gantt_search_task");
        await animationFrame();
        expect(".o_gantt_timeline .vis-item.vis-selected").toHaveAttribute("data-id", "ut_8_3");
        expect(".o_gantt_timeline .vis-label.o_gantt_found").toHaveCount(1);
        await advanceTime(2500);
        await animationFrame();
        expect(".o_gantt_timeline .vis-label.o_gantt_found").toHaveCount(0);
    });

    test("dragging a bar's handle onto another bar adds the dependency", async () => {
        const calls = [];
        serveGanttData([]);
        onRpc("project.task", "gantt_add_dependency", ({ args }) => {
            calls.push(args);
            return true;
        });
        await mountGantt();
        await pointerDown(".o_gantt_timeline .vis-item[data-id='ut_7_1'] .o_gantt_dep_handle");
        await animationFrame();
        expect(".o_gantt_link_overlay line").toHaveCount(1);
        await pointerUp(".o_gantt_timeline .vis-item[data-id='ut_8_3']");
        await animationFrame();
        expect(calls).toEqual([[[3], 1]]);
        expect(".o_gantt_link_overlay").toHaveCount(0);
    });

    test("a dependency's lag is saved and the dependency removed from its arrow", async () => {
        const calls = [];
        const loads = [];
        serveGanttData(loads);
        onRpc("project.task", ["gantt_set_dependency_lag", "gantt_remove_dependency"], ({ method, args }) => {
            calls.push([method, args]);
            return true;
        });
        await mountGantt();
        await click(".o_gantt_arrows_svg .o_gantt_dep_hit");
        await animationFrame();
        expect(".o_gantt_dep_popover .fw-bold").toHaveText("PROJ-1 Design → PROJ-2 Build");
        expect(".o_gantt_dep_lag_input").toHaveValue(0);
        // Hoot types key by key and a number input drops a lone minus: set the value as a paste would
        const lagInput = queryOne(".o_gantt_dep_lag_input");
        lagInput.value = "-1";
        await manuallyDispatchProgrammaticEvent(lagInput, "input");
        await contains(".o_gantt_dep_save").click();
        expect(".o_notification_content").toHaveText(
            "Dependencies. The lag must be a whole number of days, zero or more."
        );
        expect(calls).toEqual([]);
        await contains(".o_gantt_dep_lag_input").edit("2", { confirm: false });
        await contains(".o_gantt_dep_save").click();
        await animationFrame();
        expect(calls).toEqual([["gantt_set_dependency_lag", [[2], 1, 2]]]);
        expect(".o_gantt_dep_popover").toHaveCount(0);
        expect(loads).toHaveLength(2);

        await click(".o_gantt_arrows_svg .o_gantt_dep_hit");
        await animationFrame();
        await contains(".o_gantt_dep_remove").click();
        await animationFrame();
        expect(calls.at(-1)).toEqual(["gantt_remove_dependency", [[2], 1]]);
        expect(loads).toHaveLength(3);
    });

    test("a manager saves the baseline after confirming and sees it", async () => {
        let saved = false;
        const calls = [];
        serveGanttData([], () => {
            const payload = makePayload();
            if (saved) {
                for (const task of payload.tasks) {
                    task.baseline_date_start = task.planned_date_start;
                    task.baseline_date_end = task.planned_date_end;
                }
            }
            return payload;
        });
        onRpc("project.project", "save_gantt_baselines", ({ args }) => {
            calls.push(args);
            saved = true;
            return true;
        });
        await mountGantt();
        await contains(".o_gantt_save_baseline").click();
        expect(".modal-body").toHaveText("Save the current plan as the project baseline? It replaces the current baseline.");
        await contains(".modal-footer .btn-primary").click();
        await animationFrame();
        await animationFrame();
        expect(calls).toEqual([[[1]]]);
        expect(".o_notification_content").toHaveText("Baseline saved.");
        expect(".o_gantt_show_baseline").toHaveClass("active");
        expect(".o_gantt_timeline .vis-item.gantt-baseline").not.toHaveCount(0);
    });

    test("showing a baseline that was never saved warns", async () => {
        serveGanttData([], () => makePayload({ is_project_manager: false }));
        await mountGantt();
        expect(".o_gantt_save_baseline").toHaveCount(0);
        await contains(".o_gantt_show_baseline").click();
        expect(".o_notification_content").toHaveText(
            "Baseline. None of the tasks shown has a baseline yet. A project manager can press Save Baseline to store the current plan."
        );
    });

    test("the Excel export downloads the file the server builds", async () => {
        const downloads = [];
        serveGanttData([]);
        onRpc("project.project", "export_gantt_excel", ({ args }) => {
            expect(args).toEqual([[1]]);
            return { data: btoa("xlsx bytes"), filename: "Website - Gantt.xlsx" };
        });
        patchWithCleanup(downloadFile, {
            _download: (data, filename, mimetype) => downloads.push({ data, filename, mimetype }),
        });
        await mountGantt();
        await contains(".o_gantt_export_excel").click();
        expect(downloads).toHaveLength(1);
        expect(downloads[0].filename).toBe("Website - Gantt.xlsx");
        expect(downloads[0].mimetype).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
        expect(await readBlob(downloads[0].data)).toBe("xlsx bytes");
    });

    test("an export the server cannot build warns instead of downloading", async () => {
        const downloads = [];
        serveGanttData([]);
        onRpc("project.project", "export_gantt_excel", () => ({ error: "No task has planned dates." }));
        patchWithCleanup(downloadFile, { _download: () => downloads.push(true) });
        await mountGantt();
        await contains(".o_gantt_export_excel").click();
        expect(".o_notification_content").toHaveText("Export. No task has planned dates.");
        expect(downloads).toEqual([]);
    });

    test("a moved bar is saved as UTC datetimes kept to whole days", async () => {
        const writes = [];
        const loads = [];
        serveGanttData(loads);
        onRpc("project.task", "write", ({ args }) => {
            writes.push(args);
            return true;
        });
        const gantt = await mountGantt();
        const bar = gantt.itemsDataSet.get("ut_7_1");
        const dropped = { ...bar, start: new Date(bar.start.getTime() + 2.2 * DAY_MS), end: new Date(bar.end.getTime() + 2.2 * DAY_MS) };
        const answers = [];
        await gantt.onItemMove(dropped, (item) => answers.push(item && item.id));
        expect(writes).toEqual([
            [[1], { planned_date_start: "2026-11-18 08:00:00", planned_date_end: "2026-11-20 17:00:00" }],
        ]);
        expect(answers).toEqual(["ut_7_1"]);
        expect(loads).toHaveLength(2);
    });

    test("a move the server refuses puts the bar back and shows why", async () => {
        serveGanttData([]);
        onRpc("project.task", "write", () => {
            throw makeServerError({ message: "This task cannot be planned before its predecessor ends." });
        });
        const gantt = await mountGantt();
        const { left, drop } = await dragBarByDays(gantt, "ut_7_2", -3);
        expect(barLeft("ut_7_2")).toBeLessThan(left - 10);
        await drop();
        await animationFrame();
        expect(".o_notification_content").toHaveText("This task cannot be planned before its predecessor ends.");
        expect(".o_notification_bar").toHaveClass("bg-danger");
        expect(barLeft("ut_7_2")).toBeCloseTo(left, { margin: 1 });
    });

    for (const [direction, days, plannedDay] of [
        ["later", 2, "2026-11-29"],
        ["earlier", -3, "2026-11-24"],
    ]) {
        test(`a milestone dragged ${direction} keeps the hours between its start and its end`, async () => {
            const writes = [];
            serveGanttData([], () => {
                const payload = makePayload();
                payload.tasks[3].planned_date_end = "2026-11-27 17:00:00";
                return payload;
            });
            onRpc("project.task", "write", ({ args }) => {
                writes.push(args);
                return true;
            });
            const gantt = await mountGantt();
            const { drop } = await dragBarByDays(gantt, "ut_0_4", days);
            await drop();
            await animationFrame();
            expect(writes).toEqual([
                [[4], { planned_date_start: `${plannedDay} 08:00:00`, planned_date_end: `${plannedDay} 17:00:00` }],
            ]);
        });
    }

    for (const side of ["start", "end"]) {
        test(`a bar with its ${side} date alone moves that date and makes up no other`, async () => {
            const writes = [];
            serveGanttData([], () => {
                const payload = makePayload();
                withOneDate(payload.tasks[0], side);
                return payload;
            });
            onRpc("project.task", "write", ({ args }) => {
                writes.push(args);
                return true;
            });
            const gantt = await mountGantt();
            const bar = gantt.itemsDataSet.get("ut_7_1");
            const shift = 2.2 * DAY_MS;
            const dropped = { ...bar, start: new Date(bar.start.getTime() + shift), end: new Date(bar.end.getTime() + shift) };
            await gantt.onItemMove(dropped, () => {});
            expect(writes).toEqual([
                [[1], side === "start" ? { planned_date_start: "2026-11-18 08:00:00" } : { planned_date_end: "2026-11-20 17:00:00" }],
            ]);
        });

        test(`a milestone with its ${side} date alone moves that date and makes up no other`, async () => {
            const writes = [];
            serveGanttData([], () => {
                const payload = makePayload();
                withOneDate(payload.tasks[3], side);
                return payload;
            });
            onRpc("project.task", "write", ({ args }) => {
                writes.push(args);
                return true;
            });
            const gantt = await mountGantt();
            const { drop } = await dragBarByDays(gantt, "ut_0_4", 2);
            await drop();
            await animationFrame();
            const movedDate = side === "start" ? "planned_date_start" : "planned_date_end";
            expect(writes).toEqual([[[4], { [movedDate]: "2026-11-29 08:00:00" }]]);
        });
    }

    for (const [side, edge, days, expectedVals] of [
        ["start", "right", 3, { planned_date_start: "2026-11-16 08:00:00", planned_date_end: "2026-11-20 08:00:00" }],
        ["start", "left", -1, { planned_date_start: "2026-11-15 08:00:00" }],
        ["end", "left", -2, { planned_date_start: "2026-11-15 17:00:00", planned_date_end: "2026-11-18 17:00:00" }],
        ["end", "right", 2, { planned_date_end: "2026-11-20 17:00:00" }],
    ]) {
        const resized = Object.keys(expectedVals).length === 2 ? "both dates" : "that date alone";
        test(`resizing the ${edge} edge of a bar with its ${side} date alone saves ${resized}`, async () => {
            const writes = [];
            serveGanttData([], () => {
                const payload = makePayload();
                withOneDate(payload.tasks[0], side);
                return payload;
            });
            onRpc("project.task", "write", ({ args }) => {
                writes.push(args);
                return true;
            });
            const gantt = await mountGantt();
            const { drop } = await dragBarByDays(gantt, "ut_7_1", days, { edge });
            await drop();
            await animationFrame();
            expect(writes).toEqual([[[1], expectedVals]]);
        });
    }

    test("the open icon of a parent row and an unscheduled name open the task form", async () => {
        const actions = [];
        serveGanttData([]);
        mockService("action", { doAction: (action) => actions.push(action) });
        await mountGantt();
        const taskForm = (resId) => ({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: resId,
            views: [[false, "form"]],
            target: "current",
        });
        // a press that reached vis-timeline would fold the row's sub-tasks away too
        await click(".o_gantt_timeline .vis-label.vis-nesting-group .o_gantt_open_task[data-task-id='2']");
        await advanceTime(10);
        await animationFrame();
        expect(actions).toEqual([taskForm(2)]);
        expect(labelTexts()).toInclude("↳ PROJ-3 Build API");

        expect(".o_gantt_unscheduled_name").toHaveCount(0);
        await click(".o_gantt_timeline .vis-label.vis-nesting-group:contains(Unscheduled)");
        await advanceTime(10);
        await animationFrame();
        await click(".o_gantt_timeline .vis-label .o_gantt_unscheduled_name[data-task-id='6']");
        expect(actions).toEqual([taskForm(2), taskForm(6)]);

        await click(".o_gantt_no_dates");
        expect(actions[2].domain).toEqual([["id", "in", [6]]]);
    });

    test("the scale buttons change the time axis", async () => {
        serveGanttData([]);
        const gantt = await mountGantt();
        expect(".o_gantt_scales [data-scale='week']").toHaveClass("active");
        await contains(".o_gantt_scales [data-scale='month']").click();
        expect(".o_gantt_scales [data-scale='month']").toHaveClass("active");
        expect(gantt.timeline.timeAxis.options.timeAxis.scale).toBe("month");
    });

    test("a reload the dashboard asks for keeps the projects, facets, scale and window in place", async () => {
        const loads = [];
        serveGanttData(loads);
        const mountedGantts = [];
        patchWithCleanup(GanttView.prototype, {
            setup() {
                super.setup();
                mountedGantts.push(this);
            },
        });
        class Dashboard extends Component {
            static components = { GanttView };
            static template = xml`
                <GanttView projectId="1" projects="this.projects" reloadCount="this.state.reloadCount"/>`;

            setup() {
                this.projects = PROJECTS;
                this.state = proxy({ reloadCount: 0 });
            }
        }
        const dashboard = await mountWithCleanup(Dashboard);
        await waitFor(".o_gantt_timeline .vis-timeline");
        await animationFrame();
        await contains(".o_gantt_project_add .o_select_menu_toggler").click();
        await contains(".o_select_menu_item:contains(Mobile App)").click();
        await animationFrame();
        await click(".o_gantt_search_input");
        await animationFrame();
        await click(".o_gantt_search_field[data-field='user_names']");
        await animationFrame();
        await click(".o_gantt_search_value:contains(Bob)");
        await animationFrame();
        await contains(".o_gantt_scales [data-scale='month']").click();
        const [gantt] = mountedGantts;
        gantt.timeline.setWindow(localDate(2026, 9, 5), localDate(2026, 9, 31), { animation: false });
        const labels = labelTexts();
        expect(loads).toHaveLength(2);

        dashboard.state.reloadCount++;
        await animationFrame();
        await animationFrame();
        expect(loads).toHaveLength(3);
        expect(loads.at(-1)).toEqual({ project_ids: [1, 2], show_completed: false, include_stage_history: false });
        expect(mountedGantts).toEqual([gantt]);
        expect(queryAllTexts(".o_gantt_project_picker .o_tag")).toEqual(["PROJ - Website", "Mobile App"]);
        expect(".o_gantt_search .o_tag").toHaveText("Assignee: Bob");
        expect(labelTexts()).toEqual(labels);
        expect(".o_gantt_scales [data-scale='month']").toHaveClass("active");
        expect(gantt.timeline.timeAxis.options.timeAxis.scale).toBe("month");
        const { start, end } = gantt.timeline.getWindow();
        expect([start.getTime(), end.getTime()]).toEqual([
            localDate(2026, 9, 5).getTime(),
            localDate(2026, 9, 31).getTime(),
        ]);
    });

    test("the settings the dashboard keeps bring the chart back as it was left", async () => {
        const loads = [];
        serveGanttData(loads, (kwargs) => {
            const payload = makePayload();
            if (kwargs.show_completed) {
                payload.tasks.push(CLOSED_TASK);
            }
            return payload;
        });
        const dashboard = await mountWithCleanup(GanttKeeper);
        await waitFor(".o_gantt_timeline .vis-timeline");
        await animationFrame();
        await contains(".o_gantt_project_add .o_select_menu_toggler").click();
        await contains(".o_select_menu_item:contains(Mobile App)").click();
        await animationFrame();
        await contains(".o_gantt_show_completed").click();
        await animationFrame();
        await contains(".o_gantt_groupings [data-group='stage']").click();
        await animationFrame();
        await click(".o_gantt_search_input");
        await animationFrame();
        await click(".o_gantt_search_field[data-field='user_names']");
        await animationFrame();
        await click(".o_gantt_search_value:contains(Bob)");
        await animationFrame();
        await contains(".o_gantt_scales [data-scale='month']").click();
        await animationFrame();
        const labels = labelTexts();

        dashboard.state.isShown = false;
        await animationFrame();
        expect(".o_gantt_view").toHaveCount(0);
        dashboard.state.isShown = true;
        await waitFor(".o_gantt_timeline .vis-timeline");
        await animationFrame();
        await animationFrame();
        expect(loads.at(-1)).toEqual({ project_ids: [1, 2], show_completed: true, include_stage_history: false });
        expect(queryAllTexts(".o_gantt_project_picker .o_tag")).toEqual(["PROJ - Website", "Mobile App"]);
        expect(".o_gantt_show_completed").toHaveClass("active");
        expect(".o_gantt_groupings [data-group='stage']").toHaveClass("active");
        expect(".o_gantt_search .o_tag").toHaveText("Assignee: Bob");
        expect(".o_gantt_scales [data-scale='month']").toHaveClass("active");
        expect(labelTexts()).toEqual(labels);
    });

    test("the window a chart was left at is the one it comes back to", async () => {
        serveGanttData([]);
        const mountedGantts = [];
        patchWithCleanup(GanttView.prototype, {
            setup() {
                super.setup();
                mountedGantts.push(this);
            },
        });
        const dashboard = await mountWithCleanup(GanttKeeper);
        await waitFor(".o_gantt_timeline .vis-timeline");
        await animationFrame();
        mountedGantts[0].timeline.setWindow(localDate(2026, 9, 5), localDate(2026, 9, 31), { animation: false });
        dashboard.state.isShown = false;
        await animationFrame();
        dashboard.state.isShown = true;
        await waitFor(".o_gantt_timeline .vis-timeline");
        await animationFrame();
        expect(mountedGantts).toHaveLength(2);
        const { start, end } = mountedGantts[1].timeline.getWindow();
        expect([start.getTime(), end.getTime()]).toEqual([
            localDate(2026, 9, 5).getTime(),
            localDate(2026, 9, 31).getTime(),
        ]);
    });

    test("a chart that is gone takes its stage legend and go-to-task list with it", async () => {
        let isRefused = false;
        onRpc("project.project", "get_gantt_chart_data", () => {
            if (isRefused) {
                throw makeServerError({ message: "No access to this project." });
            }
            return makePayload();
        });
        const goToTasks = async () => {
            await click(".o_gantt_search_input");
            await animationFrame();
            return queryAllTexts(".o_gantt_search_task");
        };
        await mountGantt();
        await click(".o_gantt_search_input");
        await edit("proj-3");
        await animationFrame();
        expect(queryAllTexts(".o_gantt_search_task")).toEqual(["PROJ-3 Build API"]);
        expect(queryAllTexts(".o_gantt_legend_stage")).toEqual(["In Progress", "Review"]);

        isRefused = true;
        await contains(".o_gantt_show_completed").click();
        await animationFrame();
        expect(".o_gantt_error p:last").toHaveText("No access to this project.");
        expect(".o_gantt_legend_stage").toHaveCount(0);
        expect(await goToTasks()).toEqual([]);

        isRefused = false;
        await contains(".o_gantt_show_completed").click();
        await animationFrame();
        expect(await goToTasks()).toEqual(["PROJ-3 Build API"]);
        await click(".o_gantt_project_picker .o_tag .o_delete");
        await animationFrame();
        expect(".o_gantt_empty_state").toHaveCount(1);
        expect(".o_gantt_legend_stage").toHaveCount(0);
        expect(await goToTasks()).toEqual([]);
    });

    test("a column toggled before the last one is saved keeps it, and the saves go in turn", async () => {
        const saves = [];
        const firstSave = Promise.withResolvers();
        serveGanttData([]);
        onRpc("res.users", "write", async ({ args }) => {
            saves.push(args[1].gantt_columns);
            if (saves.length === 1) {
                await firstSave.promise;
            }
            return true;
        });
        await mountGantt();
        await contains(".o_gantt_columns_toggle").click();
        await contains(".o_gantt_column_option:contains(Estimate)").click();
        await contains(".o_gantt_column_option:contains(Stage)").click();
        await animationFrame();
        expect(".o_gantt_column_option:contains(Estimate) input").toBeChecked();
        expect(".o_gantt_column_option:contains(Stage) input").toBeChecked();
        expect(saves).toEqual(["deadline,planned_hours"]);

        firstSave.resolve();
        await animationFrame();
        expect(saves).toEqual(["deadline,planned_hours", "deadline,planned_hours,stage"]);
        expect(".o_gantt_timeline .vis-label .o_gantt_cell[data-column='stage']").toHaveCount(5);
    });

    test("a reload answering while a column save is on its way keeps the columns chosen", async () => {
        let storedColumns = "deadline";
        const save = Promise.withResolvers();
        serveGanttData([], () => makePayload({ columns: storedColumns.split(",") }));
        onRpc("res.users", "write", async ({ args }) => {
            await save.promise;
            storedColumns = args[1].gantt_columns;
            return true;
        });
        const gantt = await mountGantt();
        await contains(".o_gantt_columns_toggle").click();
        await contains(".o_gantt_column_option:contains(Estimate)").click();
        await gantt.loadData({ keepWindow: true });
        await animationFrame();
        expect(gantt.state.columns).toEqual(["deadline", "planned_hours"]);
        expect(".o_gantt_timeline .vis-label .o_gantt_cell[data-column='planned_hours']").toHaveCount(5);

        save.resolve();
        await animationFrame();
        await gantt.loadData({ keepWindow: true });
        await animationFrame();
        expect(gantt.state.columns).toEqual(["deadline", "planned_hours"]);
    });

    test("a column save the server refuses puts the stored columns back and shows why", async () => {
        serveGanttData([]);
        onRpc("res.users", "write", () => {
            throw makeServerError({ message: "You cannot change these preferences." });
        });
        const gantt = await mountGantt();
        await contains(".o_gantt_columns_toggle").click();
        await contains(".o_gantt_column_option:contains(Estimate)").click();
        await animationFrame();
        expect(gantt.state.columns).toEqual(["deadline"]);
        expect(".o_gantt_timeline .vis-label .o_gantt_cell[data-column='planned_hours']").toHaveCount(0);
        expect(".o_notification_content").toHaveText("You cannot change these preferences.");
    });

    test("the columns cannot be toggled before the first load gives the stored ones", async () => {
        const firstLoad = Promise.withResolvers();
        const saves = [];
        serveGanttData([], () => firstLoad.promise.then(() => makePayload({ columns: ["deadline", "stage"] })));
        onRpc("res.users", "write", ({ args }) => {
            saves.push(args[1].gantt_columns);
            return true;
        });
        await mountWithCleanup(GanttView, { props: { projectId: 1, projects: PROJECTS } });
        expect(".o_gantt_columns_toggle").not.toBeEnabled();
        await click(".o_gantt_columns_toggle");
        await animationFrame();
        expect(".o_gantt_columns_menu").toHaveCount(0);

        firstLoad.resolve();
        await waitFor(".o_gantt_timeline .vis-timeline");
        await animationFrame();
        expect(".o_gantt_columns_toggle").toBeEnabled();
        await contains(".o_gantt_columns_toggle").click();
        await contains(".o_gantt_column_option:contains(Estimate)").click();
        await animationFrame();
        expect(saves).toEqual(["deadline,planned_hours,stage"]);
    });

    test("a chart gone before its first load ends draws nothing", async () => {
        // the ORM answers a destroyed component never: the script of vis-timeline is what may come late
        const visLoad = Promise.withResolvers();
        patchWithCleanup(visTimeline, { load: () => visLoad.promise });
        const loads = [];
        serveGanttData(loads);
        const renders = [];
        patchWithCleanup(GanttView.prototype, {
            renderTimeline(window) {
                renders.push(window);
                return super.renderTimeline(window);
            },
        });
        const dashboard = await mountWithCleanup(GanttKeeper);
        await animationFrame();
        expect(loads).toHaveLength(1);
        dashboard.state.isShown = false;
        await animationFrame();
        visLoad.resolve(window.vis);
        await animationFrame();
        expect(renders).toEqual([]);
    });

    test("a second press on a handle starts the link over, and nothing of the first is left", async () => {
        const calls = [];
        serveGanttData([]);
        onRpc("project.task", "gantt_add_dependency", ({ args }) => {
            calls.push(args);
            return true;
        });
        await mountGantt();
        await pointerDown(".o_gantt_timeline .vis-item[data-id='ut_7_1'] .o_gantt_dep_handle");
        await pointerDown(".o_gantt_timeline .vis-item[data-id='ut_8_3'] .o_gantt_dep_handle");
        await animationFrame();
        expect(".o_gantt_link_overlay line").toHaveCount(1);
        await pointerUp(".o_gantt_timeline .vis-item[data-id='ut_0_4']");
        await animationFrame();
        expect(calls).toEqual([[[4], 3]]);

        await hover(".o_gantt_timeline .vis-item[data-id='ut_7_2']");
        await animationFrame();
        expect(".o_gantt_link_overlay").toHaveCount(0);
        await pointerUp(".o_gantt_timeline .vis-item[data-id='ut_7_2']");
        await animationFrame();
        expect(calls).toHaveLength(1);
    });
});

describe.tags("mobile");
describe("GanttView on a phone", () => {
    before(() => visTimeline.load());

    test("the chart loads and the toolbar keeps icons with their labels for assistive tech", async () => {
        mockDate("2026-11-18 10:00:00", 1);
        serveGanttData([]);
        await mountGantt();
        expect(".o_gantt_timeline .vis-timeline").toHaveCount(1);
        expect(".o_gantt_export_excel").toHaveAttribute("aria-label", "Export Excel");
        expect(".o_gantt_export_excel span.d-none.d-md-inline").toHaveCount(1);
        expect(labelTexts()).toHaveLength(9);
        const chartWidth = queryOne(".o_gantt_timeline").offsetWidth;
        expect(queryOne(".o_gantt_timeline .vis-panel.vis-left").offsetWidth).toBe(sidebarMaxWidth(chartWidth, 0));
    });
});
