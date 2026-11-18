odoo.define("project_team_dashboard.team_dashboard_tests", function (require) {
    "use strict";

    var TeamDashboard = require("project_team_dashboard.team_dashboard");

    QUnit.module("project_team_dashboard", {}, function () {
        QUnit.module("shiftGanttWindow");

        QUnit.test("the week scale moves the window by one month", function (assert) {
            var shifted = TeamDashboard.shiftGanttWindow(
                new Date(2026, 6, 1), new Date(2026, 8, 15), "week", 1
            );
            assert.strictEqual(shifted.start.getTime(), new Date(2026, 7, 1).getTime());
            assert.strictEqual(shifted.end.getTime(), new Date(2026, 9, 15).getTime());
        });

        QUnit.test("the month scale moves back by three months", function (assert) {
            var shifted = TeamDashboard.shiftGanttWindow(
                new Date(2026, 6, 1), new Date(2026, 8, 15), "month", -1
            );
            assert.strictEqual(shifted.start.getTime(), new Date(2026, 3, 1).getTime());
            assert.strictEqual(shifted.end.getTime(), new Date(2026, 5, 15).getTime());
        });

        QUnit.test("the day scale moves by one week", function (assert) {
            var shifted = TeamDashboard.shiftGanttWindow(
                new Date(2026, 6, 1), new Date(2026, 6, 15), "day", 1
            );
            assert.strictEqual(shifted.start.getTime(), new Date(2026, 6, 8).getTime());
            assert.strictEqual(shifted.end.getTime(), new Date(2026, 6, 22).getTime());
        });

        QUnit.module("groupTasksByStage");

        QUnit.test("stages follow their sequence and tasks their start", function (assert) {
            var stages = TeamDashboard.groupTasksByStage([
                {id: 1, stage_id: 43, stage_name: "Development", stage_sequence: 35,
                    planned_date_start: "2026-08-10 00:00:00"},
                {id: 2, stage_id: 41, stage_name: "Backlog", stage_sequence: 4,
                    planned_date_start: "2026-08-01 00:00:00"},
                {id: 3, stage_id: 43, stage_name: "Development", stage_sequence: 35,
                    planned_date_start: "2026-08-02 00:00:00"},
                {id: 4, stage_id: false, stage_name: "", planned_date_start: "2026-07-01 00:00:00"},
            ]);
            assert.deepEqual(stages.map(function (s) { return s.name; }), ["Backlog", "Development", "No Stage"]);
            assert.deepEqual(stages[1].tasks.map(function (t) { return t.id; }), [3, 1]);
            assert.deepEqual(stages[2].tasks.map(function (t) { return t.id; }), [4]);
        });

        QUnit.module("buildTaskTree");

        function treeShape(rows) {
            return rows.map(function (row) {
                return [row.task.id, row.depth, row.childIds, row.outsideParentId];
            });
        }

        QUnit.test("sub-tasks nest under their parent at every depth", function (assert) {
            var rows = TeamDashboard.buildTaskTree([
                {id: 1, parent_id: false},
                {id: 4, parent_id: false},
                {id: 3, parent_id: 2},
                {id: 2, parent_id: 1},
            ]);
            assert.deepEqual(treeShape(rows), [
                [1, 0, [2], false],
                [2, 1, [3], false],
                [3, 2, [], false],
                [4, 0, [], false],
            ]);
        });

        QUnit.test("a sub-task whose parent is outside the group is a root naming its parent", function (assert) {
            var rows = TeamDashboard.buildTaskTree([
                {id: 5, parent_id: 9},
                {id: 6, parent_id: 5},
            ]);
            assert.deepEqual(treeShape(rows), [
                [5, 0, [6], 9],
                [6, 1, [], false],
            ]);
        });

        QUnit.test("siblings keep the order of the group's tasks", function (assert) {
            var rows = TeamDashboard.buildTaskTree([
                {id: 1, parent_id: false},
                {id: 8, parent_id: 1},
                {id: 7, parent_id: 1},
            ]);
            assert.deepEqual(rows[0].childIds, [8, 7]);
            assert.deepEqual(rows.map(function (row) { return row.task.id; }), [1, 8, 7]);
        });

        QUnit.test("a parent cycle still lists every task once", function (assert) {
            var rows = TeamDashboard.buildTaskTree([
                {id: 1, parent_id: 2},
                {id: 2, parent_id: 1},
            ]);
            assert.deepEqual(rows.map(function (row) { return row.task.id; }).sort(), [1, 2]);
        });

        QUnit.module("findOverlappingBarIds");

        var familyTaskMap = {
            1: {id: 1, parent_id: false},
            2: {id: 2, parent_id: 1},
            3: {id: 3, parent_id: 2},
            4: {id: 4, parent_id: false},
        };

        QUnit.test("a task and its sub-tasks at any depth are an ancestor pair", function (assert) {
            assert.ok(TeamDashboard.isAncestorPair(familyTaskMap, 1, 2));
            assert.ok(TeamDashboard.isAncestorPair(familyTaskMap, 3, 1));
            assert.notOk(TeamDashboard.isAncestorPair(familyTaskMap, 3, 4));
        });

        QUnit.test("a parent covering its sub-tasks is not an overlap", function (assert) {
            var overlapping = TeamDashboard.findOverlappingBarIds([
                {id: "p", taskId: 1, start: "2026-03-01", end: "2026-03-20"},
                {id: "c", taskId: 2, start: "2026-03-02", end: "2026-03-05"},
                {id: "g", taskId: 3, start: "2026-03-03", end: "2026-03-04"},
            ], familyTaskMap);
            assert.deepEqual(overlapping, {});
        });

        QUnit.test("unrelated overlapping tasks are flagged", function (assert) {
            var overlapping = TeamDashboard.findOverlappingBarIds([
                {id: "p", taskId: 1, start: "2026-03-01", end: "2026-03-20"},
                {id: "c", taskId: 2, start: "2026-03-02", end: "2026-03-05"},
                {id: "o", taskId: 4, start: "2026-03-04", end: "2026-03-06"},
            ], familyTaskMap);
            assert.deepEqual(overlapping, {p: true, c: true, o: true});
        });

        QUnit.test("tasks that only touch do not overlap", function (assert) {
            var overlapping = TeamDashboard.findOverlappingBarIds([
                {id: "a", taskId: 1, start: "2026-03-01", end: "2026-03-05"},
                {id: "b", taskId: 4, start: "2026-03-05", end: "2026-03-08"},
            ], familyTaskMap);
            assert.deepEqual(overlapping, {});
        });

        QUnit.module("ganttBarRules");

        QUnit.test("a milestone with sub-task rows is a bar, without them a point", function (assert) {
            var milestone = {
                task_type: "milestone",
                planned_date_start: "2026-03-01 09:00:00",
                planned_date_end: "2026-03-06 18:00:00",
            };
            assert.notOk(TeamDashboard.ganttBarRules(milestone, true).isPoint);
            assert.ok(TeamDashboard.ganttBarRules(milestone, false).isPoint);
        });

        QUnit.test("a milestone whose sub-tasks span no time stays a point", function (assert) {
            var milestone = {
                task_type: "milestone",
                planned_date_start: "2026-03-01 09:00:00",
                planned_date_end: "2026-03-01 09:00:00",
            };
            assert.ok(TeamDashboard.ganttBarRules(milestone, true).isPoint);
        });

        QUnit.test("dates rolled up from sub-tasks cannot be dragged", function (assert) {
            var task = {can_write: true, planned_date_start: "2026-03-01", planned_date_end: "2026-03-02"};
            assert.ok(TeamDashboard.ganttBarRules(task, false).canDrag);
            task.is_planned_from_subtasks = true;
            assert.notOk(TeamDashboard.ganttBarRules(task, true).canDrag);
            assert.notOk(TeamDashboard.ganttBarRules({can_write: true, is_deadline_only: true}, false).canDrag);
        });

        QUnit.module("subtaskOfMarker");

        QUnit.test("names the parent, escaped", function (assert) {
            assert.ok(TeamDashboard.subtaskOfMarker("PRJ-7").indexOf("sub-task of PRJ-7") >= 0);
            var marker = TeamDashboard.subtaskOfMarker("<b>Parent</b>");
            assert.ok(marker.indexOf("&lt;b&gt;Parent&lt;/b&gt;") >= 0);
        });

        QUnit.module("outsideParentOf");

        QUnit.test("a root names its parent from another group, or a hidden parent by its label", function (assert) {
            var taskMap = {1: {id: 1, key: "LOT-1"}, 6: {id: 6, key: "", name: "Unkeyed parent"}};
            var inOtherGroup = {task: {id: 2, parent_id: 1, parent_label: "LOT-1"}, depth: 0, outsideParentId: 1};
            assert.strictEqual(TeamDashboard.outsideParentOf(inOtherGroup, taskMap), "LOT-1");
            var unkeyedParent = {task: {id: 7, parent_id: 6, parent_label: "Unkeyed parent"}, depth: 0, outsideParentId: 6};
            assert.strictEqual(TeamDashboard.outsideParentOf(unkeyedParent, taskMap), "Unkeyed parent");
            var hiddenParent = {task: {id: 3, parent_id: false, parent_label: "LOT-13"}, depth: 0, outsideParentId: false};
            assert.strictEqual(TeamDashboard.outsideParentOf(hiddenParent, taskMap), "LOT-13");
            var topLevel = {task: {id: 4, parent_id: false, parent_label: ""}, depth: 0, outsideParentId: false};
            assert.strictEqual(TeamDashboard.outsideParentOf(topLevel, taskMap), null);
            var nested = {task: {id: 5, parent_id: 1, parent_label: "LOT-1"}, depth: 1, outsideParentId: false};
            assert.strictEqual(TeamDashboard.outsideParentOf(nested, taskMap), null);
        });

        QUnit.module("assignStageColors");

        QUnit.test("each open stage gets its own colour and closed stages are grey", function (assert) {
            var colors = TeamDashboard.assignStageColors([
                {id: 1, stage_id: 41, stage_name: "Backlog", stage_sequence: 4, is_closed: false},
                {id: 2, stage_id: 43, stage_name: "Development", stage_sequence: 35, is_closed: false},
                {id: 3, stage_id: 45, stage_name: "Delivered", stage_sequence: 54, is_closed: true},
                {id: 4, stage_id: 42, stage_name: "Specifications", stage_sequence: 1, is_closed: false},
            ]);
            assert.deepEqual(
                colors.map(function (c) { return c.name; }),
                ["Specifications", "Backlog", "Development", "Delivered"]
            );
            var solids = colors.map(function (c) { return c.solid; });
            assert.strictEqual(new Set(solids.slice(0, 3)).size, 3);
            assert.strictEqual(solids[3], "#aab7b8");
            assert.strictEqual(colors[0].light, "rgba(142,124,195,0.3)");
        });

        QUnit.module("elbowPath");

        QUnit.test("a later successor is reached by one step down", function (assert) {
            assert.strictEqual(TeamDashboard.elbowPath(100, 20, 200, 60), "M100,20 H110 V60 H200");
        });

        QUnit.test("an overlapping successor is reached around the gap between rows", function (assert) {
            assert.strictEqual(
                TeamDashboard.elbowPath(100, 20, 105, 60),
                "M100,20 H110 V40 H95 V60 H105"
            );
        });

        QUnit.module("buildStageHistoryRows");

        QUnit.test("each visit is a row chained to the next one", function (assert) {
            var history = TeamDashboard.buildStageHistoryRows({id: 7, stage_history: [
                {stage_id: 41, stage_name: "Backlog", is_closed: false,
                    start: "2026-09-17 02:00:00", end: "2026-09-19 02:46:00"},
                {stage_id: 43, stage_name: "Development", is_closed: false,
                    start: "2026-09-19 02:46:00", end: "2026-09-25 09:00:00"},
                {stage_id: 41, stage_name: "Backlog", is_closed: false,
                    start: "2026-09-25 09:00:00", end: "2026-10-13 12:00:00"},
            ]});
            assert.deepEqual(history.rows.map(function (r) { return r.id; }), ["sh_7_0", "sh_7_1", "sh_7_2"]);
            assert.deepEqual(history.rows.map(function (r) { return r.stageName; }),
                ["Backlog", "Development", "Backlog"]);
            assert.strictEqual(history.rows[1].end, "2026-09-25 09:00:00");
            assert.deepEqual(history.edges, [{from: "sh_7_0", to: "sh_7_1"}, {from: "sh_7_1", to: "sh_7_2"}]);
        });

        QUnit.test("a visit of a deleted stage is filed under the key of no stage", function (assert) {
            var history = TeamDashboard.buildStageHistoryRows({id: 8, stage_history: [
                {stage_id: false, stage_name: "Retired", is_closed: false,
                    start: "2026-09-17 02:00:00", end: "2026-09-19 02:46:00"},
                {stage_id: 43, stage_name: "Development", is_closed: false,
                    start: "2026-09-19 02:46:00", end: "2026-09-25 09:00:00"},
            ]});
            assert.deepEqual(history.rows.map(function (r) { return r.stageId; }), [0, 43]);
            assert.strictEqual(history.rows[0].stageName, "Retired");
        });

        QUnit.test("a visit shorter than a day is drawn one day wide", function (assert) {
            var history = TeamDashboard.buildStageHistoryRows({id: 9, stage_history: [
                {stage_id: 43, stage_name: "Development", is_closed: false,
                    start: "2026-09-17 02:46:00", end: "2026-09-17 02:47:00"},
                {stage_id: 45, stage_name: "Canceled", is_closed: true,
                    start: "2026-09-30 23:30:00", end: "2026-09-30 23:30:00"},
            ]});
            assert.strictEqual(history.rows[0].end, "2026-09-18 02:46:00");
            assert.strictEqual(history.rows[0].visitEnd, "2026-09-17 02:47:00");
            assert.strictEqual(history.rows[1].end, "2026-10-01 23:30:00");
            assert.ok(history.rows[1].isClosed);
        });

        QUnit.test("a task without history has no rows", function (assert) {
            assert.deepEqual(TeamDashboard.buildStageHistoryRows({id: 3}), {rows: [], edges: []});
            assert.deepEqual(TeamDashboard.buildStageHistoryRows({id: 3, stage_history: []}), {rows: [], edges: []});
        });

        QUnit.test("a scope keeps the rows of one task apart per user", function (assert) {
            var task = {id: 7, stage_history: [
                {stage_id: 1, stage_name: "Backlog", start: "2026-09-01 00:00:00", end: "2026-09-03 00:00:00"},
                {stage_id: 2, stage_name: "Development", start: "2026-09-03 00:00:00", end: "2026-09-08 00:00:00"},
            ]};
            var forAnna = TeamDashboard.buildStageHistoryRows(task, 5);
            var forBob = TeamDashboard.buildStageHistoryRows(task, 6);
            assert.deepEqual(forAnna.rows.map(function (r) { return r.id; }), ["sh_5_7_0", "sh_5_7_1"]);
            assert.deepEqual(forBob.edges, [{from: "sh_6_7_0", to: "sh_6_7_1"}]);
        });

        QUnit.module("stageHistorySpan");

        QUnit.test("the summary runs from the first stage entry to the end of the last visit", function (assert) {
            var span = TeamDashboard.stageHistorySpan({stage_history: [
                {stage_id: 1, start: "2026-08-27 03:24:39", end: "2026-09-02 10:00:00"},
                {stage_id: 2, start: "2026-09-02 10:00:00", end: "2026-10-14 08:00:00"},
            ]});
            assert.deepEqual(span, {start: "2026-08-27 03:24:39", end: "2026-10-14 08:00:00"});
        });

        QUnit.test("a short history still spans one day and no history gives no summary", function (assert) {
            var span = TeamDashboard.stageHistorySpan({stage_history: [
                {stage_id: 1, start: "2026-09-07 09:48:17", end: "2026-09-07 09:50:00"},
            ]});
            assert.strictEqual(span.end, "2026-09-08 09:48:17");
            assert.strictEqual(TeamDashboard.stageHistorySpan({stage_history: []}), null);
            assert.strictEqual(TeamDashboard.stageHistorySpan({}), null);
        });

        QUnit.module("findGanttTasks");

        QUnit.test("a key match ranks above a name match", function (assert) {
            var tasks = [
                {id: 1, key: "PRJ-20", name: "Fix PRJ-1684 regression"},
                {id: 2, key: "PRJ-1684", name: "Gantt improvements"},
                {id: 3, key: "PRJ-16841", name: "Other"},
                {id: 4, key: "PRJ-5", name: "Unrelated"},
            ];
            assert.deepEqual(TeamDashboard.findGanttTasks(tasks, " prj-1684 ").map(function (t) { return t.id; }),
                [2, 3, 1]);
        });

        QUnit.test("a name prefix ranks above a match inside the name", function (assert) {
            var tasks = [
                {id: 1, key: "", name: "Export the gantt"},
                {id: 2, key: false, name: "Gantt paging"},
            ];
            assert.deepEqual(TeamDashboard.findGanttTasks(tasks, "gantt").map(function (t) { return t.id; }), [2, 1]);
        });

        QUnit.test("an empty query finds nothing and the list is capped", function (assert) {
            var tasks = [];
            for (var i = 0; i < 12; i++) {
                tasks.push({id: i, key: "T-" + i, name: "Task"});
            }
            assert.deepEqual(TeamDashboard.findGanttTasks(tasks, "  "), []);
            assert.strictEqual(TeamDashboard.findGanttTasks(tasks, "task").length, 8);
        });

        QUnit.module("ganttPanBounds");

        QUnit.test("the chart can be panned a year beyond the tasks", function (assert) {
            var bounds = TeamDashboard.ganttPanBounds(new Date(2026, 6, 1), new Date(2026, 8, 15));
            assert.strictEqual(bounds.min.getTime(), new Date(2025, 6, 1).getTime());
            assert.strictEqual(bounds.max.getTime(), new Date(2027, 8, 15).getTime());
        });

        QUnit.module("utcDateTimeToDate");

        QUnit.test("a stored datetime is read as a UTC instant", function (assert) {
            var instant = TeamDashboard.utcDateTimeToDate("2026-11-17 00:00:00");
            assert.strictEqual(instant.getTime(), Date.UTC(2026, 10, 17, 0, 0, 0));
            assert.strictEqual(
                TeamDashboard.utcDateTimeToDate("2026-11-17").getTime(), Date.UTC(2026, 10, 17)
            );
        });

        QUnit.test("the tooltip day is the local day of the instant", function (assert) {
            var offsetMinutes = new Date(Date.UTC(2026, 10, 17)).getTimezoneOffset();
            // Next to UTC midnight on the side the runner's offset pushes across, so local and UTC days differ.
            var ms = offsetMinutes > 0 ? Date.UTC(2026, 10, 17, 0, 0) : Date.UTC(2026, 10, 16, 23, 59);
            var stored = TeamDashboard.formatUtcDateTime(ms);
            var localDate = new Date(ms);
            var dayParts = TeamDashboard.utcDateTimeToLocalDay(stored).split("-").map(Number);
            assert.deepEqual(dayParts, [localDate.getFullYear(), localDate.getMonth() + 1, localDate.getDate()]);
            assert.notStrictEqual(offsetMinutes, 0, "the browser runs outside UTC");
            assert.notStrictEqual(TeamDashboard.utcDateTimeToLocalDay(stored), stored.substring(0, 10),
                "the tooltip shows the local day, not the UTC day");
        });

        QUnit.test("a UTC datetime round-trips through a Date without drift", function (assert) {
            var dayMs = 86400000;
            var start = TeamDashboard.utcDateTimeToDate("2026-11-12 01:00:00");
            var end = TeamDashboard.utcDateTimeToDate("2026-11-17 00:00:00");
            assert.strictEqual(TeamDashboard.formatUtcDateTime(start.getTime()), "2026-11-12 01:00:00");
            assert.strictEqual(TeamDashboard.formatUtcDateTime(start.getTime() + 2 * dayMs), "2026-11-14 01:00:00");
            assert.strictEqual(TeamDashboard.formatUtcDateTime(end.getTime() + 2 * dayMs), "2026-11-19 00:00:00");
            var saved = TeamDashboard.formatUtcDateTime(start.getTime() + 2 * dayMs);
            var resaved = TeamDashboard.formatUtcDateTime(TeamDashboard.utcDateTimeToDate(saved).getTime());
            assert.strictEqual(resaved, saved, "a second load and save does not drift");
        });

        QUnit.module("ganttItem");

        function fakeDashboard() {
            var dashboard = Object.create(TeamDashboard.prototype);
            dashboard._ganttStageColors = {};
            dashboard._ganttCanLink = false;
            return dashboard;
        }

        var ganttTask = {
            id: 11, key: "T-11", name: "Layer", stage_id: false, stage_name: "Backlog", can_write: true,
            planned_hours: 0, planned_date_start: "2026-11-12 01:00:00", planned_date_end: "2026-11-17 00:00:00",
        };

        QUnit.test("a range bar is drawn at the stored UTC instants", function (assert) {
            var item = fakeDashboard()._ganttItem(ganttTask, "", false);
            assert.ok(item.start instanceof Date && item.end instanceof Date);
            assert.strictEqual(item.start.getTime(), Date.UTC(2026, 10, 12, 1));
            assert.strictEqual(item.end.getTime(), Date.UTC(2026, 10, 17));
        });

        QUnit.test("a milestone point is drawn at its stored UTC start", function (assert) {
            var milestone = _.extend({}, ganttTask, {task_type: "milestone"});
            var item = fakeDashboard()._ganttItem(milestone, "", false);
            assert.strictEqual(item.type, "point");
            assert.ok(item.start instanceof Date);
            assert.strictEqual(item.start.getTime(), Date.UTC(2026, 10, 12, 1));
        });

        QUnit.module("onGanttItemMove");

        QUnit.test("a dragged bar is saved as UTC datetimes", async function (assert) {
            var rpc = require("web.rpc");
            var originalQuery = rpc.query;
            var written = [];
            rpc.query = function (params) {
                written.push(params);
                return Promise.resolve(true);
            };
            try {
                var dashboard = fakeDashboard();
                dashboard._loadGanttData = function () {};
                var item = {id: 11, start: new Date(Date.UTC(2026, 10, 14, 1)), end: new Date(Date.UTC(2026, 10, 19))};
                var saved = await new Promise(function (resolve) {
                    dashboard._onGanttItemMove(item, resolve);
                });
                assert.strictEqual(saved, item);
                assert.deepEqual(written.map(function (call) { return call.args; }), [[[11], {
                    planned_date_start: "2026-11-14 01:00:00",
                    planned_date_end: "2026-11-19 00:00:00",
                }]]);
                assert.notStrictEqual(item.start.getTimezoneOffset(), 0, "the browser runs outside UTC");
            } finally {
                rpc.query = originalQuery;
            }
        });

        QUnit.module("keepOpenTaskOutOfGroupToggle", function () {
            QUnit.test("a press on the open-task icon never reaches the row label, the click still does", function (assert) {
                var container = document.createElement("div");
                container.innerHTML = '<div class="vis-label"><span class="o_gantt_open_task"><i class="fa fa-external-link"></i></span><span class="o_name">Task</span></div>';
                document.body.appendChild(container);
                try {
                    var label = container.querySelector(".vis-label");
                    var reached = [];
                    ["pointerdown", "mousedown", "touchstart", "click"].forEach(function (eventName) {
                        label.addEventListener(eventName, function () { reached.push(eventName); });
                    });
                    var registered = [];
                    var addEventListener = container.addEventListener.bind(container);
                    container.addEventListener = function (eventName, listener, options) {
                        registered.push(eventName);
                        addEventListener(eventName, listener, options);
                    };
                    TeamDashboard.keepOpenTaskOutOfGroupToggle(container);
                    TeamDashboard.keepOpenTaskOutOfGroupToggle(container);
                    assert.deepEqual(registered, ["pointerdown", "mousedown", "touchstart"]);
                    var icon = container.querySelector(".o_gantt_open_task i");
                    ["pointerdown", "mousedown", "touchstart", "click"].forEach(function (eventName) {
                        icon.dispatchEvent(new Event(eventName, {bubbles: true}));
                    });
                    assert.deepEqual(reached, ["click"]);
                    container.querySelector(".o_name").dispatchEvent(new Event("pointerdown", {bubbles: true}));
                    assert.deepEqual(reached, ["click", "pointerdown"]);
                } finally {
                    container.remove();
                }
            });
        });

        QUnit.module("shiftByWholeDays", function () {
            QUnit.test("a drag moves by whole days and keeps the time of day", function (assert) {
                var start = new Date(Date.UTC(2026, 10, 12, 1));
                var end = new Date(Date.UTC(2026, 10, 15, 10));
                var dragged = 2 * 86400000 + 5 * 3600000;
                assert.strictEqual(TeamDashboard.shiftByWholeDays(start, new Date(start.getTime() + dragged)).getTime(), Date.UTC(2026, 10, 14, 1));
                assert.strictEqual(TeamDashboard.shiftByWholeDays(end, new Date(end.getTime() + dragged)).getTime(), Date.UTC(2026, 10, 17, 10));
                assert.strictEqual(TeamDashboard.shiftByWholeDays(start, new Date(start.getTime() + 3 * 3600000)).getTime(), start.getTime());
                var moved = new Date(Date.UTC(2026, 10, 20));
                assert.strictEqual(TeamDashboard.shiftByWholeDays(undefined, moved), moved);
            });

            QUnit.test("a dragged bar is saved with its hours kept", async function (assert) {
                var rpc = require("web.rpc");
                var originalQuery = rpc.query;
                var written = [];
                rpc.query = function (params) {
                    written.push(params.args);
                    return Promise.resolve(true);
                };
                try {
                    var dashboard = Object.create(TeamDashboard.prototype);
                    dashboard._loadGanttData = function () {};
                    var stored = {id: 21, start: new Date(Date.UTC(2026, 10, 12, 1)), end: new Date(Date.UTC(2026, 10, 15, 10))};
                    dashboard._ganttItemsDS = {get: function (id) { return id === stored.id ? stored : null; }};
                    var dragged = 2 * 86400000 + 15 * 3600000;
                    var item = {id: 21, start: new Date(stored.start.getTime() + dragged), end: new Date(stored.end.getTime() + dragged)};
                    await new Promise(function (resolve) {
                        dashboard._onGanttItemMove(item, resolve);
                    });
                    assert.deepEqual(written, [[[21], {
                        planned_date_start: "2026-11-15 01:00:00",
                        planned_date_end: "2026-11-18 10:00:00",
                    }]]);
                } finally {
                    rpc.query = originalQuery;
                }
            });
        });

        QUnit.module("team pickers", function () {
            var rpc = require("web.rpc");

            function pickerDashboard(mode) {
                var dashboard = Object.create(TeamDashboard.prototype);
                dashboard.$el = $('<div><input class="o_user_search_input"/><input class="o_dept_search_input"/></div>');
                dashboard._renderDashboard = function () {};
                return Object.assign(dashboard, {
                    dashboardMode: mode,
                    period: "this_week",
                    maxDepth: 0,
                    managerId: 2,
                    managerName: "Me",
                    departmentId: 3,
                    departmentName: "Own Department",
                });
            }

            function pick(attribute, id, name) {
                return {preventDefault: function () {}, currentTarget: $("<a/>").attr(attribute, id).text(name)[0]};
            }

            async function withServer(answer, callback) {
                var originalQuery = rpc.query;
                var queriedIds = [];
                rpc.query = function (params) {
                    queriedIds.push(params.args[0]);
                    return answer();
                };
                try {
                    await callback(queriedIds);
                } finally {
                    rpc.query = originalQuery;
                }
            }

            function refuse() {
                return Promise.reject({message: {data: {name: "odoo.exceptions.AccessError"}}});
            }

            function ignoreRefusal() {}

            QUnit.test("a refused manager gives the picker and later reloads back to the previous one", async function (assert) {
                var dashboard = pickerDashboard("manager");
                await withServer(refuse, async function (queriedIds) {
                    await dashboard._onUserSearchSelect(pick("data-user-id", 7, "Boss")).catch(ignoreRefusal);
                    await dashboard._loadDashboard().catch(ignoreRefusal);
                    assert.deepEqual(queriedIds, [7, 2]);
                });
                assert.strictEqual(dashboard.managerName, "Me");
                assert.strictEqual(dashboard.$(".o_user_search_input").val(), "Me");
            });

            QUnit.test("a refused department gives the picker and later reloads back to the previous one", async function (assert) {
                var dashboard = pickerDashboard("department");
                await withServer(refuse, async function (queriedIds) {
                    await dashboard._onDeptSearchSelect(pick("data-dept-id", 9, "Other Department")).catch(ignoreRefusal);
                    await dashboard._loadDashboard().catch(ignoreRefusal);
                    assert.deepEqual(queriedIds, [9, 3]);
                });
                assert.strictEqual(dashboard.departmentName, "Own Department");
                assert.strictEqual(dashboard.$(".o_dept_search_input").val(), "Own Department");
            });

            QUnit.test("an opened manager and department stay picked", async function (assert) {
                var dashboard = pickerDashboard("manager");
                await withServer(function () { return Promise.resolve({members: []}); }, async function () {
                    await dashboard._onUserSearchSelect(pick("data-user-id", 7, "Boss"));
                    dashboard.dashboardMode = "department";
                    await dashboard._onDeptSearchSelect(pick("data-dept-id", 9, "Other Department"));
                });
                assert.deepEqual([dashboard.managerId, dashboard.$(".o_user_search_input").val()], [7, "Boss"]);
                assert.deepEqual([dashboard.departmentId, dashboard.$(".o_dept_search_input").val()], [9, "Other Department"]);
            });
        });
    });
});
