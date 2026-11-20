import {
    Component,
    markRaw,
    onMounted,
    onWillUnmount,
    proxy,
    signal,
    status,
    t,
    useOnChange,
    useProps,
} from "@odoo/owl";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { CheckboxItem } from "@web/core/dropdown/checkbox_item";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { localization } from "@web/core/l10n/localization";
import { _t } from "@web/core/l10n/translation";
import { downloadFile } from "@web/core/network/download";
import { RPCError } from "@web/core/network/rpc";
import { usePopover } from "@web/core/popover/popover_hook";
import { user } from "@web/core/user";
import { Mutex } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { renderToElement } from "@web/core/utils/render";

import { GanttDependencyPopover } from "./gantt_dependency_popover";
import {
    ARROW_STYLES,
    GANTT_COLUMNS,
    activeColumns,
    buildGanttModel,
    dateToUtc,
    elbowPath,
    isGroupColumn,
    shiftByWholeDays,
    shiftGanttWindow,
    shownColumnsWidth,
    sidebarMaxWidth,
    utcToDate,
    weekStartMoment,
    wholeDaysBetween,
} from "./gantt_model";
import { GanttProjectPicker } from "./gantt_project_picker";
import { GanttSearch } from "./gantt_search";
import { visTimeline } from "./vis_timeline";

const SVG_NS = "http://www.w3.org/2000/svg";
const XLSX_MIMETYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const FLASH_DURATION = 2500;
// a milestone is a rotated square: its visible height is its diagonal, not its box
const MILESTONE_VISUAL_HEIGHT = 25;

function errorMessage(error) {
    return error instanceof RPCError ? error.data.message : error.message || String(error);
}

/**
 * The choices of a Gantt chart that outlive it: the dashboard owns them, so the chart comes back
 * as it was left after the cards or a breadcrumb return. The window is in epoch milliseconds.
 */
export function makeGanttSettings(values = {}) {
    return {
        projectIds: [],
        scale: "week",
        groupBy: "user",
        showCompleted: false,
        showBaseline: false,
        showStageHistory: false,
        facets: [],
        window: null,
        ...values,
    };
}

/**
 * The multi-project Gantt chart: a vis-timeline chart of the planned tasks, with the toolbar,
 * search and legend around it. The dashboard mounts it in Gantt mode with the settings it keeps,
 * and asks for a reload by raising reloadCount.
 */
