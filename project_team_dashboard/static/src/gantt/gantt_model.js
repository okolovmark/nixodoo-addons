import { _t } from "@web/core/l10n/translation";
import {
    deserializeDate,
    deserializeDateTime,
    serializeDateTime,
    toLocaleDateString,
    toLocaleDateTimeString,
} from "@web/core/l10n/dates";

const { DateTime, Info, Settings } = luxon;

export const DAY_MS = 86400000;
const SHIFT_BY_SCALE = { day: { days: 7 }, week: { months: 1 }, month: { months: 3 } };
const PAN_MARGIN = { months: 12 };
const TASK_MATCH_LIMIT = 8;
const STAGE_PALETTE = ["#8e7cc3", "#5dade2", "#2ecb71", "#f5b041", "#48c9b0", "#eb984e", "#af7ac5", "#5d6d7e"];
export const CLOSED_STAGE_COLOR = "#aab7b8";
const FALLBACK_COLORS = [CLOSED_STAGE_COLOR, "rgba(170,183,184,0.3)"];
const ELBOW_STUB = 10;
const LABEL_MIN_WIDTH = 140;

export const GANTT_COLUMNS = [
    {
        key: "assignees",
        label: _t("Assignees"),
        name: _t("Assignees"),
        width: 120,
        help: _t("Assignees: people the task is assigned to"),
    },
    {
        key: "deadline",
        label: _t("Deadline"),
        name: _t("Deadline"),
        width: 90,
        help: _t("Deadline: date the task is due, in red when overdue and the task is still open"),
    },
    {
        key: "planned_hours",
        label: _t("Estimate h"),
        name: _t("Estimate"),
        width: 84,
        numeric: true,
        help: _t("Estimate: the hours allocated to the task"),
    },
    {
        key: "spent_hours",
        label: _t("Spent h"),
        name: _t("Spent hours"),
        width: 72,
        numeric: true,
        help: _t("Spent hours: hours logged in timesheets"),
    },
    { key: "stage", label: _t("Stage"), name: _t("Stage"), width: 100, help: _t("Stage: current stage of the task") },
];

const GROUP_COLUMN = { user: "assignees", stage: "stage" };

export const SEARCH_FIELDS = [
    { field: "user_names", label: _t("Assignee"), icon: "person" },
    { field: "name", label: _t("Task"), icon: "task" },
    { field: "key", label: _t("Key"), icon: "key" },
    { field: "stage_name", label: _t("Stage"), icon: "view_kanban" },
    { field: "task_type", label: _t("Type"), icon: "label" },
];

export const ARROW_STYLES = {
    normal: { color: "#f39c12", width: "2" },
    violated: { color: "#e74c3c", width: "2" },
};

function addToDate(date, step, sign) {
    return DateTime.fromJSDate(date)
        .plus(step.months ? { months: sign * step.months } : { days: sign * step.days })
        .toJSDate();
}

export function shiftGanttWindow(start, end, scale, direction) {
    const step = SHIFT_BY_SCALE[scale] || SHIFT_BY_SCALE.week;
    return { start: addToDate(start, step, direction), end: addToDate(end, step, direction) };
}

export function ganttPanBounds(dataStart, dataEnd) {
    return { min: addToDate(dataStart, PAN_MARGIN, -1), max: addToDate(dataEnd, PAN_MARGIN, 1) };
}

/**
 * The moment constructor vis-timeline lays its axis out with: its weeks start on weekStart and are
 * numbered as `getLocalYearAndWeek` numbers them, by the ISO week of the nearest Monday, and it names
 * the months and weekdays in locale, as luxon does: vis bundles the moment locales of a few languages only.
 *
 * @param {Function} moment the one vis-timeline bundles
 * @param {number} weekStart the first day of the week, `localization.weekStart`: 1 is Monday, 7 Sunday
 * @param {string} [locale] the web client's luxon locale by default, the user's language
 */
