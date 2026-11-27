odoo.define("project_team_dashboard.team_dashboard_tests", function (require) {
    "use strict";

    var TeamDashboard = require("project_team_dashboard.team_dashboard");
    var busTestEnv = require("@bus/../tests/helpers/mock_python_environment");
    var ajax = require("web.ajax");
    var AjaxService = require("web.AjaxService");
    var session = require("web.session");
    var Widget = require("web.Widget");
    var registerCleanup = require("@web/../tests/helpers/cleanup").registerCleanup;
    var testUtils = require("@web/../tests/helpers/utils");

    var userContext = {allowed_company_ids: [3], lang: "en_US", tz: "Asia/Tokyo", uid: 2};

    /**
     * A dashboard of the user 2 whose calls reach the server as in the web client: through the
     * ajax service and the session, which adds the user's context.
     */
    function newDashboard(html) {
        var ajaxService = new AjaxService();
        var Parent = Widget.extend({
            custom_events: {
                call_service: function (ev) {
                    if (ev.data.service === "ajax") {
                        ev.data.callback(ajaxService[ev.data.method].apply(ajaxService, ev.data.args));
                    }
                },
                get_session: function (ev) {
                    ev.data.callback({uid: 2});
                },
            },
        });
        var parent = new Parent();
        registerCleanup(function () {
            parent.destroy();
        });
        var dashboard = new TeamDashboard(parent, {});
        dashboard.setElement($(html || '<div><div class="o_team_dashboard_content"/></div>'));
        return dashboard;
    }

    /** Answer each call sent to the server with `answer(params)` and return the list the calls are pushed to. */
    function serveCalls(answer) {
        var sentCalls = [];
        function send(params) {
            sentCalls.push(params);
            var answered = Promise.resolve().then(function () {
                return answer(params);
            });
            answered.abort = function () {};
            return answered;
        }
        testUtils.patchWithCleanup(session, {user_context: userContext});
        testUtils.patchWithCleanup(ajax, {
            jsonRpc: function (url, fctName, params) {
                return send(params);
            },
            // web.rpc's path, which adds no context: a call sent that way shows here without one
            rpc: function (url, params) {
                return send(params);
            },
        });
        return sentCalls;
    }

    QUnit.module("project_team_dashboard", {
        // The bus test helpers fetch model definitions when the run begins. Run alone, these
        // tests end before that request does, and leaving the page then aborts it: a global
        // failure. Waiting for it here keeps the run whole.
        before: function () {
            return busTestEnv.startServer();
        },
    }, function () {
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

        QUnit.module("findBarOverlaps");

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
            var overlapping = TeamDashboard.findBarOverlaps([
                {id: "p", taskId: 1, start: "2026-03-01", end: "2026-03-20"},
                {id: "c", taskId: 2, start: "2026-03-02", end: "2026-03-05"},
                {id: "g", taskId: 3, start: "2026-03-03", end: "2026-03-04"},
            ], familyTaskMap);
            assert.deepEqual(overlapping, {});
        });

        QUnit.test("unrelated overlapping tasks are flagged with the tasks they overlap", function (assert) {
            var overlapping = TeamDashboard.findBarOverlaps([
                {id: "p", taskId: 1, start: "2026-03-01", end: "2026-03-20"},
                {id: "c", taskId: 2, start: "2026-03-02", end: "2026-03-05"},
                {id: "o", taskId: 4, start: "2026-03-04", end: "2026-03-06"},
            ], familyTaskMap);
            assert.deepEqual(overlapping, {p: [4], c: [4], o: [1, 2]});
        });

        QUnit.test("tasks that only touch do not overlap", function (assert) {
            var overlapping = TeamDashboard.findBarOverlaps([
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

        function ganttDashboard() {
            var dashboard = newDashboard();
            dashboard._ganttStageColors = {};
            dashboard._ganttCanLink = false;
            dashboard._loadGanttData = function () {};
            return dashboard;
        }

        function itemStore(items) {
            return {get: function (id) { return _.findWhere(items, {id: id}) || null; }};
        }

        var ganttTask = {
            id: 11, key: "T-11", name: "Layer", stage_id: false, stage_name: "Backlog", can_write: true,
            planned_hours: 0, planned_date_start: "2026-11-12 01:00:00", planned_date_end: "2026-11-17 00:00:00",
            has_planned_date_start: true, has_planned_date_end: true,
        };

        QUnit.test("a range bar is drawn at the stored UTC instants", function (assert) {
            var item = ganttDashboard()._ganttItem(ganttTask, "", false);
            assert.ok(item.start instanceof Date && item.end instanceof Date);
            assert.strictEqual(item.start.getTime(), Date.UTC(2026, 10, 12, 1));
            assert.strictEqual(item.end.getTime(), Date.UTC(2026, 10, 17));
        });

        QUnit.test("a milestone point is drawn at its stored UTC start", function (assert) {
            var milestone = _.extend({}, ganttTask, {task_type: "milestone"});
            var item = ganttDashboard()._ganttItem(milestone, "", false);
            assert.strictEqual(item.type, "point");
            assert.ok(item.start instanceof Date);
            assert.strictEqual(item.start.getTime(), Date.UTC(2026, 10, 12, 1));
        });

        QUnit.module("onGanttItemMove");

        function sentArgs(sentCalls) {
            return sentCalls.map(function (params) { return params.args; });
        }

        function sentCompanies(sentCalls) {
            return sentCalls.map(function (params) { return (params.kwargs.context || {}).allowed_company_ids; });
        }

        function acceptWrite() {
            return Promise.resolve(true);
        }

        QUnit.test("a dragged bar is saved as UTC datetimes", async function (assert) {
            var sentCalls = serveCalls(acceptWrite);
            var dashboard = ganttDashboard();
            dashboard.ganttData = {tasks: [ganttTask]};
            dashboard._ganttItemsDS = itemStore([dashboard._ganttItem(ganttTask, "", false)]);
            var item = {id: 11, start: new Date(Date.UTC(2026, 10, 14, 1)), end: new Date(Date.UTC(2026, 10, 19))};
            var saved = await new Promise(function (resolve) {
                dashboard._onGanttItemMove(item, resolve);
            });
            assert.strictEqual(saved, item);
            assert.deepEqual(sentArgs(sentCalls), [[[11], {
                planned_date_start: "2026-11-14 01:00:00",
                planned_date_end: "2026-11-19 00:00:00",
            }]]);
            assert.deepEqual(sentCompanies(sentCalls), [[3]], "the write is sent in the selected companies");
            assert.notStrictEqual(item.start.getTimezoneOffset(), 0, "the browser runs outside UTC");
        });

        var dayMs = 86400000;

        /** Drag the drawn bar of `task` by `startShift`/`endShift` ms and return what it saves. */
        async function savedOnDrag(task, startShift, endShift) {
            var sentCalls = serveCalls(acceptWrite);
            var dashboard = ganttDashboard();
            dashboard.ganttData = {tasks: [task]};
            var drawn = dashboard._ganttItem(task, "", false);
            dashboard._ganttItemsDS = itemStore([drawn]);
            var dragged = _.extend({}, drawn, {start: new Date(drawn.start.getTime() + startShift)});
            if (drawn.end) {
                dragged.end = new Date(drawn.end.getTime() + endShift);
            }
            await new Promise(function (resolve) {
                dashboard._onGanttItemMove(dragged, resolve);
            });
            return sentArgs(sentCalls);
        }

        function plannedTask(values) {
            return _.extend({}, ganttTask, {id: 31}, values);
        }

        QUnit.test("a milestone dragged later moves its end with its start", async function (assert) {
            var milestone = plannedTask({
                task_type: "milestone",
                planned_date_start: "2026-11-12 01:00:00",
                planned_date_end: "2026-11-12 01:00:00",
            });
            assert.deepEqual(await savedOnDrag(milestone, 3 * dayMs + 2 * 3600000), [[[31], {
                planned_date_start: "2026-11-15 01:00:00",
                planned_date_end: "2026-11-15 01:00:00",
            }]]);
        });

        QUnit.test("a milestone dragged earlier keeps its length", async function (assert) {
            var milestone = plannedTask({
                task_type: "milestone",
                planned_date_start: "2026-11-12 01:00:00",
                planned_date_end: "2026-11-14 18:00:00",
            });
            assert.deepEqual(await savedOnDrag(milestone, -2 * dayMs), [[[31], {
                planned_date_start: "2026-11-10 01:00:00",
                planned_date_end: "2026-11-12 18:00:00",
            }]]);
        });

        QUnit.test("a milestone planned by one date saves that date alone", async function (assert) {
            var startOnly = plannedTask({
                task_type: "milestone",
                planned_date_start: "2026-11-12 01:00:00",
                planned_date_end: "2026-11-13 01:00:00",
                has_planned_date_end: false,
            });
            assert.deepEqual(await savedOnDrag(startOnly, 2 * dayMs), [[[31], {
                planned_date_start: "2026-11-14 01:00:00",
            }]], "a milestone planned by its start");
            var endOnly = plannedTask({
                task_type: "milestone",
                planned_date_start: "2026-11-16 00:00:00",
                planned_date_end: "2026-11-17 00:00:00",
                has_planned_date_start: false,
            });
            assert.deepEqual(await savedOnDrag(endOnly, -dayMs), [[[31], {
                planned_date_end: "2026-11-16 00:00:00",
            }]], "a milestone planned by its end");
        });

        QUnit.test("a moved task planned by its start alone saves its start alone", async function (assert) {
            var startOnly = plannedTask({
                planned_date_start: "2026-11-12 01:00:00",
                planned_date_end: "2026-11-13 01:00:00",
                has_planned_date_end: false,
            });
            assert.deepEqual(await savedOnDrag(startOnly, 2 * dayMs, 2 * dayMs), [[[31], {
                planned_date_start: "2026-11-14 01:00:00",
            }]]);
        });

        QUnit.test("a moved task planned by its end alone saves its end alone", async function (assert) {
            var endOnly = plannedTask({
                planned_date_start: "2026-11-16 00:00:00",
                planned_date_end: "2026-11-17 00:00:00",
                has_planned_date_start: false,
            });
            assert.deepEqual(await savedOnDrag(endOnly, -dayMs, -dayMs), [[[31], {
                planned_date_end: "2026-11-16 00:00:00",
            }]]);
        });

        QUnit.test("a resized task saves the drawn edge it dragged and not the one it left", async function (assert) {
            var startOnly = plannedTask({
                planned_date_start: "2026-11-12 01:00:00",
                planned_date_end: "2026-11-13 01:00:00",
                has_planned_date_end: false,
            });
            assert.deepEqual(await savedOnDrag(startOnly, 0, 3 * dayMs), [[[31], {
                planned_date_start: "2026-11-12 01:00:00",
                planned_date_end: "2026-11-16 01:00:00",
            }]], "the end the user dragged out is saved");
            assert.deepEqual(await savedOnDrag(startOnly, -dayMs, 0), [[[31], {
                planned_date_start: "2026-11-11 01:00:00",
            }]], "the drawn end the user left is not saved");
            var endOnly = plannedTask({
                planned_date_start: "2026-11-16 00:00:00",
                planned_date_end: "2026-11-17 00:00:00",
                has_planned_date_start: false,
            });
            assert.deepEqual(await savedOnDrag(endOnly, -2 * dayMs, 0), [[[31], {
                planned_date_start: "2026-11-14 00:00:00",
                planned_date_end: "2026-11-17 00:00:00",
            }]], "the start the user dragged out is saved");
            assert.deepEqual(await savedOnDrag(endOnly, 0, dayMs), [[[31], {
                planned_date_end: "2026-11-18 00:00:00",
            }]], "the drawn start the user left is not saved");
        });

        QUnit.module("ganttDependencyWrites");

        /** The method, arguments and companies of each call sent. */
        function sentWrites(sentCalls) {
            return sentCalls.map(function (params) {
                return [params.method, params.args, (params.kwargs.context || {}).allowed_company_ids];
            });
        }

        /** A Gantt dashboard of the tasks 11 and 12, whose reload after a write resolves `reloaded`. */
        function dependencyDashboard() {
            var dashboard = ganttDashboard();
            dashboard.setElement($('<div><input class="o_gantt_dep_lag_input" value="2"/></div>'));
            dashboard.ganttData = {tasks: [ganttTask, _.extend({}, ganttTask, {id: 12, key: "T-12", depend_on_ids: []})]};
            dashboard.reloaded = new Promise(function (resolve) {
                dashboard._loadGanttData = resolve;
            });
            return dashboard;
        }

        QUnit.test("a dependency linked on the chart is saved in the selected companies", async function (assert) {
            var sentCalls = serveCalls(acceptWrite);
            var dashboard = dependencyDashboard();
            dashboard._ganttLinkTasks(11, 12);
            await dashboard.reloaded;
            assert.deepEqual(sentWrites(sentCalls), [["gantt_add_dependency", [[12], 11], [3]]]);
        });

        QUnit.test("a dependency lag is saved in the selected companies", async function (assert) {
            var sentCalls = serveCalls(acceptWrite);
            var dashboard = dependencyDashboard();
            dashboard._ganttPopoverEdge = {pred: 11, succ: 12};
            dashboard._onGanttDependencySaveLag();
            await dashboard.reloaded;
            assert.deepEqual(sentWrites(sentCalls), [["gantt_set_dependency_lag", [[12], 11, 2], [3]]]);
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
                var sentCalls = serveCalls(acceptWrite);
                var dashboard = ganttDashboard();
                dashboard.ganttData = {tasks: [{id: 21, has_planned_date_start: true, has_planned_date_end: true}]};
                var stored = {id: 21, start: new Date(Date.UTC(2026, 10, 12, 1)), end: new Date(Date.UTC(2026, 10, 15, 10))};
                dashboard._ganttItemsDS = itemStore([stored]);
                var dragged = 2 * 86400000 + 15 * 3600000;
                var item = {id: 21, start: new Date(stored.start.getTime() + dragged), end: new Date(stored.end.getTime() + dragged)};
                await new Promise(function (resolve) {
                    dashboard._onGanttItemMove(item, resolve);
                });
                assert.deepEqual(sentArgs(sentCalls), [[[21], {
                    planned_date_start: "2026-11-15 01:00:00",
                    planned_date_end: "2026-11-18 10:00:00",
                }]]);
            });
        });

        QUnit.module("team pickers", function () {
            function pickerDashboard(mode) {
                var dashboard = newDashboard('<div><input class="o_user_search_input"/><input class="o_dept_search_input"/></div>');
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

            function queriedIds(sentCalls) {
                return sentCalls.map(function (params) { return params.args[0]; });
            }

            function refuse() {
                return Promise.reject({message: {data: {name: "odoo.exceptions.AccessError"}}});
            }

            function ignoreRefusal() {}

            QUnit.test("a refused manager gives the picker and later reloads back to the previous one", async function (assert) {
                var sentCalls = serveCalls(refuse);
                var dashboard = pickerDashboard("manager");
                await dashboard._onUserSearchSelect(pick("data-user-id", 7, "Boss")).catch(ignoreRefusal);
                await dashboard._loadDashboard().catch(ignoreRefusal);
                assert.deepEqual(queriedIds(sentCalls), [7, 2]);
                assert.strictEqual(dashboard.managerName, "Me");
                assert.strictEqual(dashboard.$(".o_user_search_input").val(), "Me");
            });

            QUnit.test("a refused department gives the picker and later reloads back to the previous one", async function (assert) {
                var sentCalls = serveCalls(refuse);
                var dashboard = pickerDashboard("department");
                await dashboard._onDeptSearchSelect(pick("data-dept-id", 9, "Other Department")).catch(ignoreRefusal);
                await dashboard._loadDashboard().catch(ignoreRefusal);
                assert.deepEqual(queriedIds(sentCalls), [9, 3]);
                assert.strictEqual(dashboard.departmentName, "Own Department");
                assert.strictEqual(dashboard.$(".o_dept_search_input").val(), "Own Department");
            });

            QUnit.test("an opened manager and department stay picked", async function (assert) {
                serveCalls(function () { return Promise.resolve({members: []}); });
                var dashboard = pickerDashboard("manager");
                await dashboard._onUserSearchSelect(pick("data-user-id", 7, "Boss"));
                dashboard.dashboardMode = "department";
                await dashboard._onDeptSearchSelect(pick("data-dept-id", 9, "Other Department"));
                assert.deepEqual([dashboard.managerId, dashboard.$(".o_user_search_input").val()], [7, "Boss"]);
                assert.deepEqual([dashboard.departmentId, dashboard.$(".o_dept_search_input").val()], [9, "Other Department"]);
            });
        });

        QUnit.module("team loads", function () {
            function sentContexts(sentCalls) {
                return sentCalls.map(function (params) {
                    return [params.model + "." + params.method, params.kwargs.context];
                });
            }

            function renderedDashboard(mode) {
                var dashboard = newDashboard();
                Object.assign(dashboard, {
                    dashboardMode: mode,
                    viewMode: "cards",
                    period: "this_week",
                    maxDepth: 0,
                    projects: [{id: 1, name: "Own Project", key: false}],
                    projectId: 1,
                    managerUsers: [{id: 2, name: "Me"}],
                    managerId: 2,
                    managerName: "Me",
                    departments: [
                        {id: 3, name: "Own Department", complete_name: "Own Department"},
                        {id: 9, name: "Other Department", complete_name: "Other Department"},
                    ],
                    departmentId: 3,
                    departmentName: "Own Department",
                });
                dashboard._renderDashboard();
                return dashboard;
            }

            QUnit.test("the start loads the projects, the pickers and the defaults in the user's context", async function (assert) {
                var answers = {
                    "project.project.search_read": [{id: 1, name: "Own Project", key: false}],
                    "res.users.search_read": [{id: 2, name: "Me"}],
                    "hr.department.search_read": [{id: 3, name: "Own Department", complete_name: "Own Department"}],
                    "hr.employee.search_read": [{id: 4, department_id: [3, "Own Department"]}],
                    "res.users.read": [{id: 2, dashboard_default_project_id: false}],
                };
                var sentCalls = serveCalls(function (params) {
                    return Promise.resolve(answers[params.model + "." + params.method]);
                });
                var dashboard = newDashboard();
                await dashboard.willStart();
                assert.deepEqual(sentContexts(sentCalls), Object.keys(answers).map(function (call) {
                    return [call, userContext];
                }));
                assert.deepEqual([dashboard.managerName, dashboard.departmentName, dashboard.projectId], ["Me", "Own Department", 1]);
            });

            QUnit.test("a default project among the projects found opens, another gives way to the first one", async function (assert) {
                var savedDefault = [5, "Second Project"];
                serveCalls(function () {
                    return Promise.resolve([{id: 2, dashboard_default_project_id: savedDefault}]);
                });
                var dashboard = newDashboard();
                dashboard.projects = [{id: 1, name: "Own Project", key: false}, {id: 5, name: "Second Project", key: false}];
                await dashboard._loadUserDefaultProject();
                assert.strictEqual(dashboard.projectId, 5, "the default project found");
                savedDefault = [8, "Project of a company not selected"];
                await dashboard._loadUserDefaultProject();
                assert.strictEqual(dashboard.projectId, 1, "the first project found");
            });

            QUnit.test("the Gantt loads its tasks in the user's context", async function (assert) {
                var sentCalls = serveCalls(function () { return Promise.resolve({tasks: [], columns: []}); });
                var dashboard = renderedDashboard("project");
                dashboard._renderGantt = function () {};
                await dashboard._loadGanttData();
                assert.deepEqual(
                    sentCalls.map(function (params) {
                        return [params.method, params.kwargs.project_ids, (params.kwargs.context || {}).allowed_company_ids];
                    }),
                    [["get_gantt_chart_data", [1], [3]]]
                );
            });

            QUnit.test("each mode loads its team in the user's context", async function (assert) {
                var sentCalls = serveCalls(function () { return Promise.resolve({members: []}); });
                var dashboard = renderedDashboard("project");
                for (var mode of ["project", "manager", "department"]) {
                    dashboard.dashboardMode = mode;
                    await dashboard._loadDashboard();
                }
                assert.deepEqual(sentContexts(sentCalls), [
                    ["project.project.get_team_dashboard_data", userContext],
                    ["project.project.get_team_dashboard_by_manager", userContext],
                    ["project.project.get_team_dashboard_by_department", userContext],
                ]);
            });

            QUnit.test("a team answered with an error keeps the toolbar and the picker of its mode", async function (assert) {
                var errors = {
                    get_team_dashboard_by_manager: "No employee record found for this user in the selected companies",
                    get_team_dashboard_by_department: "Department not in the selected companies",
                };
                serveCalls(function (params) { return Promise.resolve({error: errors[params.method]}); });
                for (var [mode, pickerSelector, error] of [
                    ["manager", ".o_user_search_input", errors.get_team_dashboard_by_manager],
                    ["department", ".o_dept_search_input", errors.get_team_dashboard_by_department],
                ]) {
                    var dashboard = renderedDashboard(mode);
                    await dashboard._loadDashboard();
                    assert.deepEqual(
                        [
                            dashboard.$(".o_dashboard_mode_btn").length,
                            dashboard.$(".o_dashboard_mode_btn.btn-primary").data("mode"),
                            dashboard.$(pickerSelector).length,
                            dashboard.$(".o_team_dashboard_refresh").length,
                            dashboard.$(".o_team_dashboard_grid .alert-warning").text(),
                        ],
                        [3, mode, 1, 1, error],
                        mode
                    );
                }
            });

            QUnit.test("the filter bar of every mode, the Gantt toolbar and its legend fit a phone's width", async function (assert) {
                var phoneScreen = document.createElement("div");
                phoneScreen.style.width = "360px";
                document.body.appendChild(phoneScreen);
                registerCleanup(function () {
                    phoneScreen.remove();
                });
                for (var mode of ["project", "manager", "department"]) {
                    var dashboard = renderedDashboard(mode);
                    phoneScreen.replaceChildren(dashboard.el);
                    var filterBar = dashboard.el.querySelector(".o_dashboard_filter_bar");
                    assert.ok(filterBar.scrollWidth <= filterBar.clientWidth, mode + ": " + filterBar.scrollWidth + "px");
                }
                // one word each: a label of several words breaks over two lines rather than overflow
                var stageNames = ["Specifications", "Implementation", "Documentation", "Verification"];
                serveCalls(function () {
                    return Promise.resolve({
                        tasks: stageNames.map(function (stageName, index) {
                            return {
                                id: index + 1, key: "T-" + (index + 1), name: "Task in " + stageName, parent_id: false,
                                stage_id: 40 + index, stage_name: stageName, stage_sequence: index, is_closed: false,
                                planned_date_start: "2026-11-0" + (index + 1) + " 01:00:00",
                                planned_date_end: "2026-11-1" + index + " 00:00:00",
                                has_planned_date_start: true, has_planned_date_end: true,
                                user_list: [], user_names: "", depend_on_ids: [], can_write: false,
                                planned_hours: 0, remaining_hours: 0, progress: 0,
                            };
                        }),
                        tasks_without_dates: 12,
                        unscheduled: [],
                        columns: [],
                        today: "2026-11-05",
                    });
                });
                var ganttDashboard = renderedDashboard("project");
                // a tag shrinks no narrower than its longest word: this one leaves the search too little room uncapped
                ganttDashboard.projects = [{id: 1, name: "Manufacturing", key: false}];
                phoneScreen.replaceChildren(ganttDashboard.el);
                ganttDashboard._onViewGantt();
                await ganttDashboard._loadGanttData();
                assert.strictEqual(ganttDashboard.$(".o_gantt_multiproject_tags .o_project_tag").text().trim(), "Manufacturing");
                assert.deepEqual(
                    ganttDashboard.$(".o_gantt_legend_stages > span").toArray().map(function (stage) { return stage.textContent; }),
                    stageNames
                );
                assert.strictEqual(ganttDashboard.$(".o_gantt_no_dates_count:not(.d-none)").text(), "12 tasks without dates");
                for (var selector of [".o_gantt_toolbar", ".o_gantt_legend"]) {
                    var element = ganttDashboard.el.querySelector(selector);
                    assert.ok(
                        element.clientWidth > 0 && element.scrollWidth <= element.clientWidth,
                        selector + ": " + element.scrollWidth + "px in " + element.clientWidth + "px"
                    );
                }
            });

            QUnit.test("grouped by user, an overlapping bar names the tasks it overlaps and the legend explains the outline", async function (assert) {
                var person = {id: 7, name: "Person"};
                function plannedTask(id, key, start, end, parentId) {
                    return {
                        id: id, key: key, name: "Task " + key, parent_id: parentId || false,
                        stage_id: 40, stage_name: "Doing", stage_sequence: 1, is_closed: false,
                        planned_date_start: start + " 01:00:00", planned_date_end: end + " 00:00:00",
                        has_planned_date_start: true, has_planned_date_end: true,
                        user_list: [person], user_names: person.name, depend_on_ids: [], can_write: false,
                        planned_hours: 0, remaining_hours: 0, progress: 0,
                    };
                }
                serveCalls(function () {
                    return Promise.resolve({
                        tasks: [
                            plannedTask(1, "T-1", "2026-11-02", "2026-11-10"),
                            plannedTask(2, "T-2", "2026-11-04", "2026-11-06", 1),
                            plannedTask(3, "T-3", "2026-11-05", "2026-11-12"),
                            plannedTask(4, "T-4", "2026-11-12", "2026-11-14"),
                        ],
                        tasks_without_dates: 0,
                        unscheduled: [],
                        columns: [],
                        today: "2026-11-05",
                    });
                });
                var dashboard = renderedDashboard("project");
                var screen = document.createElement("div");
                document.body.appendChild(screen);
                registerCleanup(function () {
                    screen.remove();
                });
                screen.replaceChildren(dashboard.el);
                dashboard._onViewGantt();
                await dashboard._loadGanttData();
                var overlapLines = {};
                dashboard._ganttItemsDS.get().forEach(function (item) {
                    if (/gantt-overlap/.test(item.className)) {
                        overlapLines[item.id] = item.title.split("<br/>").pop();
                    }
                });
                assert.deepEqual(overlapLines, {
                    ut_7_1: "Overlaps: T-3",
                    ut_7_2: "Overlaps: T-3",
                    ut_7_3: "Overlaps: T-1, T-2",
                }, "neither a sub-task inside its parent nor a task starting after another ends is an overlap");
                var legendItem = dashboard.$(".o_gantt_legend_overlap_item");
                assert.notOk(legendItem.hasClass("d-none"), "the legend shows the outline where bars carry it");
                assert.ok(/overlap another task of the same person/.test(legendItem.attr("title")));
                dashboard.ganttGroupBy = "task";
                dashboard._renderGantt();
                assert.ok(legendItem.hasClass("d-none"), "grouped by task no bar is outlined, nor is the legend");
            });

            QUnit.test("a manager or department not found leaves the header without a team", async function (assert) {
                serveCalls(function (params) {
                    return Promise.resolve({
                        error: params.method === "get_team_dashboard_by_manager" ? "User not found" : "Department not found",
                    });
                });
                for (var mode of ["manager", "department"]) {
                    var dashboard = renderedDashboard(mode);
                    await dashboard._loadDashboard();
                    var $header = dashboard.$(".o_period_info");
                    assert.deepEqual([$header.find("strong").length, $header.text().trim()], [0, ""], mode);
                }
            });

            QUnit.test("a refused team shows the toolbar of its mode, whose picker opens another team", async function (assert) {
                var isRefused = true;
                var sentCalls = serveCalls(function () {
                    return isRefused
                        ? Promise.reject({message: {data: {name: "odoo.exceptions.AccessError"}}})
                        : Promise.resolve({members: [], department_name: "Other Department"});
                });
                var dashboard = renderedDashboard("project");
                var departmentModeButton = dashboard.$('.o_dashboard_mode_btn[data-mode="department"]')[0];
                await Promise.resolve(dashboard._onDashboardModeChange({currentTarget: departmentModeButton}))
                    .catch(function () {});
                assert.strictEqual(dashboard.$(".o_dashboard_mode_btn.btn-primary").data("mode"), "department");
                assert.strictEqual(dashboard.$(".o_dept_search_input").val(), "Own Department");
                isRefused = false;
                await dashboard._onDeptSearchSelect({
                    preventDefault: function () {},
                    currentTarget: dashboard.$('.o_dept_search_item[data-dept-id="9"]')[0],
                });
                assert.deepEqual(sentCalls.map(function (params) { return params.args[0]; }), [3, 9]);
                assert.strictEqual(dashboard.$(".o_dept_search_input").val(), "Other Department");
            });
        });
    });
});