export class GanttView extends Component {
    static template = "project_team_dashboard.GanttView";
    static components = { CheckboxItem, Dropdown, GanttProjectPicker, GanttSearch };
    props = useProps({
        projectId: t.or([t.number(), t.literal(false)]).optional(false),
        projects: t.array(t.object({ id: t.number(), name: t.string() })).optional([]),
        reloadCount: t.number().optional(0),
        settings: t.object().optional(() => proxy(makeGanttSettings())),
    });
    timelineRef = signal.ref();
    popoverAnchorRef = signal.ref();

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.dialog = useService("dialog");
        this.dependencyPopover = usePopover(GanttDependencyPopover, { position: "bottom" });
        if (!this.settings.projectIds.length && this.props.projectId) {
            this.settings.projectIds.push(this.props.projectId);
        }
        this.state = proxy({
            columns: [],
            // a toggle made before the stored columns are known would save over them
            hasLoadedColumns: false,
            data: null,
            shownTasks: [],
            stageLegend: [],
            isLoading: false,
            loadError: "",
            emptyReason: "",
            linkLine: null,
        });
        this.scales = [
            { key: "day", label: _t("Day") },
            { key: "week", label: _t("Week") },
            { key: "month", label: _t("Month") },
        ];
        this.groupings = [
            { key: "task", label: _t("Task"), title: _t("Group by task"), icon: "task" },
            { key: "user", label: _t("User"), title: _t("Group by assignee"), icon: "group" },
            { key: "stage", label: _t("Stage"), title: _t("Group by stage"), icon: "view_kanban" },
        ];
        this.columnChoices = GANTT_COLUMNS;
        this.loadId = 0;
        this.timeline = null;
        this.model = null;
        this.stopLinking = null;
        this.sidebar = { width: 0, key: null };
        this.columnsMutex = new Mutex();
        this.columnToggles = 0;
        this.settledColumnToggles = 0;
        this.savedColumns = [];
        onMounted(() => this.loadData({ keepWindow: true }));
        useOnChange(
            () => [this.props.reloadCount],
            () => {
                this.loadData({ keepWindow: true });
            },
            { initialRun: false }
        );
        onWillUnmount(() => {
            this.stopLinking?.();
            this.rememberWindow();
            this.destroyTimeline();
        });
    }

    get settings() {
        return this.props.settings;
    }

    get valueTasks() {
        return this.state.data ? this.state.data.tasks : [];
    }

    get noDatesCount() {
        return this.state.data ? this.state.data.tasks_without_dates : 0;
    }

    get noDatesLabel() {
        const count = this.noDatesCount;
        return count === 1 ? _t("1 task without dates") : _t("%s tasks without dates", count);
    }

    get isProjectManager() {
        return Boolean(this.state.data && this.state.data.is_project_manager);
    }

    get isSavingColumns() {
        return this.settledColumnToggles !== this.columnToggles;
    }

    isColumnLocked(column) {
        return isGroupColumn(column.key, this.settings.groupBy);
    }

    columnTitle(column) {
        if (this.isColumnLocked(column)) {
            const grouping = this.groupings.find((choice) => choice.key === this.settings.groupBy);
            return _t("%(column)s: hidden while the chart is grouped by %(grouping)s", {
                column: column.name,
                grouping: grouping.label,
            });
        }
        return String(column.help);
    }

    // Loading and drawing

    async loadData({ keepWindow = false } = {}) {
        const loadId = ++this.loadId;
        const projectIds = [...this.settings.projectIds];
        if (!projectIds.length) {
            this.clearChart({ emptyReason: "no_projects" });
            return;
        }
        const window = keepWindow ? this.currentWindow() : null;
        // the answer to a load sent before the last column save settled may carry the columns stored before it
        const columnToggles = this.isSavingColumns ? -1 : this.columnToggles;
        Object.assign(this.state, { isLoading: true, loadError: "" });
        let vis;
        let data;
        try {
            [vis, data] = await Promise.all([
                visTimeline.load(),
                this.orm.call("project.project", "get_gantt_chart_data", [], {
                    project_ids: projectIds,
                    show_completed: this.settings.showCompleted,
                    include_stage_history: this.settings.showStageHistory,
                }),
            ]);
        } catch (error) {
            if (loadId === this.loadId && status(this) !== "destroyed") {
                this.clearChart({ loadError: errorMessage(error) });
            }
            return;
        }
        if (loadId !== this.loadId || status(this) === "destroyed") {
            return;
        }
        this.vis = vis;
        if (columnToggles === this.columnToggles) {
            this.savedColumns = data.columns || [];
            Object.assign(this.state, { columns: this.savedColumns, hasLoadedColumns: true });
        }
        Object.assign(this.state, { data: markRaw(data), isLoading: false });
        this.renderTimeline(window);
    }

    /** No chart, and nothing drawn from the tasks of the last one: its legend and go-to list too. */
    clearChart({ emptyReason = "", loadError = "" }) {
        this.destroyTimeline();
        Object.assign(this.state, {
            data: null,
            shownTasks: [],
            stageLegend: [],
            isLoading: false,
            loadError,
            emptyReason,
        });
    }

    /** The window shown, or before the chart is drawn the one it was left at. */
    currentWindow() {
        if (this.timeline) {
            return this.timeline.getWindow();
        }
        const window = this.settings.window;
        return window && { start: new Date(window.start), end: new Date(window.end) };
    }

    rememberWindow() {
        if (this.timeline) {
            const { start, end } = this.timeline.getWindow();
            this.settings.window = { start: start.getTime(), end: end.getTime() };
        }
    }

    renderTimeline(window = null) {
        const data = this.state.data;
        if (!data) {
            return;
        }
        this.dependencyPopover.close();
        this.destroyTimeline();
        const model = buildGanttModel(data, {
            groupBy: this.settings.groupBy,
            facets: this.settings.facets,
            columns: this.state.columns,
            showBaseline: this.settings.showBaseline,
            showStageHistory: this.settings.showStageHistory,
            isMultiProject: this.settings.projectIds.length > 1,
            isRtl: localization.direction === "rtl",
        });
        this.model = model;
        const hasAnyTask = Boolean(data.tasks.length || (data.unscheduled || []).length);
        Object.assign(this.state, {
            shownTasks: markRaw(model.tasks),
            stageLegend: markRaw(model.stageLegend),
            emptyReason: model.isEmpty ? (hasAnyTask ? "no_matches" : "no_tasks") : "",
        });
        if (model.isEmpty) {
            return;
        }
        this.groupsDataSet = new this.vis.DataSet(model.groups);
        this.itemsDataSet = new this.vis.DataSet(model.items);
        this.timeline = new this.vis.Timeline(
            this.timelineRef(),
            this.itemsDataSet,
            this.groupsDataSet,
            this.timelineOptions(model, window)
        );
        this.timeline.on("changed", () => this.onTimelineChanged());
        this.timeline.on("rangechanged", () => {
            this.drawArrows();
            this.rememberWindow();
        });
        this.rememberWindow();
    }

    timelineOptions(model, window) {
        const top = this.timelineRef().getBoundingClientRect().top;
        return {
            // given the direction, vis-timeline skips reading it from the page, which keeps the chart
            // hidden until its first range change settles
            rtl: localization.direction === "rtl",
            orientation: { axis: "top" },
            showCurrentTime: true,
            stack: false,
            zoomKey: "ctrlKey",
            horizontalScroll: true,
            verticalScroll: true,
            // vis scrolls the rows itself, which keeps the time axis pinned above them
            maxHeight: Math.max(400, document.documentElement.clientHeight - top - 16),
            margin: { item: { horizontal: 0, vertical: 4 } },
            // a bar's content spans the bar, its link handle at the end: vis would shift it into the window
            align: "left",
            start: window ? window.start : model.range.start,
            end: window ? window.end : model.range.end,
            min: model.range.min,
            max: model.range.max,
            groupOrder: (a, b) => a.order - b.order,
            groupTemplate: (group) =>
                group && group.label
                    ? renderToElement("project_team_dashboard.GanttGroupLabel", { label: group.label })
                    : "",
            template: (item) =>
                renderToElement("project_team_dashboard.GanttItemContent", { view: item.view || {} }),
            tooltip: {
                followMouse: true,
                overflowMethod: "cap",
                template: (item) =>
                    item.tooltip
                        ? renderToElement("project_team_dashboard.GanttItemTooltip", { lines: item.tooltip })
                        : null,
            },
            editable: { updateTime: true, updateGroup: false, add: false, remove: false },
            selectable: true,
            snap: null,
            timeAxis: { scale: this.settings.scale, step: 1 },
            moment: weekStartMoment(this.vis.moment, localization.weekStart),
            dataAttributes: ["id"],
            onMove: (item, callback) => this.onItemMove(item, callback),
        };
    }

    destroyTimeline() {
        if (this.timeline) {
            this.timeline.destroy();
            this.timeline = null;
        }
        this.columnsHeader = null;
        this.sidebar = { width: 0, key: null };
    }

    onTimelineChanged() {
        this.drawArrows();
        this.capSidebarWidth();
        this.centerItems();
        this.markGroupBorders();
        this.renderColumnsHeader();
    }

    // Toolbar

    addProject(projectId) {
        if (!this.settings.projectIds.includes(projectId)) {
            this.settings.projectIds.push(projectId);
            this.loadData();
        }
    }

    removeProject(projectId) {
        this.settings.projectIds = this.settings.projectIds.filter((id) => id !== projectId);
        this.loadData();
    }

    setScale(scale) {
        this.settings.scale = scale;
        this.timeline?.setOptions({ timeAxis: { scale, step: 1 } });
    }

    setGroupBy(groupBy) {
        if (groupBy !== this.settings.groupBy) {
            this.settings.groupBy = groupBy;
            this.renderTimeline();
        }
    }

    fit() {
        this.timeline?.fit();
    }

    goToToday() {
        this.timeline?.moveTo(new Date());
    }

    shiftWindow(direction) {
        if (this.timeline) {
            const { start, end } = this.timeline.getWindow();
            const shifted = shiftGanttWindow(start, end, this.settings.scale, direction);
            this.timeline.setWindow(shifted.start, shifted.end);
        }
    }

    toggleShowCompleted() {
        this.settings.showCompleted = !this.settings.showCompleted;
        this.loadData();
    }

    toggleStageHistory() {
        this.settings.showStageHistory = !this.settings.showStageHistory;
        this.loadData({ keepWindow: true });
    }

    toggleBaseline() {
        this.settings.showBaseline = !this.settings.showBaseline;
        if (!this.state.data) {
            return;
        }
        this.renderTimeline(this.timeline && this.timeline.getWindow());
        const hasBaseline = this.state.shownTasks.some((task) => task.baseline_date_start && task.baseline_date_end);
        if (this.settings.showBaseline && !hasBaseline) {
            this.notification.add(
                _t(
                    "None of the tasks shown has a baseline yet. A project manager can press Save Baseline to store the current plan."
                ),
                { title: _t("Baseline"), type: "warning" }
            );
        }
    }

    /**
     * The chart shows the new columns at once, so a toggle made before the last one is saved starts
     * from it; the saves go one after the other, the last toggle's being the one stored.
     */
    toggleColumn(columnKey) {
        if (isGroupColumn(columnKey, this.settings.groupBy)) {
            return;
        }
        const chosenKeys = new Set(this.state.columns);
        if (chosenKeys.has(columnKey)) {
            chosenKeys.delete(columnKey);
        } else {
            chosenKeys.add(columnKey);
        }
        const columns = GANTT_COLUMNS.map((column) => column.key).filter((key) => chosenKeys.has(key));
        this.state.columns = columns;
        this.renderTimeline(this.timeline && this.timeline.getWindow());
        const toggle = ++this.columnToggles;
        return this.columnsMutex.exec(() => this.saveColumns(columns, toggle));
    }

    /** A save the server refuses puts back the columns stored, unless a later toggle is yet to be saved. */
    async saveColumns(columns, toggle) {
        try {
            await this.orm.write("res.users", [user.userId], { gantt_columns: columns.join(",") });
            this.savedColumns = columns;
        } catch (error) {
            if (toggle === this.columnToggles && status(this) !== "destroyed") {
                this.state.columns = this.savedColumns;
                this.renderTimeline(this.timeline && this.timeline.getWindow());
            }
            if (!(error instanceof RPCError)) {
                throw error;
            }
            this.notification.add(errorMessage(error), { type: "danger" });
        } finally {
            this.settledColumnToggles = toggle;
        }
    }

    saveBaselines() {
        const projectIds = [...this.settings.projectIds];
        const body =
            projectIds.length > 1
                ? _t(
                      "Save the current plan of the %s projects shown as their baselines? Each replaces that project's current baseline.",
                      projectIds.length
                  )
                : _t("Save the current plan as the project baseline? It replaces the current baseline.");
        this.dialog.add(ConfirmationDialog, {
            title: _t("Save Baseline"),
            body,
            confirm: async () => {
                await this.orm.call("project.project", "save_gantt_baselines", [projectIds]);
                this.notification.add(_t("Baseline saved."), { type: "success" });
                this.settings.showBaseline = true;
                await this.loadData({ keepWindow: true });
            },
            cancel: () => {},
        });
    }

    async exportExcel() {
        const projectIds = [...this.settings.projectIds];
        if (!projectIds.length) {
            this.notification.add(_t("Please select a project first."), { title: _t("Export"), type: "warning" });
            return;
        }
        const result = await this.orm.call("project.project", "export_gantt_excel", [projectIds]);
        if (result.error) {
            this.notification.add(result.error, { title: _t("Export"), type: "warning" });
            return;
        }
        const bytes = Uint8Array.from(atob(result.data), (char) => char.charCodeAt(0));
        downloadFile(new Blob([bytes], { type: XLSX_MIMETYPE }), result.filename, XLSX_MIMETYPE);
    }

    openTasksWithoutDates() {
        const unscheduledIds = (this.state.data.unscheduled || []).map((task) => task.id);
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name: _t("Tasks Without Dates"),
            res_model: "project.task",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: [["id", "in", unscheduledIds]],
            target: "current",
        });
    }

    openTask(taskId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: taskId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // Search

    addFacet(facet) {
        this.settings.facets.push(facet);
        this.renderTimeline();
    }

    removeFacet(index) {
        this.settings.facets.splice(index, 1);
        this.renderTimeline();
    }

    goToTask(taskId) {
        if (!this.timeline) {
            return;
        }
        const item = this.itemsDataSet.get({ filter: (entry) => entry.isTaskBar && entry.taskId === taskId })[0];
        if (!item) {
            return;
        }
        this.timeline.setSelection([item.id]);
        this.timeline.focus(item.id, { zoom: false, animation: { duration: 400 } });
        this.flashGroup(item.group);
    }

    flashGroup(groupId) {
        const groupsDataSet = this.groupsDataSet;
        const group = groupsDataSet.get(groupId);
        // vis-timeline cannot take a group's last class away (it adds a null class), so one stays
        const baseClass = (group.className || "").replace(/\s*o_gantt_found/g, "").trim() || "o_gantt_group";
        groupsDataSet.update({ id: groupId, className: `${baseClass} o_gantt_found` });
        setTimeout(() => {
            if (this.groupsDataSet === groupsDataSet && groupsDataSet.get(groupId)) {
                groupsDataSet.update({ id: groupId, className: baseClass });
            }
        }, FLASH_DURATION);
    }

    // Timeline interactions

    async onItemMove(item, callback) {
        const stored = this.itemsDataSet.get(item.id);
        if (!stored || !stored.isTaskBar) {
            callback(null);
            return;
        }
        const task = this.taskById(stored.taskId);
        const offset = item.start - stored.start;
        const startDays = wholeDaysBetween(stored.start, item.start);
        const endDays = item.end ? wholeDaysBetween(stored.end, item.end) : startDays;
        item.start = shiftByWholeDays(stored.start, item.start);
        let movedEnd;
        if (item.end) {
            item.end = shiftByWholeDays(stored.end, item.end);
            movedEnd = item.end;
        } else {
            // a milestone is a point: its end moves by as many days
            const plannedEnd = utcToDate(task.planned_date_end);
            movedEnd = shiftByWholeDays(plannedEnd, new Date(plannedEnd.getTime() + offset));
        }
        // the bar pads a missing date, which is not the task's to keep unless a resize dragged its edge
        const isResize = startDays !== endDays;
        const vals = {};
        if (task.has_planned_date_start || (isResize && startDays !== 0)) {
            vals.planned_date_start = dateToUtc(item.start);
        }
        if (task.has_planned_date_end || (isResize && endDays !== 0)) {
            vals.planned_date_end = dateToUtc(movedEnd);
        }
        try {
            await this.orm.write("project.task", [stored.taskId], vals);
        } catch (error) {
            callback(null);
            if (!(error instanceof RPCError)) {
                throw error;
            }
            this.notification.add(errorMessage(error), { type: "danger" });
            return;
        }
        callback(item);
        await this.loadData({ keepWindow: true });
    }

    /**
     * A press on a control drawn in the chart must not reach vis-timeline: it would toggle the row,
     * or redraw the arrows and take the pressed one away before its click.
     */
    onTimelinePress(ev) {
        const control = ev.target.closest(
            ".o_gantt_open_task, .o_gantt_unscheduled_name, .o_gantt_dep_handle, .o_gantt_dep_hit"
        );
        if (!control) {
            return;
        }
        ev.stopPropagation();
        if (ev.type === "pointerdown" && control.classList.contains("o_gantt_dep_handle")) {
            ev.preventDefault();
            this.startLinking(control, ev);
        }
    }

    onTimelineClick(ev) {
        const opener = ev.target.closest(".o_gantt_open_task, .o_gantt_unscheduled_name");
        if (opener) {
            ev.preventDefault();
            this.openTask(Number(opener.dataset.taskId));
            return;
        }
        const arrow = ev.target.closest(".o_gantt_dep_hit");
        if (arrow) {
            this.openDependencyPopover(this.model.edges[Number(arrow.dataset.edgeIndex)], ev);
        }
    }

    /** The openers drawn in the row labels are spans with a button role: Enter and Space press them. */
    onTimelineKeydown(ev) {
        const opener = ev.target.closest(".o_gantt_open_task, .o_gantt_unscheduled_name");
        if (opener && (ev.key === "Enter" || ev.key === " ")) {
            ev.preventDefault();
            this.openTask(Number(opener.dataset.taskId));
        }
    }

    itemOfElement(element) {
        const itemElement = element instanceof Element && element.closest(".vis-item");
        return itemElement && this.itemsDataSet ? this.itemsDataSet.get(itemElement.dataset.id) : null;
    }

    startLinking(handle, startEvent) {
        const sourceItem = this.itemOfElement(handle);
        if (!sourceItem || !sourceItem.isTaskBar) {
            return;
        }
        // a second press, by another finger, starts over: one link at a time
        this.stopLinking?.();
        const handleRect = handle.getBoundingClientRect();
        const x1 = handleRect.left + handleRect.width / 2;
        const y1 = handleRect.top + handleRect.height / 2;
        this.state.linkLine = { x1, y1, x2: startEvent.clientX, y2: startEvent.clientY };
        const onMove = (ev) => {
            this.state.linkLine = { x1, y1, x2: ev.clientX, y2: ev.clientY };
        };
        const onUp = (ev) => {
            this.stopLinking();
            // a touch pointer stays captured by the handle it started on: the bar is then the one under it
            const releasedOn = this.itemOfElement(ev.target);
            const targetItem =
                releasedOn && releasedOn.id !== sourceItem.id
                    ? releasedOn
                    : this.itemOfElement(document.elementFromPoint(ev.clientX, ev.clientY));
            if (targetItem && targetItem.isTaskBar && targetItem.taskId !== sourceItem.taskId) {
                this.linkTasks(sourceItem.taskId, targetItem.taskId);
            }
        };
        const stopLinking = () => {
            document.removeEventListener("pointermove", onMove);
            document.removeEventListener("pointerup", onUp);
            document.removeEventListener("pointercancel", stopLinking);
            this.state.linkLine = null;
            this.stopLinking = null;
        };
        this.stopLinking = stopLinking;
        document.addEventListener("pointermove", onMove);
        document.addEventListener("pointerup", onUp);
        document.addEventListener("pointercancel", stopLinking);
    }

    taskById(taskId) {
        return this.state.data.tasks.find((task) => task.id === taskId);
    }

    taskName(taskId) {
        const task = this.taskById(taskId);
        if (!task) {
            return `#${taskId}`;
        }
        return task.key ? `${task.key} ${task.name}` : task.name;
    }

    async linkTasks(predecessorId, successorId) {
        const successor = this.taskById(successorId);
        if (!successor || !successor.can_write) {
            this.notification.add(_t("You cannot edit the dependencies of %s.", this.taskName(successorId)), {
                title: _t("Dependencies"),
                type: "warning",
            });
            return;
        }
        if ((successor.depend_on_ids || []).includes(predecessorId)) {
            return;
        }
        await this.orm.call("project.task", "gantt_add_dependency", [[successorId], predecessorId]);
        await this.loadData({ keepWindow: true });
    }

    openDependencyPopover(edge, ev) {
        const anchor = this.popoverAnchorRef();
        const bodyRect = anchor.offsetParent.getBoundingClientRect();
        anchor.style.left = `${ev.clientX - bodyRect.left}px`;
        anchor.style.top = `${ev.clientY - bodyRect.top}px`;
        const info = this.model.analysis.edges[`${edge.pred}_${edge.succ}`] || { lag: 0, violated: false };
        const successor = this.taskById(edge.succ);
        this.dependencyPopover.open(anchor, {
            title: `${this.taskName(edge.pred)} → ${this.taskName(edge.succ)}`,
            lag: info.lag,
            isViolated: info.violated,
            canWrite: Boolean(successor && successor.can_write),
            onSave: (lag) => this.saveDependencyLag(edge, lag),
            onRemove: () => this.removeDependency(edge),
        });
    }

    async saveDependencyLag(edge, lagValue) {
        const lagDays = Number(lagValue);
        if (String(lagValue).trim() === "" || !Number.isInteger(lagDays) || lagDays < 0) {
            this.notification.add(_t("The lag must be a whole number of days, zero or more."), {
                title: _t("Dependencies"),
                type: "warning",
            });
            return;
        }
        await this.orm.call("project.task", "gantt_set_dependency_lag", [[edge.succ], edge.pred, lagDays]);
        this.dependencyPopover.close();
        await this.loadData({ keepWindow: true });
    }

    async removeDependency(edge) {
        await this.orm.call("project.task", "gantt_remove_dependency", [[edge.succ], edge.pred]);
        this.dependencyPopover.close();
        await this.loadData({ keepWindow: true });
    }

    // Drawing inside vis-timeline, after each of its redraws

    drawArrows() {
        const container = this.timelineRef();
        container.querySelector(".o_gantt_arrows_svg")?.remove();
        const model = this.model;
        const content = container.querySelector(".vis-center .vis-content");
        if (!this.timeline || !content || (!model.edges.length && !model.historyEdges.length)) {
            return;
        }
        const contentRect = content.getBoundingClientRect();
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("class", "o_gantt_arrows_svg");
        const defs = document.createElementNS(SVG_NS, "defs");
        for (const [styleKey, arrowStyle] of Object.entries(ARROW_STYLES)) {
            const marker = document.createElementNS(SVG_NS, "marker");
            const markerAttributes = {
                id: `gantt-arrowhead-${styleKey}`,
                markerWidth: "8",
                markerHeight: "6",
                refX: "8",
                refY: "3",
                orient: "auto",
                markerUnits: "strokeWidth",
            };
            for (const [name, value] of Object.entries(markerAttributes)) {
                marker.setAttribute(name, value);
            }
            const head = document.createElementNS(SVG_NS, "path");
            head.setAttribute("d", "M0,0 L8,3 L0,6 Z");
            head.setAttribute("fill", arrowStyle.color);
            marker.appendChild(head);
            defs.appendChild(marker);
        }
        svg.appendChild(defs);
        const isRtl = this.timeline.options.rtl;
        const pathBetween = (fromId, toId) => {
            const fromElement = content.querySelector(`.vis-item[data-id="${CSS.escape(fromId)}"]`);
            const toElement = content.querySelector(`.vis-item[data-id="${CSS.escape(toId)}"]`);
            if (!fromElement || !toElement) {
                return null;
            }
            const fromRect = fromElement.getBoundingClientRect();
            const toRect = toElement.getBoundingClientRect();
            return elbowPath(
                (isRtl ? fromRect.left : fromRect.right) - contentRect.left,
                fromRect.top + fromRect.height / 2 - contentRect.top,
                (isRtl ? toRect.right : toRect.left) - contentRect.left,
                toRect.top + toRect.height / 2 - contentRect.top,
                isRtl
            );
        };
        const addPath = (d, styleKey) => {
            const path = document.createElementNS(SVG_NS, "path");
            path.setAttribute("d", d);
            path.setAttribute("fill", "none");
            path.setAttribute("stroke", ARROW_STYLES[styleKey].color);
            path.setAttribute("stroke-width", ARROW_STYLES[styleKey].width);
            path.setAttribute("marker-end", `url(#gantt-arrowhead-${styleKey})`);
            svg.appendChild(path);
        };
        model.edges.forEach((edge, index) => {
            const d = pathBetween(edge.from, edge.to);
            if (!d) {
                return;
            }
            const info = model.analysis.edges[`${edge.pred}_${edge.succ}`] || { lag: 0 };
            addPath(d, info.violated ? "violated" : "normal");
            const hitPath = document.createElementNS(SVG_NS, "path");
            hitPath.setAttribute("d", d);
            hitPath.setAttribute("class", "o_gantt_dep_hit");
            hitPath.dataset.edgeIndex = index;
            const title = document.createElementNS(SVG_NS, "title");
            title.textContent = this.dependencyTitle(edge, info);
            hitPath.appendChild(title);
            svg.appendChild(hitPath);
        });
        for (const edge of model.historyEdges) {
            const d = pathBetween(edge.from, edge.to);
            if (d) {
                addPath(d, "normal");
            }
        }
        content.appendChild(svg);
    }

    dependencyTitle(edge, info) {
        const lines = [`${this.taskName(edge.pred)} → ${this.taskName(edge.succ)}`];
        if (info.lag) {
            lines.push(_t("Lag: %s d", info.lag));
        }
        if (info.violated) {
            lines.push(_t("Warning: starts before the predecessor's end plus lag."));
        }
        return lines.join("\n");
    }

    /** The panel of the row labels: vis-timeline puts it on the right of the chart right to left. */
    labelPanel() {
        return this.timelineRef().querySelector(this.timeline.options.rtl ? ".vis-panel.vis-right" : ".vis-panel.vis-left");
    }

    /**
     * Cap the label sidebar (`sidebarMaxWidth`). The width is locked at the widest label seen, so
     * scrolling to shorter rows does not shrink it; a new chart width or column set drops the lock
     * and measures the natural width again.
     */
    capSidebarWidth() {
        const container = this.timelineRef();
        const containerWidth = container.offsetWidth;
        const columnsWidth = shownColumnsWidth(activeColumns(this.state.columns, this.settings.groupBy), containerWidth);
        container.classList.toggle("o_gantt_cols_off", !columnsWidth);
        const maxWidth = sidebarMaxWidth(containerWidth, columnsWidth);
        const sidebar = this.labelPanel();
        if (!sidebar) {
            return;
        }
        const inners = sidebar.querySelectorAll(".vis-inner");
        const sidebarKey = `${containerWidth}:${columnsWidth}`;
        if (sidebarKey !== this.sidebar.key) {
            Object.assign(this.sidebar, { width: 0, key: sidebarKey });
            sidebar.style.width = "";
            for (const inner of inners) {
                inner.style.width = "";
                inner.style.maxWidth = "";
            }
        }
        const desiredWidth = Math.min(sidebar.offsetWidth, maxWidth);
        if (!this.sidebar.width || desiredWidth > this.sidebar.width) {
            this.sidebar.width = desiredWidth;
        }
        const lockWidth = Math.min(this.sidebar.width, maxWidth);
        sidebar.style.width = `${lockWidth}px`;
        if (lockWidth !== this.sidebar.laidOutWidth) {
            // vis placed the panels around the width before the cap (right to left, the names would end
            // short of the chart's edge): its _change lays them out again, without redraw()'s render of every bar
            this.sidebar.laidOutWidth = lockWidth;
            this.timeline.body.emitter.emit("_change", { queue: true });
        }
        const isRtl = this.timeline.options.rtl;
        if (!isRtl) {
            for (const panel of container.querySelectorAll(".vis-panel.vis-center, .vis-panel.vis-top, .vis-panel.vis-bottom")) {
                panel.style.left = `${lockWidth}px`;
            }
        }
        const sidebarRect = sidebar.getBoundingClientRect();
        for (const inner of inners) {
            const innerRect = inner.getBoundingClientRect();
            const indent = isRtl ? sidebarRect.right - innerRect.right : innerRect.left - sidebarRect.left;
            const innerWidth = `${Math.max(0, lockWidth - indent - 4)}px`;
            inner.style.width = innerWidth;
            inner.style.maxWidth = innerWidth;
        }
    }

    /** vis-timeline sets each item's top: bars are centred in their row, baselines sit at its foot. */
    centerItems() {
        for (const groupElement of this.timelineRef().querySelectorAll(".vis-foreground .vis-group")) {
            const groupHeight = groupElement.offsetHeight;
            for (const itemElement of groupElement.querySelectorAll(".vis-item")) {
                const itemHeight = itemElement.offsetHeight;
                if (itemElement.classList.contains("gantt-baseline")) {
                    itemElement.style.top = `${Math.max(0, groupHeight - itemHeight)}px`;
                } else if (groupHeight > itemHeight) {
                    const isMilestone = itemElement.classList.contains("gantt-milestone");
                    const visualHeight = isMilestone ? MILESTONE_VISUAL_HEIGHT : itemHeight;
                    itemElement.style.top = `${Math.round((groupHeight - visualHeight) / 2)}px`;
                }
            }
        }
    }

    /** The last row of a nested block gets a closing border, in the rows and in the sidebar. */
    markGroupBorders() {
        const container = this.timelineRef();
        for (const selector of [".vis-foreground .vis-group", ".vis-labelset .vis-label"]) {
            const rows = [...container.querySelectorAll(selector)];
            rows.forEach((row, index) => {
                const next = rows[index + 1];
                const isLastNested =
                    row.classList.contains("vis-nested-group") &&
                    (!next || !next.classList.contains("vis-nested-group"));
                row.classList.toggle("gantt-last-nested", isLastNested);
            });
        }
    }

    renderColumnsHeader() {
        const container = this.timelineRef();
        const root = container.querySelector(".vis-timeline");
        const columns = activeColumns(this.state.columns, this.settings.groupBy);
        const top = root && root.querySelector(".vis-panel.vis-top");
        const bodyCell = root && this.labelPanel()?.querySelector(".o_gantt_cell:last-child");
        if (!columns.length || !top || !bodyCell || container.classList.contains("o_gantt_cols_off")) {
            this.columnsHeader?.remove();
            this.columnsHeader = null;
            return;
        }
        if (!this.columnsHeader || !this.columnsHeader.isConnected) {
            this.columnsHeader = renderToElement("project_team_dashboard.GanttColumnsHeader", { columns });
            root.appendChild(this.columnsHeader);
        }
        // the header spans from the chart's border to the last cell; the root's borders are even
        const rootRect = root.getBoundingClientRect();
        const cellRect = bodyCell.getBoundingClientRect();
        const headerWidth = this.timeline.options.rtl
            ? rootRect.right - root.clientLeft - cellRect.left
            : cellRect.right - rootRect.left - root.clientLeft;
        this.columnsHeader.style.width = `${headerWidth}px`;
        this.columnsHeader.style.height = `${top.offsetHeight}px`;
    }
}