export function weekStartMoment(moment, weekStart, locale = Settings.defaultLocale) {
    const localeName = `o_gantt_week_start_${weekStart}_${locale}`;
    if (!moment.locales().includes(localeName)) {
        const dow = weekStart % 7;
        const daysToMonday = weekStart > 1 && weekStart < 5 ? 1 - weekStart : (8 - weekStart) % 7;
        const globalLocale = moment.locale();
        // luxon lists the weekdays from Monday, moment from Sunday
        const fromSunday = (weekdays) => [weekdays[6], ...weekdays.slice(0, 6)];
        moment.defineLocale(localeName, {
            parentLocale: globalLocale,
            // doy is moment's 7 + dow - (the day of January that is always in week 1)
            week: { dow, doy: 3 + dow + daysToMonday },
            // moment takes the format names next to a day ("D MMMM"), the standalone ones elsewhere
            months: { format: Info.monthsFormat("long", { locale }), standalone: Info.months("long", { locale }) },
            monthsShort: { format: Info.monthsFormat("short", { locale }), standalone: Info.months("short", { locale }) },
            weekdays: fromSunday(Info.weekdays("long", { locale })),
            weekdaysShort: fromSunday(Info.weekdays("short", { locale })),
        });
        // defining a locale makes it the global one too
        moment.locale(globalLocale);
    }
    return (date) => moment(date).locale(localeName);
}

/** A stored UTC datetime ("YYYY-MM-DD HH:MM:SS") as the instant vis-timeline draws. */
export function utcToDate(value) {
    return deserializeDateTime(value).toJSDate();
}

export function dateToUtc(date) {
    return serializeDateTime(DateTime.fromJSDate(new Date(date)));
}

/** The local day of a stored UTC datetime, as the user reads it. */
export function localDayLabel(value) {
    return toLocaleDateString(deserializeDateTime(value));
}

function localDateTimeLabel(value) {
    return toLocaleDateTimeString(deserializeDateTime(value));
}

/** The whole days a drag moved an edge of a bar by, the nearest. */
export function wholeDaysBetween(original, moved) {
    return Math.round((new Date(moved) - new Date(original)) / DAY_MS);
}

/** A drag moves a bar by whole local days, so the stored time of day survives it. */
export function shiftByWholeDays(original, moved) {
    if (!original || !moved) {
        return moved;
    }
    return DateTime.fromJSDate(new Date(original)).plus({ days: wholeDaysBetween(original, moved) }).toJSDate();
}

function dayNumber(isoDate) {
    const [year, month, day] = isoDate.substring(0, 10).split("-").map(Number);
    return Date.UTC(year, month - 1, day) / DAY_MS;
}

function byPlannedStart(a, b) {
    return (a.planned_date_start || "").localeCompare(b.planned_date_start || "");
}

export function groupTasksByStage(tasks) {
    const byStage = new Map();
    for (const task of tasks) {
        const stageId = task.stage_id || 0;
        if (!byStage.has(stageId)) {
            byStage.set(stageId, {
                stageId,
                name: task.stage_name || _t("No Stage"),
                sequence: stageId ? task.stage_sequence || 0 : Infinity,
                tasks: [],
            });
        }
        byStage.get(stageId).tasks.push(task);
    }
    const stages = [...byStage.values()];
    stages.sort((a, b) => a.sequence - b.sequence || a.stageId - b.stageId);
    for (const stage of stages) {
        stage.tasks.sort(byPlannedStart);
    }
    return stages;
}

/**
 * Order one group's tasks as a tree, depth first. A task nests under its parent only when the
 * parent is in the same group; otherwise it is a root and `outsideParentId` names the parent.
 */
export function buildTaskTree(tasks) {
    const tasksById = new Map(tasks.map((task) => [task.id, task]));
    const childrenById = new Map();
    const roots = [];
    for (const task of tasks) {
        if (task.parent_id && task.parent_id !== task.id && tasksById.has(task.parent_id)) {
            if (!childrenById.has(task.parent_id)) {
                childrenById.set(task.parent_id, []);
            }
            childrenById.get(task.parent_id).push(task);
        } else {
            roots.push(task);
        }
    }
    const rows = [];
    const visited = new Set();
    const visit = (task, depth) => {
        if (visited.has(task.id)) {
            return;
        }
        visited.add(task.id);
        const children = (childrenById.get(task.id) || []).filter((child) => !visited.has(child.id));
        rows.push({
            task,
            depth,
            childIds: children.map((child) => child.id),
            outsideParentId: task.parent_id && !tasksById.has(task.parent_id) ? task.parent_id : false,
        });
        for (const child of children) {
            visit(child, depth + 1);
        }
    };
    roots.forEach((task) => visit(task, 0));
    // a parent cycle has no root: its tasks still get a row each
    tasks.forEach((task) => visit(task, 0));
    return rows;
}

export function isAncestorPair(taskMap, taskIdA, taskIdB) {
    const isAncestorOf = (ancestorId, taskId) => {
        const seen = new Set();
        let task = taskMap[taskId];
        while (task && task.parent_id && !seen.has(task.id)) {
            seen.add(task.id);
            if (task.parent_id === ancestorId) {
                return true;
            }
            task = taskMap[task.parent_id];
        }
        return false;
    };
    return isAncestorOf(taskIdA, taskIdB) || isAncestorOf(taskIdB, taskIdA);
}

/**
 * Ids of the bars that overlap another bar of the same person. A rolled-up parent always covers
 * its sub-tasks, so a pair where one task is an ancestor of the other never counts.
 * @param {Array<{id, taskId, start, end}>} bars
 */
export function findOverlappingBarIds(bars, taskMap) {
    const overlappingIds = new Set();
    let openBars = [];
    const datedBars = bars.filter((bar) => bar.end).sort((a, b) => new Date(a.start) - new Date(b.start));
    for (const bar of datedBars) {
        const barStart = new Date(bar.start);
        openBars = openBars.filter((openBar) => new Date(openBar.end) > barStart);
        for (const openBar of openBars) {
            if (!isAncestorPair(taskMap, openBar.taskId, bar.taskId)) {
                overlappingIds.add(openBar.id);
                overlappingIds.add(bar.id);
            }
        }
        openBars.push(bar);
    }
    return overlappingIds;
}

/** The stored datetime a point is drawn on: the planned end of a task that has no planned start, else the start. */
export function pointDateValue(task) {
    return !task.has_planned_date_start && task.has_planned_date_end ? task.planned_date_end : task.planned_date_start;
}

export function ganttBarRules(task, hasSubtaskRows) {
    const hasNoLength = !task.planned_date_end || task.planned_date_end === task.planned_date_start;
    return {
        isPoint: !!task.is_deadline_only || (task.task_type === "milestone" && (!hasSubtaskRows || hasNoLength)),
        canDrag: !task.is_deadline_only && !task.is_planned_from_subtasks && !!task.can_write,
    };
}

export function outsideParentOf(row, taskMap) {
    if (row.depth) {
        return null;
    }
    const parent = row.outsideParentId && taskMap[row.outsideParentId];
    if (parent) {
        return parent.key || parent.name;
    }
    return row.task.parent_label || null;
}

function lightColor(hex) {
    const value = parseInt(hex.slice(1), 16);
    return `rgba(${value >> 16},${(value >> 8) & 255},${value & 255},0.3)`;
}

export function assignStageColors(tasks) {
    let openIndex = 0;
    return groupTasksByStage(tasks).map((stage) => {
        const isClosed = stage.tasks.every((task) => task.is_closed);
        const solid = isClosed ? CLOSED_STAGE_COLOR : STAGE_PALETTE[openIndex++ % STAGE_PALETTE.length];
        return { stageId: stage.stageId, name: stage.name, solid, light: lightColor(solid) };
    });
}

/** The arrow from a bar's end at (x1, y1) to another's start at (x2, y2); right to left, time runs leftwards. */
export function elbowPath(x1, y1, x2, y2, isRtl = false) {
    const stub = isRtl ? -ELBOW_STUB : ELBOW_STUB;
    const exitX = x1 + stub;
    const entryX = x2 - stub;
    if (isRtl ? entryX <= exitX : entryX >= exitX) {
        return `M${x1},${y1} H${exitX} V${y2} H${x2}`;
    }
    const midY = (y1 + y2) / 2;
    return `M${x1},${y1} H${exitX} V${midY} H${entryX} V${y2} H${x2}`;
}

function parseUtcMs(value) {
    return deserializeDateTime(value).toMillis();
}

function formatUtcMs(ms) {
    return dateToUtc(new Date(ms));
}

export function buildStageHistoryRows(task, scope) {
    const idPrefix = `sh_${scope === undefined ? "" : `${scope}_`}${task.id}_`;
    const rows = (task.stage_history || []).map((visit, index) => {
        const startMs = parseUtcMs(visit.start);
        return {
            id: idPrefix + index,
            // a deleted stage has no id: 0 is the key the stage colours and the legend file it under
            stageId: visit.stage_id || 0,
            stageName: visit.stage_name,
            isClosed: visit.is_closed,
            start: visit.start,
            end: formatUtcMs(Math.max(parseUtcMs(visit.end), startMs + DAY_MS)),
            visitEnd: visit.end,
        };
    });
    const edges = rows.slice(1).map((row, index) => ({ from: rows[index].id, to: row.id }));
    return { rows, edges };
}

export function stageHistorySpan(task) {
    const visits = task.stage_history || [];
    if (!visits.length) {
        return null;
    }
    const startMs = parseUtcMs(visits[0].start);
    const endMs = Math.max(parseUtcMs(visits.at(-1).end), startMs + DAY_MS);
    return { start: visits[0].start, end: formatUtcMs(endMs) };
}

export function findGanttTasks(tasks, query) {
    const search = (query || "").trim().toLowerCase();
    if (!search) {
        return [];
    }
    const ranked = [];
    tasks.forEach((task, index) => {
        const key = (task.key || "").toLowerCase();
        const name = (task.name || "").toLowerCase();
        let rank;
        if (key === search) {
            rank = 0;
        } else if (key.startsWith(search)) {
            rank = 1;
        } else if (name.startsWith(search)) {
            rank = 2;
        } else if (key.includes(search) || name.includes(search)) {
            rank = 3;
        } else {
            return;
        }
        ranked.push({ task, rank, index });
    });
    ranked.sort((a, b) => a.rank - b.rank || a.index - b.index);
    return ranked.slice(0, TASK_MATCH_LIMIT).map((entry) => entry.task);
}

export function matchesFacets(task, facets) {
    return facets.every((facet) => {
        const value = (task[facet.field] || "").toLowerCase();
        const search = facet.value.toLowerCase();
        return facet.field === "key" || facet.field === "task_type" ? value === search : value.includes(search);
    });
}

/** The tasks matching every facet, with the parent and sub-tasks of each match kept for context. */
export function filterTasksByFacets(tasks, facets) {
    if (!facets.length) {
        return tasks;
    }
    const taskIds = new Set(tasks.map((task) => task.id));
    const shownIds = new Set();
    for (const task of tasks) {
        if (matchesFacets(task, facets)) {
            shownIds.add(task.id);
            if (task.parent_id && taskIds.has(task.parent_id)) {
                shownIds.add(task.parent_id);
            }
            for (const childId of task.child_ids || []) {
                if (taskIds.has(childId)) {
                    shownIds.add(childId);
                }
            }
        }
    }
    return tasks.filter((task) => shownIds.has(task.id));
}

/** The distinct values a facet on `field` can take, an assignee list split per person. */
export function searchValues(tasks, field) {
    const values = new Set();
    for (const task of tasks) {
        const rawValue = task[field] || "";
        const parts = field === "user_names" ? rawValue.split(",") : [rawValue];
        for (const part of parts) {
            if (part.trim()) {
                values.add(part.trim());
            }
        }
    }
    return [...values].sort((a, b) => a.localeCompare(b));
}

export function formatTaskType(value) {
    return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function analyzeDependencies(tasks) {
    const taskById = new Map(tasks.map((task) => [task.id, task]));
    const edges = {};
    for (const task of tasks) {
        const lags = task.depend_on_lags || {};
        for (const predecessorId of task.depend_on_ids || []) {
            const predecessor = taskById.get(predecessorId);
            if (!predecessor) {
                continue;
            }
            const lag = lags[predecessorId] || 0;
            const info = { lag, violated: false };
            if (predecessor.plan_date_end && task.plan_date_start) {
                info.violated = dayNumber(task.plan_date_start) < dayNumber(predecessor.plan_date_end) + lag;
            }
            edges[`${predecessorId}_${task.id}`] = info;
        }
    }
    return { edges };
}

export function activeColumns(columnKeys, groupBy) {
    const groupColumn = GROUP_COLUMN[groupBy];
    return GANTT_COLUMNS.filter((column) => column.key !== groupColumn && columnKeys.includes(column.key));
}

export function isGroupColumn(columnKey, groupBy) {
    return GROUP_COLUMN[groupBy] === columnKey;
}

/** The width of the attribute columns, or 0 when they would leave the task names too little room. */
export function shownColumnsWidth(columns, containerWidth) {
    const columnsWidth = columns.reduce((sum, column) => sum + column.width, 0);
    return Math.floor(containerWidth * 0.6) >= columnsWidth + LABEL_MIN_WIDTH ? columnsWidth : 0;
}

/**
 * The label sidebar's cap: a quarter of the chart, never less than the task names need (a quarter
 * of a phone shows little more than the keys), plus the shown columns, never above 60%.
 */
export function sidebarMaxWidth(containerWidth, columnsWidth) {
    const namesWidth = Math.max(Math.floor(containerWidth * 0.25), LABEL_MIN_WIDTH);
    return Math.min(namesWidth + columnsWidth, Math.floor(containerWidth * 0.6));
}

function formatHours(hours) {
    return hours ? hours.toFixed(1) : "";
}

function columnCells(task, columns, today) {
    return columns.map((column) => {
        let text = "";
        let isOverdue = false;
        if (column.key === "assignees") {
            text = task.user_names || "";
        } else if (column.key === "deadline" && task.date_deadline) {
            text = toLocaleDateString(deserializeDate(task.date_deadline));
            isOverdue = !task.is_closed && task.date_deadline < today;
        } else if (column.key === "planned_hours") {
            text = formatHours(task.planned_hours);
        } else if (column.key === "spent_hours") {
            text = formatHours(task.effective_hours);
        } else if (column.key === "stage") {
            text = task.stage_name || "";
        }
        return {
            key: column.key,
            text,
            title: text ? `${column.name}: ${text}` : "",
            width: column.width,
            isNumeric: !!column.numeric,
            isOverdue,
        };
    });
}

function taskTooltip(task, prefix, isPoint) {
    const lines = [prefix + (task.key ? `${task.key} - ${task.name}` : task.name)];
    if (task.user_names) {
        lines.push(_t("Assigned: %s", task.user_names));
    }
    lines.push(_t("Stage: %s", task.stage_name || ""));
    if (task.is_overdue && !task.is_closed) {
        lines.push(_t("Overdue"));
    }
    if (task.task_type === "milestone") {
        lines.push(_t("Type: Milestone"));
    }
    if (task.planned_hours > 0) {
        lines.push(
            _t("Progress: %(progress)s - %(remaining)sh remaining", {
                progress: `${Math.round(task.progress || 0)}%`,
                remaining: (task.remaining_hours || 0).toFixed(1),
            })
        );
    }
    if (task.is_deadline_only) {
        lines.push(_t("Deadline: %s (no planned dates)", toLocaleDateString(deserializeDate(task.date_deadline))));
    } else if (isPoint) {
        lines.push(localDayLabel(pointDateValue(task)));
    } else if (task.has_planned_date_start && task.has_planned_date_end) {
        lines.push(`${localDayLabel(task.planned_date_start)} → ${localDayLabel(task.planned_date_end)}`);
    } else if (task.has_planned_date_start) {
        lines.push(_t("Start: %s", localDayLabel(task.planned_date_start)));
    } else {
        lines.push(_t("End: %s", localDayLabel(task.planned_date_end)));
    }
    if (typeof task.baseline_shift_days === "number") {
        const shift = task.baseline_shift_days;
        lines.push(_t("%s d vs baseline", (shift > 0 ? "+" : "") + shift));
    }
    return lines;
}

/**
 * Shape the server's Gantt payload into vis-timeline groups and items, plus what the chart draws
 * around them. Labels and bar contents are descriptors the templates render: no HTML is built here.
 *
 * @param {Object} data payload of `project.project.get_gantt_chart_data`
 * @param {Object} options groupBy, facets, columns, showBaseline, showStageHistory, isMultiProject, isRtl
 */
export function buildGanttModel(data, options) {
    const allTasks = data.tasks || [];
    const facets = options.facets || [];
    const unscheduled = (data.unscheduled || []).filter((task) => matchesFacets(task, facets));
    const tasks = filterTasksByFacets(allTasks, facets);
    const model = {
        tasks,
        unscheduled,
        groups: [],
        items: [],
        edges: [],
        historyEdges: [],
        stageLegend: [],
        analysis: { edges: {} },
        canLink: allTasks.some((task) => task.can_write),
        isEmpty: !tasks.length && !unscheduled.length,
    };
    if (model.isEmpty) {
        return model;
    }
    const taskMap = Object.fromEntries(allTasks.map((task) => [task.id, task]));
    const columns = activeColumns(options.columns || [], options.groupBy);
    const today = data.today || "";
    const { groups, items } = model;
    let order = 0;

    const colorSourceTasks = [...allTasks];
    if (options.showStageHistory) {
        for (const task of allTasks) {
            for (const visit of task.stage_history || []) {
                colorSourceTasks.push({
                    stage_id: visit.stage_id,
                    stage_name: visit.stage_name,
                    stage_sequence: visit.stage_sequence,
                    is_closed: visit.is_closed,
                });
            }
        }
    }
    model.stageLegend = assignStageColors(colorSourceTasks);
    const stageColors = new Map(model.stageLegend.map((stage) => [stage.stageId, [stage.solid, stage.light]]));
    const colorsOf = (stageId) => stageColors.get(stageId || 0) || FALLBACK_COLORS;
    const prefixOf = (task) => (options.isMultiProject && task.project_name ? `${task.project_name} | ` : "");
    // right to left, a bar starts on its right: its progress fills from there
    const progressSide = options.isRtl ? "left" : "right";

    const taskItem = (task, rowId, prefix, hasSubtaskRows, userId) => {
        const barRules = ganttBarRules(task, hasSubtaskRows);
        const [solid, light] = colorsOf(task.stage_id);
        const isOverdue = task.is_overdue && !task.is_closed;
        const className = ["gantt-stage", isOverdue && "gantt-overdue", barRules.isPoint && "gantt-milestone"]
            .filter(Boolean)
            .join(" ");
        const item = {
            id: rowId,
            group: rowId,
            taskId: task.id,
            userId,
            isTaskBar: true,
            className,
            tooltip: taskTooltip(task, prefix, barRules.isPoint),
            editable: { updateTime: barRules.canDrag, updateGroup: false, remove: false },
        };
        if (barRules.isPoint) {
            return Object.assign(item, {
                type: "point",
                start: task.is_deadline_only
                    ? deserializeDate(task.date_deadline).toJSDate()
                    : utcToDate(pointDateValue(task)),
                view: { kind: "milestone", color: solid },
            });
        }
        const progress = Math.min(100, Math.max(0, Math.round(task.progress || 0)));
        return Object.assign(item, {
            type: "range",
            start: utcToDate(task.planned_date_start),
            end: utcToDate(task.planned_date_end),
            style: `background: linear-gradient(to ${progressSide}, ${solid} 0%, ${solid} ${progress}%, ${light} ${progress}%, ${light} 100%);`,
            view: { kind: "bar", progress, showProgress: task.planned_hours > 0, canLink: model.canLink },
        });
    };

    const rowItem = (task, rowId, prefix, hasSubtaskRows, userId) => {
        const item = taskItem(task, rowId, prefix, hasSubtaskRows, userId);
        const span = options.showStageHistory && stageHistorySpan(task);
        if (!span) {
            return item;
        }
        return Object.assign(item, {
            type: "range",
            start: utcToDate(span.start),
            end: utcToDate(span.end),
            className: "gantt-stage gantt-summary",
            style: `background: ${colorsOf(task.stage_id)[0]};`,
            editable: false,
            view: { kind: "plain" },
        });
    };

    const addStageHistory = (task, treeLevel, scope) => {
        const history = buildStageHistoryRows(task, scope);
        history.rows.forEach((row, index) => {
            groups.push({
                id: row.id,
                order: order + index,
                treeLevel,
                label: { kind: "history", stageName: row.stageName },
            });
            items.push({
                id: row.id,
                group: row.id,
                type: "range",
                start: utcToDate(row.start),
                end: utcToDate(row.end),
                className: "gantt-stage-history",
                style: `background: ${colorsOf(row.stageId)[0]};`,
                editable: false,
                selectable: false,
                view: { kind: "plain" },
                tooltip: [row.stageName, `${localDateTimeLabel(row.start)} → ${localDateTimeLabel(row.visitEnd)}`],
            });
        });
        model.historyEdges.push(...history.edges);
        return history.rows.map((row) => row.id);
    };

    const addTaskTree = (groupTasks, baseLevel, rowIdOf, userId) => {
        const rootIds = [];
        for (const row of buildTaskTree(groupTasks)) {
            const task = row.task;
            const rowId = rowIdOf(task.id);
            const treeLevel = baseLevel + row.depth;
            const prefix = prefixOf(task);
            const group = { id: rowId, order: order++, treeLevel };
            groups.push(group);
            items.push(rowItem(task, rowId, prefix, row.childIds.length > 0, userId));
            const historyIds = options.showStageHistory ? addStageHistory(task, treeLevel + 1, userId) : [];
            order += historyIds.length;
            const nestedIds = [...historyIds, ...row.childIds.map(rowIdOf)];
            const outsideParentLabel = outsideParentOf(row, taskMap);
            group.label = {
                kind: "task",
                task,
                prefix,
                outsideParentLabel,
                outsideParentText: outsideParentLabel && _t("sub-task of %(parent)s", { parent: outsideParentLabel }),
                isNestedLeaf: !outsideParentLabel && treeLevel > 0 && !nestedIds.length,
                cells: columnCells(task, columns, today),
            };
            if (nestedIds.length) {
                group.nestedGroups = nestedIds;
                group.showNested = true;
            }
            if (!row.depth) {
                rootIds.push(rowId);
            }
        }
        return rootIds;
    };

    const taskRowId = (taskId) => `t_${taskId}`;
    if (options.groupBy === "user") {
        const tasksByUser = new Map();
        const unassigned = [];
        for (const task of tasks) {
            if (!(task.user_list || []).length) {
                unassigned.push(task);
            }
            for (const user of task.user_list || []) {
                if (!tasksByUser.has(user.id)) {
                    tasksByUser.set(user.id, { name: user.name, tasks: [] });
                }
                tasksByUser.get(user.id).tasks.push(task);
            }
        }
        const userEntries = [...tasksByUser.entries()].sort((a, b) => a[1].name.localeCompare(b[1].name));
        if (unassigned.length) {
            userEntries.push([0, { tasks: unassigned }]);
        }
        for (const [userId, entry] of userEntries) {
            const userGroup = {
                id: `user_${userId || "unassigned"}`,
                order: order++,
                showNested: true,
                label: userId ? { kind: "user", name: entry.name } : { kind: "unassigned" },
            };
            groups.push(userGroup);
            const userTasks = [...entry.tasks].sort(byPlannedStart);
            userGroup.nestedGroups = addTaskTree(userTasks, 1, (taskId) => `ut_${userId}_${taskId}`, userId);
        }
    } else if (options.groupBy === "stage") {
        for (const stage of groupTasksByStage(tasks)) {
            const stageGroup = {
                id: `stage_${stage.stageId}`,
                order: order++,
                showNested: true,
                label: { kind: "stage", name: stage.name, count: stage.tasks.length },
            };
            groups.push(stageGroup);
            stageGroup.nestedGroups = addTaskTree(stage.tasks, 1, taskRowId);
        }
    } else {
        addTaskTree([...tasks].sort(byPlannedStart), 0, taskRowId);
    }

    if (options.showBaseline) {
        items.push(...baselineItems(items, taskMap));
    }
    addUnscheduledGroups(model, order, prefixOf, columns, today);
    if (options.groupBy === "user" && !options.showStageHistory) {
        markOverlaps(items, taskMap);
    }
    model.analysis = analyzeDependencies(tasks);
    model.edges = dependencyEdges(tasks, items, options.groupBy === "user");
    model.range = dateRange(items);
    return model;
}

function baselineItems(items, taskMap) {
    return items
        .filter((item) => item.isTaskBar)
        .map((item) => [item, taskMap[item.taskId]])
        .filter(([, task]) => task && task.baseline_date_start && task.baseline_date_end)
        .map(([item, task]) => ({
            id: `bl_${item.id}`,
            group: item.group,
            type: "range",
            start: utcToDate(task.baseline_date_start),
            end: utcToDate(task.baseline_date_end),
            className: "gantt-baseline",
            editable: false,
            selectable: false,
            view: { kind: "plain" },
            tooltip: [
                _t("Baseline: %(start)s → %(end)s", {
                    start: localDayLabel(task.baseline_date_start),
                    end: localDayLabel(task.baseline_date_end),
                }),
            ],
        }));
}

function addUnscheduledGroups(model, order, prefixOf, columns, today) {
    const entries = model.unscheduled;
    if (!entries.length) {
        return;
    }
    model.groups.push({
        id: "unscheduled",
        order: order++,
        nestedGroups: entries.map((task) => `us_${task.id}`),
        showNested: false,
        className: "gantt-unscheduled-group",
        label: { kind: "unscheduled", count: entries.length },
    });
    for (const task of entries) {
        model.groups.push({
            id: `us_${task.id}`,
            order: order++,
            treeLevel: 1,
            label: {
                kind: "task",
                task,
                prefix: prefixOf(task),
                isUnscheduled: true,
                cells: columnCells(task, columns, today),
            },
        });
    }
}

function markOverlaps(items, taskMap) {
    const barsByUser = new Map();
    for (const item of items) {
        if (item.isTaskBar && item.userId) {
            if (!barsByUser.has(item.userId)) {
                barsByUser.set(item.userId, []);
            }
            barsByUser.get(item.userId).push({ id: item.id, taskId: item.taskId, start: item.start, end: item.end });
        }
    }
    const overlappingIds = new Set();
    for (const bars of barsByUser.values()) {
        findOverlappingBarIds(bars, taskMap).forEach((id) => overlappingIds.add(id));
    }
    for (const item of items) {
        if (overlappingIds.has(item.id)) {
            item.className += " gantt-overlap";
        }
    }
}

/**
 * One arrow per dependency, from the predecessor's bar to the successor's. Grouped by person, a
 * dependency between two tasks of the same person is drawn in that person's rows, one per person;
 * otherwise once, between the first rows found.
 */
function dependencyEdges(tasks, items, isGroupedByUser) {
    const rowsByTask = new Map();
    for (const item of items) {
        if (item.isTaskBar) {
            if (!rowsByTask.has(item.taskId)) {
                rowsByTask.set(item.taskId, new Map());
            }
            rowsByTask.get(item.taskId).set(item.userId ?? "all", item.id);
        }
    }
    const edges = [];
    for (const task of tasks) {
        for (const predecessorId of task.depend_on_ids || []) {
            const fromRows = rowsByTask.get(predecessorId);
            const toRows = rowsByTask.get(task.id);
            if (!fromRows || !toRows) {
                continue;
            }
            const edge = { pred: predecessorId, succ: task.id };
            const sharedScopes = isGroupedByUser ? [...fromRows.keys()].filter((scope) => toRows.has(scope)) : [];
            if (sharedScopes.length) {
                for (const scope of sharedScopes) {
                    edges.push({ ...edge, from: fromRows.get(scope), to: toRows.get(scope) });
                }
            } else {
                edges.push({ ...edge, from: fromRows.values().next().value, to: toRows.values().next().value });
            }
        }
    }
    return edges;
}

function dateRange(items) {
    const starts = items.map((item) => item.start.getTime()).filter((time) => !isNaN(time));
    const ends = items.map((item) => (item.end || item.start).getTime()).filter((time) => !isNaN(time));
    const start = DateTime.fromMillis(starts.length ? Math.min(...starts) : Date.now()).minus({ days: 7 });
    const end = DateTime.fromMillis(ends.length ? Math.max(...ends) : Date.now()).plus({ days: 14 });
    const bounds = ganttPanBounds(start.toJSDate(), end.toJSDate());
    return { start: start.toJSDate(), end: end.toJSDate(), ...bounds };
}
