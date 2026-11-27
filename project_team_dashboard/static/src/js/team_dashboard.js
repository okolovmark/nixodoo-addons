odoo.define("project_team_dashboard.team_dashboard", function (require) {
    "use strict";

    var AbstractAction = require("web.AbstractAction");
    var core = require("web.core");
    var Dialog = require("web.Dialog");
    var QWeb = core.qweb;

    // Every server call goes through this._rpc, which sends the user's context and with it the companies
    // selected in the switcher; web.rpc sends none.

    // vis-timeline lazy loader - loads the library once on demand,
    // bypassing AMD so it assigns to window.vis.
    var _visPromise = null;
    function _ensureVisTimeline() {
        if (window.vis && window.vis.Timeline && window.vis.DataSet) {
            return $.when();
        }
        if (_visPromise) {
            return _visPromise;
        }
        var def = $.Deferred();
        _visPromise = def.promise();

        // Load CSS
        if (!document.querySelector('link[href*="vis-timeline-graph2d.css"]')) {
            var link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = "/project_team_dashboard/static/lib/vis-timeline/vis-timeline-graph2d.css";
            document.head.appendChild(link);
        }

        // Load JS with AMD temporarily disabled so vis registers as a global
        var savedDefine = window.define;
        window.define = undefined;

        var script = document.createElement("script");
        script.src = "/project_team_dashboard/static/lib/vis-timeline/vis-timeline-graph2d.js";
        script.onload = function () {
            window.define = savedDefine;
            if (window.vis && window.vis.Timeline) {
                def.resolve();
            } else {
                def.reject("vis-timeline loaded but vis.Timeline not found");
            }
        };
        script.onerror = function () {
            window.define = savedDefine;
            _visPromise = null;
            def.reject("Failed to load vis-timeline library");
        };
        document.head.appendChild(script);
        return _visPromise;
    }

    var GANTT_SHIFT_BY_SCALE = {day: {days: 7}, week: {months: 1}, month: {months: 3}};
    var GANTT_PAN_MARGIN_MONTHS = 12;

    function addToDate(date, step, sign) {
        var result = new Date(date.getTime());
        if (step.months) {
            result.setMonth(result.getMonth() + sign * step.months);
        } else {
            result.setDate(result.getDate() + sign * step.days);
        }
        return result;
    }

    function shiftGanttWindow(start, end, scale, direction) {
        var step = GANTT_SHIFT_BY_SCALE[scale] || GANTT_SHIFT_BY_SCALE.week;
        return {start: addToDate(start, step, direction), end: addToDate(end, step, direction)};
    }

    function ganttPanBounds(dataStart, dataEnd) {
        var margin = {months: GANTT_PAN_MARGIN_MONTHS};
        return {min: addToDate(dataStart, margin, -1), max: addToDate(dataEnd, margin, 1)};
    }

    function groupTasksByStage(tasks) {
        var byStage = {};
        var stages = [];
        tasks.forEach(function (task) {
            var key = task.stage_id || 0;
            if (!byStage[key]) {
                byStage[key] = {
                    stageId: key,
                    name: task.stage_name || "No Stage",
                    sequence: key ? (task.stage_sequence || 0) : Infinity,
                    tasks: [],
                };
                stages.push(byStage[key]);
            }
            byStage[key].tasks.push(task);
        });
        stages.sort(function (a, b) {
            return (a.sequence - b.sequence) || (a.stageId - b.stageId);
        });
        stages.forEach(function (stage) {
            stage.tasks.sort(function (a, b) {
                return (a.planned_date_start || "").localeCompare(b.planned_date_start || "");
            });
        });
        return stages;
    }

    /**
     * Order one group's tasks as a tree, depth first. A task nests under its parent only when
     * the parent is in the same group; otherwise it is a root and `outsideParentId` names the parent.
     */
    function buildTaskTree(tasks) {
        var tasksById = {};
        tasks.forEach(function (task) { tasksById[task.id] = task; });
        var childrenById = {};
        var roots = [];
        tasks.forEach(function (task) {
            if (task.parent_id && task.parent_id !== task.id && tasksById[task.parent_id]) {
                (childrenById[task.parent_id] = childrenById[task.parent_id] || []).push(task);
            } else {
                roots.push(task);
            }
        });
        var rows = [];
        var visited = {};
        function visit(task, depth) {
            if (visited[task.id]) { return; }
            visited[task.id] = true;
            var children = (childrenById[task.id] || []).filter(function (child) { return !visited[child.id]; });
            rows.push({
                task: task,
                depth: depth,
                childIds: children.map(function (child) { return child.id; }),
                outsideParentId: task.parent_id && !tasksById[task.parent_id] ? task.parent_id : false,
            });
            children.forEach(function (child) { visit(child, depth + 1); });
        }
        roots.forEach(function (task) { visit(task, 0); });
        tasks.forEach(function (task) { visit(task, 0); });
        return rows;
    }

    function isAncestorPair(taskMap, taskIdA, taskIdB) {
        function isAncestorOf(ancestorId, taskId) {
            var seen = {};
            var task = taskMap[taskId];
            while (task && task.parent_id && !seen[task.id]) {
                seen[task.id] = true;
                if (task.parent_id === ancestorId) {
                    return true;
                }
                task = taskMap[task.parent_id];
            }
            return false;
        }
        return isAncestorOf(taskIdA, taskIdB) || isAncestorOf(taskIdB, taskIdA);
    }

    /**
     * The bars that overlap another bar of the same person, each with the tasks of the bars it
     * overlaps. A rolled-up parent always covers its sub-tasks, so a pair where one task is an
     * ancestor of the other never counts.
     * @param {Array} bars - {id, taskId, start, end}
     * @returns {Object} bar id -> [task id of each bar it overlaps, by start]
     */
    function findBarOverlaps(bars, taskMap) {
        var overlaps = {};
        function addOverlap(bar, otherBar) {
            (overlaps[bar.id] = overlaps[bar.id] || []).push(otherBar.taskId);
        }
        var openBars = [];
        bars.filter(function (bar) { return bar.end; })
            .sort(function (a, b) { return new Date(a.start) - new Date(b.start); })
            .forEach(function (bar) {
                var barStart = new Date(bar.start);
                openBars = openBars.filter(function (openBar) { return new Date(openBar.end) > barStart; });
                openBars.forEach(function (openBar) {
                    if (!isAncestorPair(taskMap, openBar.taskId, bar.taskId)) {
                        addOverlap(openBar, bar);
                        addOverlap(bar, openBar);
                    }
                });
                openBars.push(bar);
            });
        return overlaps;
    }

    function ganttBarRules(task, hasSubtaskRows) {
        var hasNoLength = !task.planned_date_end || task.planned_date_end === task.planned_date_start;
        return {
            isPoint: !!task.is_deadline_only || (task.task_type === "milestone" && (!hasSubtaskRows || hasNoLength)),
            canDrag: !task.is_deadline_only && !task.is_planned_from_subtasks && !!task.can_write,
        };
    }

    function outsideParentOf(row, taskMap) {
        if (row.depth) {
            return null;
        }
        var parent = row.outsideParentId && taskMap[row.outsideParentId];
        if (parent) {
            return parent.key || parent.name;
        }
        return row.task.parent_label || null;
    }

    function subtaskOfMarker(parentLabel) {
        return '<span class="text-muted small ms-1">↳ sub-task of ' + _.escape(parentLabel) + "</span>";
    }

    var GANTT_STAGE_PALETTE = ["#8e7cc3", "#5dade2", "#2ecb71", "#f5b041", "#48c9b0", "#eb984e", "#af7ac5", "#5d6d7e"];
    var GANTT_CLOSED_STAGE_COLOR = "#aab7b8";

    function lightColor(hex) {
        var value = parseInt(hex.slice(1), 16);
        return "rgba(" + (value >> 16) + "," + ((value >> 8) & 255) + "," + (value & 255) + ",0.3)";
    }

    function assignStageColors(tasks) {
        var openIndex = 0;
        return groupTasksByStage(tasks).map(function (stage) {
            var closed = stage.tasks.every(function (t) { return t.is_closed; });
            var solid = closed ? GANTT_CLOSED_STAGE_COLOR
                : GANTT_STAGE_PALETTE[openIndex++ % GANTT_STAGE_PALETTE.length];
            return {stageId: stage.stageId, name: stage.name, solid: solid, light: lightColor(solid)};
        });
    }

    var GANTT_ELBOW_STUB = 10;

    function elbowPath(x1, y1, x2, y2) {
        var exitX = x1 + GANTT_ELBOW_STUB;
        if (x2 - GANTT_ELBOW_STUB >= exitX) {
            return "M" + x1 + "," + y1 + " H" + exitX + " V" + y2 + " H" + x2;
        }
        var midY = (y1 + y2) / 2;
        var entryX = x2 - GANTT_ELBOW_STUB;
        return "M" + x1 + "," + y1 + " H" + exitX + " V" + midY + " H" + entryX + " V" + y2 + " H" + x2;
    }

    var OPEN_TASK_POINTER_EVENTS = ["pointerdown", "mousedown", "touchstart"];

    function keepOpenTaskOutOfGroupToggle(container) {
        if (container.dataset.openTaskGuard) {
            return;
        }
        container.dataset.openTaskGuard = "1";
        OPEN_TASK_POINTER_EVENTS.forEach(function (eventName) {
            container.addEventListener(eventName, function (ev) {
                if (ev.target.closest && ev.target.closest(".o_gantt_open_task")) {
                    ev.stopPropagation();
                }
            }, true);
        });
    }

    var GANTT_DAY_MS = 86400000;

    function wholeDaysBetween(original, moved) {
        return Math.round((new Date(moved) - new Date(original)) / GANTT_DAY_MS);
    }

    function shiftByWholeDays(original, moved) {
        if (!original || !moved) {
            return moved;
        }
        return window.moment(new Date(original)).add(wholeDaysBetween(original, moved), "days").toDate();
    }

    function parseUtcDateTime(value) {
        var parts = value.split(/[- :]/);
        return Date.UTC(+parts[0], +parts[1] - 1, +parts[2], +(parts[3] || 0), +(parts[4] || 0), +(parts[5] || 0));
    }

    function formatUtcDateTime(ms) {
        return new Date(ms).toISOString().substring(0, 19).replace("T", " ");
    }

    function utcDateTimeToDate(value) {
        return new Date(parseUtcDateTime(value));
    }

    function localIsoDate(date) {
        var pad = function (n) { return n < 10 ? "0" + n : "" + n; };
        return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
    }

    function utcDateTimeToLocalDay(value) {
        return localIsoDate(utcDateTimeToDate(value));
    }

    function buildStageHistoryRows(task, scope) {
        var idPrefix = "sh_" + (scope === undefined ? "" : scope + "_") + task.id + "_";
        var rows = (task.stage_history || []).map(function (visit, index) {
            var startMs = parseUtcDateTime(visit.start);
            return {
                id: idPrefix + index,
                // a deleted stage has no id: 0 is the key the stage colours and the legend file it under
                stageId: visit.stage_id || 0,
                stageName: visit.stage_name,
                isClosed: visit.is_closed,
                start: visit.start,
                end: formatUtcDateTime(Math.max(parseUtcDateTime(visit.end), startMs + GANTT_DAY_MS)),
                visitEnd: visit.end,
            };
        });
        var edges = rows.slice(1).map(function (row, index) {
            return {from: rows[index].id, to: row.id};
        });
        return {rows: rows, edges: edges};
    }

    function stageHistorySpan(task) {
        var visits = task.stage_history || [];
        if (!visits.length) {
            return null;
        }
        var startMs = parseUtcDateTime(visits[0].start);
        var endMs = Math.max(parseUtcDateTime(visits[visits.length - 1].end), startMs + GANTT_DAY_MS);
        return {start: visits[0].start, end: formatUtcDateTime(endMs)};
    }

    var GANTT_TASK_MATCH_LIMIT = 8;

    function findGanttTasks(tasks, query) {
        var search = (query || "").trim().toLowerCase();
        if (!search) {
            return [];
        }
        var ranked = [];
        tasks.forEach(function (task, idx) {
            var key = (task.key || "").toLowerCase();
            var name = (task.name || "").toLowerCase();
            var rank;
            if (key === search) {
                rank = 0;
            } else if (key.indexOf(search) === 0) {
                rank = 1;
            } else if (name.indexOf(search) === 0) {
                rank = 2;
            } else if (key.indexOf(search) !== -1 || name.indexOf(search) !== -1) {
                rank = 3;
            } else {
                return;
            }
            ranked.push({task: task, rank: rank, idx: idx});
        });
        ranked.sort(function (a, b) { return a.rank - b.rank || a.idx - b.idx; });
        return ranked.slice(0, GANTT_TASK_MATCH_LIMIT).map(function (entry) { return entry.task; });
    }

    function dayNumber(isoDate) {
        var parts = isoDate.substring(0, 10).split("-");
        return Date.UTC(+parts[0], +parts[1] - 1, +parts[2]) / 86400000;
    }

    var TeamDashboard = AbstractAction.extend({
        template: "project_team_dashboard.TeamDashboardMain",
        cssLibs: [],
        events: {
            "click .o_team_dashboard_refresh": "_onRefresh",
            "click .o_open_project": "_onOpenProject",
            "click .o_open_my_kanban": "_onOpenMyKanban",
            "click .o_open_kanban": "_onOpenKanban",
            "click .o_open_backlog": "_onOpenBacklog",
            "click .o_member_backlog_link": "_onMemberBacklogClick",
            "click .o_member_kanban_link": "_onMemberKanbanClick",
            "click .o_member_closed_tasks": "_onMemberClosedTasks",
            "click .o_queue_toggle": "_onToggleQueue",
            "click .o_task_link": "_onTaskClick",
            "input .o_dashboard_filter_bar .o_project_search_input": "_onProjectSearchInput",
            "focus .o_dashboard_filter_bar .o_project_search_input": "_onProjectSearchFocus",
            "click .o_dashboard_filter_bar .o_project_search_item": "_onProjectSearchSelect",
            "change .o_period_select": "_onPeriodChange",
            "change .o_date_start": "_onCustomDateChange",
            "change .o_date_end": "_onCustomDateChange",
            "click .o_view_cards": "_onViewCards",
            "click .o_view_gantt": "_onViewGantt",
            "click .o_gantt_scale": "_onGanttScaleChange",
            "click .o_gantt_fit": "_onGanttFit",
            "click .o_gantt_group_by": "_onGanttGroupByChange",
            "click .o_gantt_show_completed": "_onGanttShowCompleted",
            "click .o_gantt_export_excel": "_onGanttExportExcel",
            "click .o_gantt_today": "_onGanttToday",
            "click .o_gantt_shift": "_onGanttShift",
            "click .o_gantt_open_task": "_onGanttOpenTask",
            "input .o_gantt_search_input": "_onGanttSearchInput",
            "focus .o_gantt_search_input": "_onGanttSearchFocus",
            "keydown .o_gantt_search_input": "_onGanttSearchKeydown",
            "click .o_gantt_faceted_input": "_onGanttFacetedInputClick",
            "click .o_gantt_search_field": "_onGanttSearchFieldSelect",
            "click .o_gantt_search_value": "_onGanttSearchValueSelect",
            "click .o_gantt_search_task": "_onGanttSearchTaskSelect",
            "click .o_gantt_facet_remove": "_onGanttFacetRemove",
            "click .o_gantt_no_dates_count": "_onGanttNoDatesClick",
            "click .o_gantt_unscheduled_name": "_onGanttOpenTask",
            "change .o_gantt_column_toggle": "_onGanttColumnToggle",
            "click .o_gantt_show_baseline": "_onGanttShowBaselineToggle",
            "click .o_gantt_stage_history": "_onGanttStageHistoryToggle",
            "click .o_gantt_save_baseline": "_onGanttSaveBaseline",
            "click .o_gantt_dep_save": "_onGanttDependencySaveLag",
            "click .o_gantt_dep_remove": "_onGanttDependencyRemove",
            "click .o_gantt_dep_close": "_ganttClosePopover",
            // Multi-project gantt: add/remove project tags
            "input .o_gantt_project_add_input": "_onGanttProjectAddInput",
            "focus .o_gantt_project_add_input": "_onGanttProjectAddFocus",
            "click .o_gantt_container .o_project_search_item": "_onGanttProjectAddSelect",
            "click .o_remove_project_tag": "_onGanttProjectRemoveTag",
            // Mode switching
            "click .o_dashboard_mode_btn": "_onDashboardModeChange",
            // Manager mode: user search
            "input .o_user_search_input": "_onUserSearchInput",
            "focus .o_user_search_input": "_onUserSearchFocus",
            "click .o_user_search_item": "_onUserSearchSelect",
            "change .o_depth_select": "_onDepthChange",
            // Department mode: department search
            "input .o_dept_search_input": "_onDeptSearchInput",
            "focus .o_dept_search_input": "_onDeptSearchFocus",
            "click .o_dept_search_item": "_onDeptSearchSelect",
        },

        init: function (parent, action) {
            this._super.apply(this, arguments);
            this.projectId = action.context && action.context.default_project_id;
            this.projects = [];
            this.dashboardData = null;
            this.period = "this_week";
            this.dateStart = null;
            this.dateEnd = null;
            this.viewMode = "cards";
            this.ganttTimeline = null;
            this.ganttData = null;
            this.ganttScale = "week";
            this.ganttGroupBy = "user";
            this.ganttShowCompleted = false;
            this.ganttColumns = [];
            this.ganttShowBaseline = false;
            this.ganttShowStageHistory = false;
            // Multi-project gantt state
            this.ganttProjectIds = []; // list of project IDs for gantt
            // Dashboard mode: "project", "manager", "department"
            this.dashboardMode = "project";
            // Manager mode state
            this.managerId = null; // defaults to current user in willStart
            this.managerName = "";
            this.managerUsers = []; // loaded list of all users
            this.maxDepth = 0; // 0 = all levels
            // Department mode state
            this.departmentId = null;
            this.departmentName = "";
            this.departments = [];
            // Faceted search state
            this.ganttFacets = [];        // [{field, fieldLabel, value}]
            this._ganttSearchField = null; // Currently selected field for next value
            this._ganttSearchDebounce = null;
            this._GANTT_SEARCH_FIELDS = [
                {field: "user_names", label: "Assignee", icon: "fa-user"},
                {field: "name", label: "Task", icon: "fa-tasks"},
                {field: "key", label: "Key", icon: "fa-key"},
                {field: "stage_name", label: "Stage", icon: "fa-columns"},
                {field: "task_type", label: "Type", icon: "fa-tag"},
            ];
        },

        willStart: function () {
            var self = this;
            return this._super.apply(this, arguments).then(function () {
                return $.when(
                    self._loadProjects(),
                    self._loadManagerUsers(),
                    self._loadDepartments()
                ).then(function () {
                    // Set default manager to current session user
                    self.managerId = self.getSession().uid;
                    var me = self.managerUsers.find(function (u) {
                        return u.id === self.managerId;
                    });
                    if (me) {
                        self.managerName = me.name;
                    }
                    // Set default department from current user's employee
                    return self._rpc({
                        model: "hr.employee",
                        method: "search_read",
                        domain: [["user_id", "=", self.getSession().uid]],
                        fields: ["department_id"],
                        limit: 1,
                    }).then(function (emps) {
                        if (emps.length && emps[0].department_id) {
                            self.departmentId = emps[0].department_id[0];
                            var dept = self.departments.find(function (d) {
                                return d.id === self.departmentId;
                            });
                            if (dept) {
                                self.departmentName = dept.complete_name || dept.name;
                            }
                        }
                    });
                }).then(function () {
                    if (!self.projectId) {
                        return self._loadUserDefaultProject();
                    }
                });
            });
        },

        start: function () {
            var self = this;
            return this._super.apply(this, arguments).then(function () {
                // Close search dropdowns when clicking outside
                $(document).on("click.team_dashboard", function (ev) {
                    if (!$(ev.target).closest(".o_project_search_wrapper").length) {
                        self.$(".o_project_search_dropdown").removeClass("show");
                    }
                    if (!$(ev.target).closest(".o_user_search_wrapper").length) {
                        self.$(".o_user_search_dropdown").removeClass("show");
                    }
                    if (!$(ev.target).closest(".o_dept_search_wrapper").length) {
                        self.$(".o_dept_search_dropdown").removeClass("show");
                    }
                    // Close gantt search dropdown when clicking outside
                    if (!$(ev.target).closest(".o_gantt_search_box").length) {
                        self._ganttHideDropdown();
                    }
                    if (!$(ev.target).closest(".o_gantt_dep_popover, .o_gantt_dep_hit").length) {
                        self._ganttClosePopover();
                    }
                });
                if (self.dashboardMode === "project" && self.projectId) {
                    return self._loadDashboard();
                } else {
                    // Render empty state so toolbar/filters are visible
                    self._renderDashboard();
                }
            });
        },

        destroy: function () {
            this._destroyGantt();
            $(document).off("click.team_dashboard");
            this._super.apply(this, arguments);
        },

        _loadProjects: function () {
            var self = this;
            return this._rpc({
                model: "project.project",
                method: "search_read",
                domain: [],
                fields: ["id", "name", "key"],
                orderBy: [{name: "name", asc: true}],
            }).then(function (projects) {
                self.projects = projects;
            });
        },

        _loadManagerUsers: function () {
            var self = this;
            return this._rpc({
                model: "res.users",
                method: "search_read",
                domain: [["share", "=", false]],
                fields: ["id", "name"],
                orderBy: [{name: "name", asc: true}],
            }).then(function (users) {
                self.managerUsers = users;
            });
        },

        _loadDepartments: function () {
            var self = this;
            return this._rpc({
                model: "hr.department",
                method: "search_read",
                domain: [],
                fields: ["id", "name", "complete_name"],
                orderBy: [{name: "complete_name", asc: true}],
            }).then(function (depts) {
                self.departments = depts;
            });
        },

        _loadUserDefaultProject: function () {
            var self = this;
            return this._rpc({
                model: "res.users",
                method: "read",
                args: [[self.getSession().uid], ["dashboard_default_project_id"]],
            }).then(function (result) {
                var defaultProject = result && result[0] && result[0].dashboard_default_project_id;
                // a default project of a company not selected is not among the projects found
                if (defaultProject && _.findWhere(self.projects, {id: defaultProject[0]})) {
                    self.projectId = defaultProject[0];
                } else if (self.projects.length > 0) {
                    self.projectId = self.projects[0].id;
                }
            });
        },

        _getDates: function () {
            var today = new Date();
            var start, end;
            switch (this.period) {
                case "this_week": {
                    var day = today.getDay();
                    var diff = day === 0 ? 6 : day - 1; // Monday=0
                    start = new Date(today);
                    start.setDate(today.getDate() - diff);
                    end = new Date(start);
                    end.setDate(start.getDate() + 6);
                    break;
                }
                case "last_week": {
                    var day2 = today.getDay();
                    var diff2 = day2 === 0 ? 6 : day2 - 1;
                    start = new Date(today);
                    start.setDate(today.getDate() - diff2 - 7);
                    end = new Date(start);
                    end.setDate(start.getDate() + 6);
                    break;
                }
                case "this_month":
                    start = new Date(today.getFullYear(), today.getMonth(), 1);
                    end = new Date(today.getFullYear(), today.getMonth() + 1, 0);
                    break;
                case "last_month":
                    start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
                    end = new Date(today.getFullYear(), today.getMonth(), 0);
                    break;
                case "custom":
                    if (this.dateStart && this.dateEnd) {
                        return {
                            date_start: this.dateStart,
                            date_end: this.dateEnd,
                        };
                    }
                    // fallthrough to this_week if no custom dates
                    return this._getThisWeekDates();
                default:
                    return this._getThisWeekDates();
            }
            return {
                date_start: this._formatDate(start),
                date_end: this._formatDate(end),
            };
        },

        _getThisWeekDates: function () {
            var today = new Date();
            var day = today.getDay();
            var diff = day === 0 ? 6 : day - 1;
            var start = new Date(today);
            start.setDate(today.getDate() - diff);
            var end = new Date(start);
            end.setDate(start.getDate() + 6);
            return {
                date_start: this._formatDate(start),
                date_end: this._formatDate(end),
            };
        },

        _formatDate: function (d) {
            var year = d.getFullYear();
            var month = String(d.getMonth() + 1).padStart(2, "0");
            var day = String(d.getDate()).padStart(2, "0");
            return year + "-" + month + "-" + day;
        },

        _loadDashboard: function () {
            var self = this;
            var dates = this._getDates();

            if (this.dashboardMode === "manager") {
                if (!this.managerId) { return; }
                return this._rpc({
                    model: "project.project",
                    method: "get_team_dashboard_by_manager",
                    args: [this.managerId, dates.date_start, dates.date_end, this.maxDepth],
                }).then(function (data) {
                    self.dashboardData = data;
                    self._renderDashboard();
                });
            } else if (this.dashboardMode === "department") {
                if (!this.departmentId) { return; }
                return this._rpc({
                    model: "project.project",
                    method: "get_team_dashboard_by_department",
                    args: [this.departmentId, dates.date_start, dates.date_end],
                }).then(function (data) {
                    self.dashboardData = data;
                    self._renderDashboard();
                });
            } else {
                // project mode (default)
                if (!this.projectId) { return; }
                return this._rpc({
                    model: "project.project",
                    method: "get_team_dashboard_data",
                    args: [this.projectId, dates.date_start, dates.date_end],
                }).then(function (data) {
                    self.dashboardData = data;
                    self._renderDashboard();
                });
            }
        },

        _renderDashboard: function () {
            var $content = this.$(".o_team_dashboard_content");
            if (!$content.length) {
                return;
            }
            this._destroyGantt();
            $content.empty();

            // Ensure we always have a data object for the template
            var data = this.dashboardData || {
                members: [],
                period_start: "",
                period_end: "",
                project_name: "",
                manager_name: this.managerName || "",
                department_name: this.departmentName || "",
                project_count: 0,
            };

            var html = QWeb.render("project_team_dashboard.TeamDashboardContent", {
                data: data,
                projects: this.projects,
                selectedProjectId: this.projectId,
                selectedProjectName: this._getSelectedProjectName(),
                period: this.period,
                dateStart: this.dateStart,
                dateEnd: this.dateEnd,
                // Mode state
                dashboardMode: this.dashboardMode,
                // Manager mode
                managerUsers: this.managerUsers,
                managerId: this.managerId,
                managerName: this.managerName,
                maxDepth: this.maxDepth,
                // Department mode
                departments: this.departments,
                departmentId: this.departmentId,
                departmentName: this.departmentName,
            });
            $content.html(html);
            // Restore view mode after re-render
            if (this.viewMode === "gantt" && this.dashboardMode === "project") {
                this.$(".o_team_dashboard_grid").addClass("d-none");
                this.$(".o_gantt_container").removeClass("d-none");
                this.$(".o_view_gantt").addClass("active");
                this.$(".o_view_cards").removeClass("active");
                this.$(".o_dashboard_filter_bar").addClass("d-none");
                // Restore scale button state
                this.$(".o_gantt_scale").removeClass("active");
                this.$('.o_gantt_scale[data-scale="' + this.ganttScale + '"]').addClass("active");
                // Restore group-by button state
                this.$(".o_gantt_group_by").removeClass("active");
                this.$('.o_gantt_group_by[data-group="' + this.ganttGroupBy + '"]').addClass("active");
                // Restore show completed button state
                this.$(".o_gantt_show_completed").toggleClass("active", this.ganttShowCompleted);
                // Restore facet chips
                this._ganttRenderFacets();
                this._ganttUpdatePlaceholder();
                // Render multi-project tags
                this._ganttRenderProjectTags();
                this._loadGanttData();
            }
        },

        // Event handlers
        _onRefresh: function () {
            this._loadDashboard();
        },

        _getSelectedProjectName: function () {
            for (var i = 0; i < this.projects.length; i++) {
                if (this.projects[i].id === this.projectId) {
                    var p = this.projects[i];
                    return p.key ? p.key + " - " + p.name : p.name;
                }
            }
            return "";
        },

        _onProjectSearchInput: function (ev) {
            var query = $(ev.currentTarget).val().toLowerCase();
            var $wrapper = $(ev.currentTarget).closest(".o_project_search_wrapper");
            var $dropdown = $wrapper.find(".o_project_search_dropdown");
            var $items = $dropdown.find(".o_project_search_item");
            var visibleCount = 0;
            $items.each(function () {
                var text = $(this).text().toLowerCase();
                var match = !query || text.indexOf(query) !== -1;
                $(this).toggleClass("d-none", !match);
                if (match) { visibleCount++; }
            });
            if (visibleCount > 0) {
                $dropdown.addClass("show");
            } else {
                $dropdown.removeClass("show");
            }
        },

        _onProjectSearchFocus: function (ev) {
            $(ev.currentTarget).select();
            var $wrapper = $(ev.currentTarget).closest(".o_project_search_wrapper");
            var $dropdown = $wrapper.find(".o_project_search_dropdown");
            $dropdown.find(".o_project_search_item").removeClass("d-none");
            $dropdown.addClass("show");
        },

        _onProjectSearchSelect: function (ev) {
            ev.preventDefault();
            var $item = $(ev.currentTarget);
            var projectId = parseInt($item.data("project-id"), 10);
            this.$(".o_dashboard_filter_bar .o_project_search_dropdown").removeClass("show");
            this.projectId = projectId;
            this.$(".o_dashboard_filter_bar .o_project_search_input").val(this._getSelectedProjectName());
            this._loadDashboard();
        },

        _onToggleQueue: function (ev) {
            var $target = $(ev.currentTarget);
            var userId = $target.data("user-id");
            var $queue = this.$('.o_queue_list[data-user-id="' + userId + '"]');
            $queue.toggleClass("d-none");
            var $icon = $target.find("i");
            $icon.toggleClass("fa-chevron-down fa-chevron-up");
        },

        _onTaskClick: function (ev) {
            ev.preventDefault();
            var taskId = $(ev.currentTarget).data("task-id");
            if (taskId) {
                this.do_action({
                    type: "ir.actions.act_window",
                    res_model: "project.task",
                    res_id: taskId,
                    views: [[false, "form"]],
                    target: "current",
                });
            }
        },

        _onOpenProject: function () {
            if (!this.projectId) {
                return;
            }
            this.do_action({
                type: "ir.actions.act_window",
                res_model: "project.project",
                res_id: this.projectId,
                views: [[false, "form"]],
                target: "current",
            });
        },

        _onOpenBacklog: function () {
            if (!this.projectId) {
                return;
            }
            this.do_action("project_team_dashboard.action_project_task_backlog", {
                additional_context: {
                    search_default_project_id: this.projectId,
                    search_default_unassigned: true,
                    default_project_id: this.projectId,
                },
            });
        },

        _onMemberBacklogClick: function (ev) {
            ev.preventDefault();
            var userId = $(ev.currentTarget).data("user-id");
            if (!this.projectId || !userId) {
                return;
            }
            this.do_action("project_team_dashboard.action_project_task_backlog", {
                additional_context: {
                    search_default_project_id: this.projectId,
                    search_default_user_ids: userId,
                    default_project_id: this.projectId,
                },
            });
        },

        _onOpenKanban: function () {
            if (!this.projectId) {
                return;
            }
            this.do_action({
                type: "ir.actions.act_window",
                name: "Kanban",
                res_model: "project.task",
                views: [
                    [false, "kanban"],
                    [false, "form"],
                ],
                domain: [["project_id", "=", this.projectId]],
                context: {
                    default_project_id: this.projectId,
                },
            });
        },

        _onMemberKanbanClick: function (ev) {
            ev.preventDefault();
            var userId = $(ev.currentTarget).data("user-id");
            if (!this.projectId || !userId) {
                return;
            }
            this.do_action({
                type: "ir.actions.act_window",
                name: "Kanban",
                res_model: "project.task",
                views: [
                    [false, "kanban"],
                    [false, "form"],
                ],
                domain: [
                    ["project_id", "=", this.projectId],
                    ["user_ids", "in", [userId]],
                ],
                context: {
                    default_project_id: this.projectId,
                },
            });
        },

        // ---- Requirement 6: Member card closed tasks button ----

        _onMemberClosedTasks: function (ev) {
            ev.preventDefault();
            var userId = parseInt($(ev.currentTarget).data("user-id"), 10);
            if (!userId) { return; }

            // Build domain: user's closed (folded stage) tasks
            var domain = [
                ["user_ids", "in", [userId]],
                ["stage_id.fold", "=", true],
            ];
            // If in project mode with a project selected, filter to that project
            if (this.dashboardMode === "project" && this.projectId) {
                domain.push(["project_id", "=", this.projectId]);
            }

            this.do_action({
                type: "ir.actions.act_window",
                name: "Closed Tasks",
                res_model: "project.task",
                views: [
                    [false, "list"],
                    [false, "form"],
                ],
                domain: domain,
                target: "current",
            });
        },

        _onOpenMyKanban: function () {
            if (!this.projectId) {
                return;
            }
            this.do_action({
                type: "ir.actions.act_window",
                name: "My Tasks",
                res_model: "project.task",
                views: [
                    [false, "kanban"],
                    [false, "list"],
                    [false, "form"],
                ],
                domain: [["project_id", "=", this.projectId]],
                context: {
                    search_default_my_tasks: 1,
                    default_project_id: this.projectId,
                },
            });
        },

        _onProjectChange: function (ev) {
            this.projectId = parseInt($(ev.currentTarget).val(), 10);
            this._loadDashboard();
        },

        // ---- Dashboard mode switching ----

        _onDashboardModeChange: function (ev) {
            var self = this;
            var mode = $(ev.currentTarget).data("mode");
            if (mode === this.dashboardMode) {
                return;
            }
            this.dashboardMode = mode;
            this.dashboardData = null;
            this.viewMode = "cards";

            var loading = this._loadDashboard();
            if (!loading) {
                this._renderDashboard();
                return;
            }
            // A refused team still gets the toolbar of its mode, whose picker opens another team.
            return loading.guardedCatch(function () {
                self._renderDashboard();
            });
        },

        // ---- Manager mode: user search ----

        _getSelectedManagerName: function () {
            for (var i = 0; i < this.managerUsers.length; i++) {
                if (this.managerUsers[i].id === this.managerId) {
                    return this.managerUsers[i].name;
                }
            }
            return this.managerName || "";
        },

        _onUserSearchInput: function (ev) {
            var query = $(ev.currentTarget).val().toLowerCase();
            var $wrapper = $(ev.currentTarget).closest(".o_user_search_wrapper");
            var $dropdown = $wrapper.find(".o_user_search_dropdown");
            var $items = $dropdown.find(".o_user_search_item");
            var visibleCount = 0;
            $items.each(function () {
                var text = $(this).text().toLowerCase();
                var match = !query || text.indexOf(query) !== -1;
                $(this).toggleClass("d-none", !match);
                if (match) { visibleCount++; }
            });
            if (visibleCount > 0) {
                $dropdown.addClass("show");
            } else {
                $dropdown.removeClass("show");
            }
        },

        _onUserSearchFocus: function (ev) {
            $(ev.currentTarget).select();
            var $wrapper = $(ev.currentTarget).closest(".o_user_search_wrapper");
            var $dropdown = $wrapper.find(".o_user_search_dropdown");
            $dropdown.find(".o_user_search_item").removeClass("d-none");
            $dropdown.addClass("show");
        },

        _onUserSearchSelect: function (ev) {
            ev.preventDefault();
            var self = this;
            var $item = $(ev.currentTarget);
            var previousId = this.managerId;
            var previousName = this.managerName;
            this.$(".o_user_search_dropdown").removeClass("show");
            this.managerId = parseInt($item.data("user-id"), 10);
            this.managerName = $item.text().trim();
            this.$(".o_user_search_input").val(this.managerName);
            // A refused manager leaves the previous team on screen: the picker and later reloads go back to it.
            return this._loadDashboard().guardedCatch(function () {
                self.managerId = previousId;
                self.managerName = previousName;
                self.$(".o_user_search_input").val(previousName);
            });
        },

        _onDepthChange: function (ev) {
            this.maxDepth = parseInt($(ev.currentTarget).val(), 10);
            if (this.managerId) {
                this._loadDashboard();
            }
        },

        // ---- Department mode: department search ----

        _getSelectedDepartmentName: function () {
            for (var i = 0; i < this.departments.length; i++) {
                if (this.departments[i].id === this.departmentId) {
                    return this.departments[i].complete_name || this.departments[i].name;
                }
            }
            return this.departmentName || "";
        },

        _onDeptSearchInput: function (ev) {
            var query = $(ev.currentTarget).val().toLowerCase();
            var $wrapper = $(ev.currentTarget).closest(".o_dept_search_wrapper");
            var $dropdown = $wrapper.find(".o_dept_search_dropdown");
            var $items = $dropdown.find(".o_dept_search_item");
            var visibleCount = 0;
            $items.each(function () {
                var text = $(this).text().toLowerCase();
                var match = !query || text.indexOf(query) !== -1;
                $(this).toggleClass("d-none", !match);
                if (match) { visibleCount++; }
            });
            if (visibleCount > 0) {
                $dropdown.addClass("show");
            } else {
                $dropdown.removeClass("show");
            }
        },

        _onDeptSearchFocus: function (ev) {
            $(ev.currentTarget).select();
            var $wrapper = $(ev.currentTarget).closest(".o_dept_search_wrapper");
            var $dropdown = $wrapper.find(".o_dept_search_dropdown");
            $dropdown.find(".o_dept_search_item").removeClass("d-none");
            $dropdown.addClass("show");
        },

        _onDeptSearchSelect: function (ev) {
            ev.preventDefault();
            var self = this;
            var $item = $(ev.currentTarget);
            var previousId = this.departmentId;
            var previousName = this.departmentName;
            this.$(".o_dept_search_dropdown").removeClass("show");
            this.departmentId = parseInt($item.data("dept-id"), 10);
            this.departmentName = $item.text().trim();
            this.$(".o_dept_search_input").val(this.departmentName);
            // A refused department leaves the previous team on screen: the picker and later reloads go back to it.
            return this._loadDashboard().guardedCatch(function () {
                self.departmentId = previousId;
                self.departmentName = previousName;
                self.$(".o_dept_search_input").val(previousName);
            });
        },

        _onPeriodChange: function (ev) {
            this.period = $(ev.currentTarget).val();
            if (this.period !== "custom") {
                this._loadDashboard();
            }
            // Show/hide custom date inputs
            this.$(".o_custom_dates").toggleClass("d-none", this.period !== "custom");
        },

        _onCustomDateChange: function () {
            this.dateStart = this.$(".o_date_start").val();
            this.dateEnd = this.$(".o_date_end").val();
            if (this.dateStart && this.dateEnd) {
                this._loadDashboard();
            }
        },

        // ---- View mode toggle ----

        _onViewCards: function () {
            if (this.viewMode === "cards") {
                return;
            }
            this.viewMode = "cards";
            this._destroyGantt();
            this.$(".o_team_dashboard_grid").removeClass("d-none");
            this.$(".o_gantt_container").addClass("d-none");
            this.$(".o_view_cards").addClass("active");
            this.$(".o_view_gantt").removeClass("active");
            this.$(".o_dashboard_filter_bar").removeClass("d-none");
        },

        _onViewGantt: function () {
            if (this.viewMode === "gantt") {
                return;
            }
            this.viewMode = "gantt";
            this.$(".o_team_dashboard_grid").addClass("d-none");
            this.$(".o_gantt_container").removeClass("d-none");
            this.$(".o_view_gantt").addClass("active");
            this.$(".o_view_cards").removeClass("active");
            this.$(".o_dashboard_filter_bar").addClass("d-none");
            // Initialize gantt project list with current project if empty
            if (!this.ganttProjectIds.length && this.projectId) {
                this.ganttProjectIds = [this.projectId];
            }
            this._ganttRenderProjectTags();
            this._loadGanttData();
        },

        // ---- Multi-project gantt project selector ----

        _getProjectNameById: function (pid) {
            for (var i = 0; i < this.projects.length; i++) {
                if (this.projects[i].id === pid) {
                    var p = this.projects[i];
                    return p.key ? p.key + " - " + p.name : p.name;
                }
            }
            return "Project #" + pid;
        },

        _ganttRenderProjectTags: function () {
            var self = this;
            var $container = this.$(".o_gantt_multiproject_tags");
            if (!$container.length) { return; }
            $container.empty();
            this.ganttProjectIds.forEach(function (pid) {
                var name = self._getProjectNameById(pid);
                var $tag = $(
                    '<span class="o_project_tag" data-project-id="' + pid + '">' +
                    _.escape(name) +
                    ' <button type="button" class="o_remove_project_tag btn btn-link p-0 border-0" ' +
                    'data-project-id="' + pid + '" ' +
                    'aria-label="Remove ' + _.escape(name) + '" ' +
                    'title="Remove project">' +
                    '<i class="fa fa-times-circle"></i></button>' +
                    '</span>'
                );
                $container.append($tag);
            });
        },

        _onGanttProjectAddInput: function (ev) {
            var query = $(ev.currentTarget).val().toLowerCase();
            var $wrapper = $(ev.currentTarget).closest(".o_project_search_wrapper");
            var $dropdown = $wrapper.find(".o_project_search_dropdown");
            var $items = $dropdown.find(".o_project_search_item");
            var self = this;
            var visibleCount = 0;
            $items.each(function () {
                var pid = parseInt($(this).data("project-id"), 10);
                var text = $(this).text().toLowerCase();
                var match = (!query || text.indexOf(query) !== -1) && self.ganttProjectIds.indexOf(pid) === -1;
                $(this).toggleClass("d-none", !match);
                if (match) { visibleCount++; }
            });
            if (visibleCount > 0) {
                $dropdown.addClass("show");
            } else {
                $dropdown.removeClass("show");
            }
        },

        _onGanttProjectAddFocus: function (ev) {
            $(ev.currentTarget).select();
            var $wrapper = $(ev.currentTarget).closest(".o_project_search_wrapper");
            var $dropdown = $wrapper.find(".o_project_search_dropdown");
            var self = this;
            $dropdown.find(".o_project_search_item").each(function () {
                var pid = parseInt($(this).data("project-id"), 10);
                $(this).toggleClass("d-none", self.ganttProjectIds.indexOf(pid) !== -1);
            });
            $dropdown.addClass("show");
        },

        _onGanttProjectAddSelect: function (ev) {
            ev.preventDefault();
            var $item = $(ev.currentTarget);
            var pid = parseInt($item.data("project-id"), 10);
            this.$(".o_gantt_container .o_project_search_dropdown").removeClass("show");
            this.$(".o_gantt_project_add_input").val("");
            if (this.ganttProjectIds.indexOf(pid) === -1) {
                this.ganttProjectIds.push(pid);
                this._ganttRenderProjectTags();
                this._loadGanttData();
            }
        },

        _onGanttProjectRemoveTag: function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var pid = parseInt($(ev.currentTarget).data("project-id"), 10);
            this.ganttProjectIds = this.ganttProjectIds.filter(function (id) { return id !== pid; });
            this._ganttRenderProjectTags();
            if (this.ganttProjectIds.length) {
                this._loadGanttData();
            } else {
                this._destroyGantt();
                this.$(".o_gantt_timeline_wrapper").html(
                    '<div class="o_gantt_empty_state text-center p-5">' +
                    '<i class="fa fa-calendar-o fa-3x text-muted mb-3 d-block" />' +
                    '<h5 class="text-muted">No projects selected</h5>' +
                    '<p class="text-muted">Add a project above to see the Gantt chart.</p>' +
                    '</div>'
                );
            }
        },

        // ---- Gantt chart logic ----

        _loadGanttData: function (keepWindow) {
            var self = this;
            var pids = this._ganttProjectIdsOrCurrent();
            if (!pids.length) {
                return;
            }
            this._ganttKeepWindow = keepWindow && this.ganttTimeline ? this.ganttTimeline.getWindow() : null;
            var $wrapper = this.$(".o_gantt_timeline_wrapper");
            if (!$wrapper.length) {
                $wrapper = this.$(".o_gantt_container");
            }
            $wrapper.html('<div class="text-center p-5"><i class="fa fa-spinner fa-spin fa-2x" /></div>');

            return _ensureVisTimeline().then(function () {
                return self._rpc({
                    model: "project.project",
                    method: "get_gantt_chart_data",
                    kwargs: {
                        project_ids: pids,
                        show_completed: self.ganttShowCompleted,
                        include_stage_history: self.ganttShowStageHistory,
                    },
                });
            }).then(function (data) {
                self.ganttData = data;
                self.ganttColumns = data.columns || [];
                self._renderGantt();
            }).fail(function (err) {
                var $w = self.$(".o_gantt_timeline_wrapper");
                if (!$w.length) { $w = self.$(".o_gantt_container"); }
                $w.html(
                    '<div class="alert alert-warning m-3">Failed to load Gantt chart: ' +
                    _.escape(String(err || "unknown error")) + '</div>'
                );
            });
        },

        _renderGantt: function () {
            var self = this;
            // Reset locked sidebar width so it's recalculated for new content
            this._ganttSidebarW = 0;
            this._ganttSidebarKey = null;
            var $container = this.$(".o_gantt_timeline_wrapper");
            if (!$container.length) {
                $container = this.$(".o_gantt_container");
            }
            if (!$container.length) {
                return;
            }
            $container.empty().removeClass("o_gantt_cols_off");
            this._ganttSyncToolbar();

            var unscheduledMatches = this._ganttUnscheduledMatches();
            if (!this.ganttData || (!(this.ganttData.tasks || []).length && !unscheduledMatches.length)) {
                $container.html(
                    '<div class="o_gantt_empty_state text-center p-5">' +
                    '<i class="fa fa-calendar-o fa-3x text-muted mb-3 d-block" />' +
                    '<h5 class="text-muted">No tasks with planned dates</h5>' +
                    '<p class="text-muted">Set planned start/end dates on tasks to see them on the Gantt chart.</p>' +
                    '</div>'
                );
                this._ganttKeepWindow = null;
                this._ganttShownTasks = [];
                this._ganttUpdateNoDatesCount();
                return;
            }

            var allTasks = this.ganttData.tasks || [];
            var taskMap = {};
            allTasks.forEach(function (t) { taskMap[t.id] = t; });

            // Apply faceted filters
            var facets = this.ganttFacets || [];
            var tasks;
            if (facets.length) {
                var matchIds = {};
                allTasks.forEach(function (t) {
                    if (self._ganttMatchesFacets(t)) {
                        matchIds[t.id] = true;
                        if (t.parent_id && taskMap[t.parent_id]) {
                            matchIds[t.parent_id] = true;
                        }
                        (t.child_ids || []).forEach(function (cid) {
                            if (taskMap[cid]) { matchIds[cid] = true; }
                        });
                    }
                });
                tasks = allTasks.filter(function (t) { return matchIds[t.id]; });
            } else {
                tasks = allTasks;
            }

            this._ganttShownTasks = tasks;
            if (!tasks.length && !unscheduledMatches.length) {
                this._destroyGantt();
                $container.html(
                    '<div class="o_gantt_empty_state text-center p-5">' +
                    '<i class="fa fa-search fa-3x text-muted mb-3 d-block" />' +
                    '<h5 class="text-muted">No matching tasks</h5>' +
                    '<p class="text-muted">Try a different search term.</p>' +
                    '</div>'
                );
                return;
            }

            // Check if multi-project mode: prefix task labels with project name
            var isMultiProject = this.ganttProjectIds.length > 1;
            this._ganttAnalysis = this._ganttAnalyzeDependencies(tasks);
            this._ganttCanLink = allTasks.some(function (t) { return t.can_write; });
            var showStageHistory = this.ganttShowStageHistory;
            this._ganttHistoryEdges = [];
            var colorSourceTasks = allTasks.slice();
            if (showStageHistory) {
                allTasks.forEach(function (t) {
                    (t.stage_history || []).forEach(function (visit) {
                        colorSourceTasks.push({
                            stage_id: visit.stage_id,
                            stage_name: visit.stage_name,
                            stage_sequence: visit.stage_sequence,
                            is_closed: visit.is_closed,
                        });
                    });
                });
            }
            this._ganttStageColors = {};
            var stageColorList = assignStageColors(colorSourceTasks);
            stageColorList.forEach(function (stage) {
                self._ganttStageColors[stage.stageId] = [stage.solid, stage.light];
            });
            this._ganttRenderLegend(stageColorList);

            // Build groups and items
            var groups = [];
            var items = [];
            var orderIdx = 0;

            if (this.ganttGroupBy === "user") {
                // ---- Group by User mode ----
                // Collect users and their tasks
                var userTasksMap = {};  // userId -> [task, ...]
                var userNames = {};     // userId -> name
                var unassigned = [];

                tasks.forEach(function (t) {
                    var users = t.user_list || [];
                    if (!users.length) {
                        unassigned.push(t);
                    } else {
                        users.forEach(function (u) {
                            if (!userTasksMap[u.id]) {
                                userTasksMap[u.id] = [];
                                userNames[u.id] = u.name;
                            }
                            userTasksMap[u.id].push(t);
                        });
                    }
                });

                var addUserTaskRows = function (userTasks, uid) {
                    var tree = self._ganttAddTaskTree(userTasks, {
                        groups: groups,
                        items: items,
                        orderIdx: orderIdx,
                        baseLevel: 1,
                        rowId: function (taskId) { return "ut_" + uid + "_" + taskId; },
                        scope: uid,
                        taskMap: taskMap,
                        isMultiProject: isMultiProject,
                        showStageHistory: showStageHistory,
                    });
                    orderIdx = tree.orderIdx;
                    return tree.rootIds;
                };

                // Sort users by name
                var userIds = Object.keys(userTasksMap).sort(function (a, b) {
                    return userNames[a].localeCompare(userNames[b]);
                });

                userIds.forEach(function (uid) {
                    var userGroupId = "user_" + uid;
                    var userTasks = userTasksMap[uid];
                    userTasks.sort(function (a, b) {
                        return (a.planned_date_start || "").localeCompare(b.planned_date_start || "");
                    });
                    var userGroup = {
                        id: userGroupId,
                        content: '<span class="o_gantt_label_name"><i class="fa fa-user me-1"></i> ' +
                            _.escape(userNames[uid]) + "</span>",
                        order: orderIdx++,
                        showNested: true,
                    };
                    groups.push(userGroup);
                    userGroup.nestedGroups = addUserTaskRows(userTasks, uid);
                });

                // Unassigned tasks
                if (unassigned.length) {
                    var unassignedGroupId = "user_unassigned";
                    unassigned.sort(function (a, b) {
                        return (a.planned_date_start || "").localeCompare(b.planned_date_start || "");
                    });
                    var unassignedGroup = {
                        id: unassignedGroupId,
                        content: '<span class="o_gantt_label_name"><i class="fa fa-user-o me-1"></i> Unassigned</span>',
                        order: orderIdx++,
                        showNested: true,
                    };
                    groups.push(unassignedGroup);
                    unassignedGroup.nestedGroups = addUserTaskRows(unassigned, 0);
                }
            } else if (this.ganttGroupBy === "stage") {
                groupTasksByStage(tasks).forEach(function (stage) {
                    var stageGroup = {
                        id: "stage_" + stage.stageId,
                        content: '<span class="o_gantt_label_name"><i class="fa fa-columns me-1"></i> ' +
                            _.escape(stage.name) + ' <span class="text-muted">(' + stage.tasks.length +
                            ")</span></span>",
                        order: orderIdx++,
                        showNested: true,
                    };
                    groups.push(stageGroup);
                    var tree = self._ganttAddTaskTree(stage.tasks, {
                        groups: groups,
                        items: items,
                        orderIdx: orderIdx,
                        baseLevel: 1,
                        taskMap: taskMap,
                        isMultiProject: isMultiProject,
                        showStageHistory: showStageHistory,
                    });
                    orderIdx = tree.orderIdx;
                    stageGroup.nestedGroups = tree.rootIds;
                });
            } else {
                var tasksByStart = tasks.slice().sort(function (a, b) {
                    return (a.planned_date_start || "").localeCompare(b.planned_date_start || "");
                });
                orderIdx = this._ganttAddTaskTree(tasksByStart, {
                    groups: groups,
                    items: items,
                    orderIdx: orderIdx,
                    baseLevel: 0,
                    taskMap: taskMap,
                    isMultiProject: isMultiProject,
                    showStageHistory: showStageHistory,
                }).orderIdx;
            }

            if (this.ganttShowBaseline) {
                items = items.concat(this._ganttBaselineItems(items));
            }
            orderIdx = this._ganttAddUnscheduledGroups(groups, orderIdx, isMultiProject);

            // ---- Requirement 5: Detect overlapping tasks (user groupBy mode) ----
            var overlaps = {};
            var marksOverlaps = this.ganttGroupBy === "user" && !showStageHistory;
            this.$(".o_gantt_legend_overlap_item").toggleClass("d-none", !marksOverlaps);
            if (marksOverlaps) {
                var userBarsForOverlap = {};
                items.forEach(function (it) {
                    // Extract user id from item id "ut_<uid>_<taskId>"
                    if (typeof it.id === "string" && it.id.indexOf("ut_") === 0) {
                        var parts = it.id.split("_");
                        var uid = parts[1];
                        if (uid === "0") { return; } // skip unassigned
                        if (!userBarsForOverlap[uid]) {
                            userBarsForOverlap[uid] = [];
                        }
                        userBarsForOverlap[uid].push({
                            id: it.id, taskId: parseInt(parts[2], 10), start: it.start, end: it.end,
                        });
                    }
                });

                Object.keys(userBarsForOverlap).forEach(function (uid) {
                    _.extend(overlaps, findBarOverlaps(userBarsForOverlap[uid], taskMap));
                });

                // Outline each overlapping bar and name the tasks it overlaps in its tooltip
                items.forEach(function (it) {
                    var overlappedTaskIds = overlaps[it.id];
                    if (overlappedTaskIds) {
                        it.className = (it.className || "") + " gantt-overlap";
                        it.title += "<br/>Overlaps: " + overlappedTaskIds.map(function (taskId) {
                            var task = taskMap[taskId];
                            return _.escape(task.key || task.name);
                        }).join(", ");
                    }
                });
            }

            // Compute date range
            var allStarts = items.map(function (i) { return new Date(i.start); });
            var allEnds = items.map(function (i) { return new Date(i.end || i.start); });
            var validStarts = allStarts.filter(function (d) { return !isNaN(d); });
            var validEnds = allEnds.filter(function (d) { return !isNaN(d); });
            var minDate = validStarts.length ? new Date(Math.min.apply(null, validStarts)) : new Date();
            var maxDate = validEnds.length ? new Date(Math.max.apply(null, validEnds)) : new Date();
            minDate.setDate(minDate.getDate() - 7);
            maxDate.setDate(maxDate.getDate() + 14);
            var panBounds = ganttPanBounds(minDate, maxDate);

            var groupsDS = new vis.DataSet(groups);
            var itemsDS = new vis.DataSet(items);
            this._ganttGroupsDS = groupsDS;
            this._ganttItemsDS = itemsDS;

            // Scale-dependent options
            var timeAxisScale, timeAxisStep;
            if (this.ganttScale === "month") {
                timeAxisScale = "month";
                timeAxisStep = 1;
            } else if (this.ganttScale === "day") {
                timeAxisScale = "day";
                timeAxisStep = 1;
            } else {
                timeAxisScale = "week";
                timeAxisStep = 1;
            }

            // Calculate available height so vis-timeline uses internal scroll
            // with the time-axis header pinned at the top.
            var $wrapper = this.$(".o_gantt_timeline_wrapper");
            var wrapperTop = $wrapper.length ? $wrapper[0].getBoundingClientRect().top : 200;
            var availableHeight = Math.max(400, window.innerHeight - wrapperTop - 16);

            var options = {
                orientation: {axis: "top"},
                showCurrentTime: true,
                stack: false,
                zoomKey: "ctrlKey",
                horizontalScroll: true,
                verticalScroll: true,
                maxHeight: availableHeight,
                margin: {item: {horizontal: 0, vertical: 4}},
                start: minDate,
                end: maxDate,
                min: panBounds.min,
                max: panBounds.max,
                groupOrder: function (a, b) { return a.order - b.order; },
                tooltip: {followMouse: true, overflowMethod: "cap"},
                editable: {
                    updateTime: true,
                    updateGroup: false,
                    add: false,
                    remove: false,
                },
                selectable: true,
                snap: null,
                timeAxis: {scale: timeAxisScale, step: timeAxisStep},
                xss: {filterOptions: {
                    whiteList: {
                        span: ["class", "title", "style", "data-task-id"],
                        i: ["class"],
                        br: [],
                    },
                }},
                dataAttributes: ['id'],
                onMove: function (item, callback) {
                    self._onGanttItemMove(item, callback);
                },
            };
            if (this._ganttKeepWindow) {
                options.start = this._ganttKeepWindow.start;
                options.end = this._ganttKeepWindow.end;
                this._ganttKeepWindow = null;
            }

            this._destroyGantt();
            var container = $container[0];
            keepOpenTaskOutOfGroupToggle(container);
            this.ganttTimeline = new vis.Timeline(container, itemsDS, groupsDS, options);

            // --- Dependency arrows ---
            // Build edge list: [{from: itemId, to: itemId}, ...]
            // depend_on_ids = tasks that block this task
            // So arrow: depend_on_id (blocker) --> this task (blocked)
            this._ganttEdges = [];
            var itemIdSet = {};
            // taskItemsByUser: { realTaskId: { uid: itemId, ... } }
            var taskItemsByUser = {};
            items.forEach(function (it) {
                itemIdSet[it.id] = true;
                var realId = it.id;
                var uid = "__all__";
                if (typeof it.id === "string" && it.id.indexOf("ut_") === 0) {
                    var parts = it.id.split("_");
                    uid = parts[1];
                    realId = parseInt(parts[2], 10);
                }
                if (!taskItemsByUser[realId]) {
                    taskItemsByUser[realId] = {};
                }
                taskItemsByUser[realId][uid] = it.id;
            });

            tasks.forEach(function (t) {
                var deps = t.depend_on_ids || [];
                deps.forEach(function (blockerId) {
                    if (self.ganttGroupBy !== "user") {
                        if (itemIdSet[blockerId] && itemIdSet[t.id]) {
                            self._ganttEdges.push({from: blockerId, to: t.id, pred: blockerId, succ: t.id});
                        }
                    } else {
                        // User mode: prefer same-user pair, fallback to any pair
                        var fromMap = taskItemsByUser[blockerId] || {};
                        var toMap = taskItemsByUser[t.id] || {};
                        var fromUids = Object.keys(fromMap);
                        var toUids = Object.keys(toMap);
                        if (!fromUids.length || !toUids.length) { return; }

                        // Find shared users (same user has both tasks)
                        var sharedUids = fromUids.filter(function (u) {
                            return toMap[u] !== undefined;
                        });

                        if (sharedUids.length) {
                            // Draw one arrow per shared user (within that user's section)
                            sharedUids.forEach(function (u) {
                                self._ganttEdges.push({from: fromMap[u], to: toMap[u], pred: blockerId, succ: t.id});
                            });
                        } else {
                            // Different users - draw one cross-user arrow
                            self._ganttEdges.push({
                                from: fromMap[fromUids[0]],
                                to: toMap[toUids[0]],
                                pred: blockerId,
                                succ: t.id,
                            });
                        }
                    }
                });
            });

            // Create SVG overlay for arrows once vis-timeline finishes initial render
            this.ganttTimeline.once("changed", function () {
                self._ganttDrawArrows();
            });

            // Redraw arrows on any viewport change
            this.ganttTimeline.on("rangechanged", function () {
                self._ganttDrawArrows();
            });
            this.ganttTimeline.on("changed", function () {
                self._ganttDrawArrows();
                self._ganttCapSidebarWidth();
                self._ganttCenterItems();
                self._ganttMarkGroupBorders();
                self._ganttRenderColumnHeader();
            });
            this._ganttBindDependencyDrag(container);

            // Update the tasks-without-dates count badge
            this._ganttUpdateNoDatesCount();
        },

        /**
         * Draw SVG dependency arrows on the gantt chart.
         * Positions arrows from the end of the blocker item to the start of the blocked item.
         */
        _ganttDrawArrows: function () {
            // Remove existing SVG
            this.$(".o_gantt_arrows_svg").remove();

            var historyEdges = this._ganttHistoryEdges || [];
            if (!this.ganttTimeline || ((!this._ganttEdges || !this._ganttEdges.length) && !historyEdges.length)) {
                return;
            }

            var self = this;
            var $foreground = this.$(".vis-center .vis-content");
            if (!$foreground.length) { return; }

            // Create SVG element overlaying the content area
            var contentEl = $foreground[0];
            var contentRect = contentEl.getBoundingClientRect();

            var svgNS = "http://www.w3.org/2000/svg";
            var svg = document.createElementNS(svgNS, "svg");
            svg.setAttribute("class", "o_gantt_arrows_svg");
            svg.style.position = "absolute";
            svg.style.top = "0";
            svg.style.left = "0";
            svg.style.width = "100%";
            svg.style.height = "100%";
            svg.style.pointerEvents = "none";
            svg.style.overflow = "visible";
            svg.style.zIndex = "3";

            var defs = document.createElementNS(svgNS, "defs");
            Object.keys(this._GANTT_ARROW_STYLES).forEach(function (styleKey) {
                var marker = document.createElementNS(svgNS, "marker");
                marker.setAttribute("id", "gantt-arrowhead-" + styleKey);
                marker.setAttribute("markerWidth", "8");
                marker.setAttribute("markerHeight", "6");
                marker.setAttribute("refX", "8");
                marker.setAttribute("refY", "3");
                marker.setAttribute("orient", "auto");
                marker.setAttribute("markerUnits", "strokeWidth");
                var arrowPath = document.createElementNS(svgNS, "path");
                arrowPath.setAttribute("d", "M0,0 L8,3 L0,6 Z");
                arrowPath.setAttribute("fill", self._GANTT_ARROW_STYLES[styleKey].color);
                marker.appendChild(arrowPath);
                defs.appendChild(marker);
            });
            svg.appendChild(defs);

            var edgeInfoMap = (this._ganttAnalysis && this._ganttAnalysis.edges) || {};
            (this._ganttEdges || []).forEach(function (edge) {
                var fromEl = contentEl.querySelector('.vis-item[data-id="' + edge.from + '"]');
                var toEl = contentEl.querySelector('.vis-item[data-id="' + edge.to + '"]');
                if (!fromEl || !toEl) { return; }

                var fromRect = fromEl.getBoundingClientRect();
                var toRect = toEl.getBoundingClientRect();
                var x1 = fromRect.right - contentRect.left;
                var y1 = fromRect.top + fromRect.height / 2 - contentRect.top;
                var x2 = toRect.left - contentRect.left;
                var y2 = toRect.top + toRect.height / 2 - contentRect.top;
                var d = elbowPath(x1, y1, x2, y2);

                var info = edgeInfoMap[edge.pred + "_" + edge.succ] || {lag: 0};
                var styleKey = info.violated ? "violated" : "normal";
                var arrowStyle = self._GANTT_ARROW_STYLES[styleKey];
                var path = document.createElementNS(svgNS, "path");
                path.setAttribute("d", d);
                path.setAttribute("fill", "none");
                path.setAttribute("stroke", arrowStyle.color);
                path.setAttribute("stroke-width", arrowStyle.width);
                if (arrowStyle.dash) {
                    path.setAttribute("stroke-dasharray", arrowStyle.dash);
                }
                path.setAttribute("marker-end", "url(#gantt-arrowhead-" + styleKey + ")");
                svg.appendChild(path);

                var hitPath = document.createElementNS(svgNS, "path");
                hitPath.setAttribute("d", d);
                hitPath.setAttribute("class", "o_gantt_dep_hit");
                var titleEl = document.createElementNS(svgNS, "title");
                titleEl.textContent = self._ganttDependencyTitle(edge, info);
                hitPath.appendChild(titleEl);
                hitPath.addEventListener("click", function (ev) {
                    self._ganttOpenDependencyPopover(edge, info, ev);
                });
                svg.appendChild(hitPath);
            });

            var historyStyle = this._GANTT_ARROW_STYLES.normal;
            historyEdges.forEach(function (edge) {
                var fromEl = contentEl.querySelector('.vis-item[data-id="' + edge.from + '"]');
                var toEl = contentEl.querySelector('.vis-item[data-id="' + edge.to + '"]');
                if (!fromEl || !toEl) { return; }
                var fromRect = fromEl.getBoundingClientRect();
                var toRect = toEl.getBoundingClientRect();
                var path = document.createElementNS(svgNS, "path");
                path.setAttribute("d", elbowPath(
                    fromRect.right - contentRect.left,
                    fromRect.top + fromRect.height / 2 - contentRect.top,
                    toRect.left - contentRect.left,
                    toRect.top + toRect.height / 2 - contentRect.top
                ));
                path.setAttribute("fill", "none");
                path.setAttribute("stroke", historyStyle.color);
                path.setAttribute("stroke-width", historyStyle.width);
                path.setAttribute("marker-end", "url(#gantt-arrowhead-normal)");
                svg.appendChild(path);
            });

            contentEl.appendChild(svg);
        },

        /**
         * Persist a dragged or resized bar, by whole days. The payload pads a missing planned date
         * to draw the bar: a move saves only the dates the task has, a resize also the edge it dragged.
         */
        _onGanttItemMove: function (item, callback) {
            var self = this;
            // Extract real task ID (handles "ut_<uid>_<taskId>" format)
            var taskId = item.id;
            if (typeof taskId === "string" && taskId.indexOf("sh_") === 0) {
                callback(null);
                return;
            }
            if (typeof taskId === "string" && taskId.indexOf("ut_") === 0) {
                var parts = taskId.split("_");
                taskId = parseInt(parts[parts.length - 1], 10);
            }
            var task = taskId && _.findWhere(this.ganttData.tasks, {id: taskId});
            if (!task) {
                callback(null);
                return;
            }

            var original = this._ganttItemsDS.get(item.id);
            var startOffset = item.start - original.start;
            var startDays = wholeDaysBetween(original.start, item.start);
            var endDays = item.end ? wholeDaysBetween(original.end, item.end) : startDays;
            item.start = shiftByWholeDays(original.start, item.start);
            var movedEnd;
            if (item.end) {
                item.end = shiftByWholeDays(original.end, item.end);
                movedEnd = item.end;
            } else {
                // a milestone point is drawn on its start alone: its end moves by as many days
                var plannedEnd = utcDateTimeToDate(task.planned_date_end);
                movedEnd = shiftByWholeDays(plannedEnd, new Date(plannedEnd.getTime() + startOffset));
            }
            var isResize = startDays !== endDays;
            var vals = {};
            if (task.has_planned_date_start || (isResize && startDays !== 0)) {
                vals.planned_date_start = formatUtcDateTime(item.start.getTime());
            }
            if (task.has_planned_date_end || (isResize && endDays !== 0)) {
                vals.planned_date_end = formatUtcDateTime(movedEnd.getTime());
            }

            this._rpc({
                model: "project.task",
                method: "write",
                args: [[taskId], vals],
            }).then(function () {
                callback(item);
                _.defer(function () {
                    self._loadGanttData(true);
                });
            }).guardedCatch(function () {
                // Revert on error
                callback(null);
            });
        },

        _ganttGroupLabel: function (task, depth, options) {
            var prefix = options.prefix;
            var isUnscheduled = !!options.isUnscheduled;
            var openIcon = '<span class="o_gantt_open_task" data-task-id="' + task.id +
                '" title="Open task"><i class="fa fa-external-link"></i></span>';
            var key = task.key ? '<span class="gantt-task-key">' + _.escape(task.key) + '</span> ' : "";
            var prefixHtml = prefix ? '<span class="text-primary small me-1">' + _.escape(prefix) + '</span>' : "";
            var name = _.escape(task.name);
            var fullTitle = _.escape((prefix || "") + (task.key ? task.key + " - " : "") + task.name);
            var nameClass = isUnscheduled ? ' class="o_gantt_unscheduled_name" data-task-id="' + task.id + '"' : "";
            var label = '<span' + nameClass + ' title="' + fullTitle + '">' + prefixHtml + key + name + '</span>';
            if (options.outsideParentLabel) {
                label = openIcon + ' ' + label + ' ' + subtaskOfMarker(options.outsideParentLabel);
            } else if (depth > 0 && !options.hasChildren && !isUnscheduled) {
                label = '<span class="gantt-subtask-indicator">\u21b3</span> ' + openIcon + ' ' + label;
            } else {
                label = openIcon + ' ' + label;
            }
            return '<span class="o_gantt_label_name">' + label + '</span>' + this._ganttColumnCells(task);
        },

        _GANTT_COLUMNS: [
            {
                key: "assignees", label: "Assignees", name: "Assignees", width: 120,
                help: "Assignees: people the task is assigned to",
            },
            {
                key: "deadline", label: "Deadline", name: "Deadline", width: 90,
                help: "Deadline: date the task is due, in red when overdue and the task is still open",
            },
            {
                key: "planned_hours", label: "Estimate h", name: "Estimate", width: 84, numeric: true,
                help: "Estimate: the hours the task is expected to take",
            },
            {
                key: "spent_hours", label: "Spent h", name: "Spent hours", width: 72, numeric: true,
                help: "Spent hours: hours logged in timesheets",
            },
            {
                key: "stage", label: "Stage", name: "Stage", width: 100,
                help: "Stage: current stage of the task",
            },
        ],

        _GANTT_LABEL_MIN_WIDTH: 140,

        _ganttCellClass: function (col) {
            return "o_gantt_cell" + (col.numeric ? " o_gantt_cell_num" : "");
        },

        _ganttShownColumnsWidth: function (containerW) {
            var columnsW = this._ganttActiveColumns().reduce(function (sum, col) { return sum + col.width; }, 0);
            return Math.floor(containerW * 0.6) >= columnsW + this._GANTT_LABEL_MIN_WIDTH ? columnsW : 0;
        },

        _GANTT_GROUP_COLUMN: {user: "assignees", stage: "stage"},

        _ganttActiveColumns: function () {
            var chosen = this.ganttColumns || [];
            var groupColumn = this._GANTT_GROUP_COLUMN[this.ganttGroupBy];
            return this._GANTT_COLUMNS.filter(function (col) {
                return col.key !== groupColumn && chosen.indexOf(col.key) !== -1;
            });
        },

        _ganttColumnCells: function (task) {
            var self = this;
            var today = (this.ganttData && this.ganttData.today) || "";
            return this._ganttActiveColumns().map(function (col) {
                var text = "";
                var cellClass = self._ganttCellClass(col);
                if (col.key === "assignees") {
                    text = task.user_names || "";
                } else if (col.key === "deadline" && task.date_deadline) {
                    text = self._ganttFormatDate(task.date_deadline);
                    if (!task.is_closed && task.date_deadline < today) {
                        cellClass += " text-danger fw-bold";
                    }
                } else if (col.key === "planned_hours") {
                    text = task.planned_hours ? task.planned_hours.toFixed(1) : "";
                } else if (col.key === "spent_hours") {
                    text = task.effective_hours ? task.effective_hours.toFixed(1) : "";
                } else if (col.key === "stage") {
                    text = task.stage_name || "";
                }
                var titleAttr = text ? ' title="' + _.escape(col.name + ": " + text) + '"' : "";
                return '<span class="' + cellClass + '" style="width:' + col.width + 'px;"' + titleAttr + '>' +
                    _.escape(text) + '</span>';
            }).join("");
        },

        _ganttLocalMidnight: function (isoDate) {
            var parts = isoDate.split("-");
            return new Date(+parts[0], +parts[1] - 1, +parts[2]);
        },

        _ganttFormatDate: function (isoDate) {
            if (window.moment) {
                return window.moment(isoDate, "YYYY-MM-DD").format("DD MMM YY");
            }
            return isoDate;
        },

        // Stage color map: [solidColor, lightColor]
        _GANTT_STAGE_COLORS: {
            "gantt-in-progress": ["#2ecb71", "rgba(46,203,113,0.3)"],
            "gantt-backlog": ["#5dade2", "rgba(93,173,226,0.3)"],
            "gantt-overdue": ["#ec7063", "rgba(236,112,99,0.3)"],
            "gantt-other": ["#aab7b8", "rgba(170,183,184,0.3)"],
        },

        _GANTT_ARROW_STYLES: {
            normal: {color: "#f39c12", width: "2", dash: ""},
            violated: {color: "#e74c3c", width: "2", dash: ""},
        },

        _ganttItem: function (task, prefix, hasSubtaskRows) {
            var barRules = ganttBarRules(task, hasSubtaskRows);
            var isMilestone = barRules.isPoint;
            var className = "gantt-stage";
            if (task.is_overdue && !task.is_closed) {
                className += " gantt-overdue";
            }
            var stageColors = (this._ganttStageColors || {})[task.stage_id || 0];
            var colors = stageColors || this._GANTT_STAGE_COLORS["gantt-other"];
            if (isMilestone) {
                className += " gantt-milestone";
            }

            // Build tooltip
            var tooltip = _.escape(task.key ? task.key + " - " + task.name : task.name);
            if (prefix) {
                tooltip = _.escape(prefix) + tooltip;
            }
            if (task.user_names) {
                tooltip += "\nAssigned: " + _.escape(task.user_names);
            }
            tooltip += "\nStage: " + _.escape(task.stage_name);
            if (task.is_overdue && !task.is_closed) {
                tooltip += "\nOverdue";
            }
            if (task.task_type === "milestone") {
                tooltip += "\nType: Milestone";
            }
            if (task.planned_hours > 0) {
                var pct = Math.round(task.progress);
                tooltip += "\nProgress: " + pct + "% - " + task.remaining_hours.toFixed(1) + "h remaining";
            }
            if (task.is_deadline_only) {
                tooltip += "\nDeadline: " + task.date_deadline + " (no planned dates)";
            } else {
                tooltip += "\n" + utcDateTimeToLocalDay(task.planned_date_start) +
                    (isMilestone ? "" : " \u2192 " + utcDateTimeToLocalDay(task.planned_date_end));
            }
            if (typeof task.baseline_shift_days === "number") {
                var shift = task.baseline_shift_days;
                tooltip += "\n" + (shift > 0 ? "+" : "") + shift + " d vs baseline";
            }

            if (isMilestone) {
                return {
                    id: task.id,
                    group: task.id,
                    start: task.is_deadline_only
                        ? this._ganttLocalMidnight(task.date_deadline)
                        : utcDateTimeToDate(task.planned_date_start),
                    type: "point",
                    content: '<span class="gantt-milestone-diamond" style="background:' + colors[0] + ';"></span>',
                    className: className,
                    title: tooltip.replace(/\n/g, "<br/>"),
                    editable: {updateTime: barRules.canDrag, updateGroup: false, remove: false},
                };
            }

            // Regular task: range bar with progress gradient
            var pct = Math.min(100, Math.max(0, Math.round(task.progress || 0)));
            var solid = colors[0];
            var light = colors[1];
            var bgStyle = "background: linear-gradient(to right, " +
                solid + " 0%, " + solid + " " + pct + "%, " +
                light + " " + pct + "%, " + light + " 100%) !important;";

            var content = "";
            if (task.planned_hours > 0) {
                content = '<span class="gantt-progress-label">' + pct + '%</span>';
            }
            if (this._ganttCanLink) {
                content += '<span class="o_gantt_dep_handle" title="Drag onto another task to make it depend on this one"></span>';
            }

            return {
                id: task.id,
                group: task.id,
                start: utcDateTimeToDate(task.planned_date_start),
                end: utcDateTimeToDate(task.planned_date_end),
                content: content,
                className: className,
                style: bgStyle,
                title: tooltip.replace(/\n/g, "<br/>"),
                editable: {updateTime: barRules.canDrag, updateGroup: false, remove: false},
            };
        },

        _onGanttScaleChange: function (ev) {
            var scale = $(ev.currentTarget).data("scale");
            if (scale === this.ganttScale) {
                return;
            }
            this.ganttScale = scale;
            this.$(".o_gantt_scale").removeClass("active");
            $(ev.currentTarget).addClass("active");
            if (this.ganttData) {
                this._renderGantt();
            }
        },

        _onGanttFit: function () {
            if (this.ganttTimeline) {
                this.ganttTimeline.fit();
            }
        },

        _onGanttToday: function () {
            if (this.ganttTimeline) {
                this.ganttTimeline.moveTo(new Date());
            }
        },

        _ganttRenderLegend: function (stageColorList) {
            var $stages = this.$(".o_gantt_legend_stages").empty();
            stageColorList.forEach(function (stage) {
                $stages.append(
                    '<span class="d-flex align-items-center gap-1">' +
                    '<span class="o_gantt_legend_dot" style="background:' + stage.solid + ';"></span>' +
                    _.escape(stage.name) + "</span>"
                );
            });
        },

        _onGanttShift: function (ev) {
            if (!this.ganttTimeline) {
                return;
            }
            var window_ = this.ganttTimeline.getWindow();
            var shifted = shiftGanttWindow(
                window_.start, window_.end, this.ganttScale, $(ev.currentTarget).data("direction")
            );
            this.ganttTimeline.setWindow(shifted.start, shifted.end);
        },

        _onGanttShowCompleted: function (ev) {
            this.ganttShowCompleted = !this.ganttShowCompleted;
            $(ev.currentTarget).toggleClass("active", this.ganttShowCompleted);
            this._loadGanttData();
        },

        // ---- Export Excel Gantt ----

        _onGanttExportExcel: function (ev) {
            ev.preventDefault();
            var self = this;
            var projectIds = this.ganttProjectIds && this.ganttProjectIds.length
                ? this.ganttProjectIds
                : (this.projectId ? [this.projectId] : []);
            if (!projectIds.length) {
                this.displayNotification({
                    title: "Export",
                    message: "Please select a project first.",
                    type: "warning",
                });
                return;
            }
            this._rpc({
                model: "project.project",
                method: "export_gantt_excel",
                args: [projectIds],
            }).then(function (result) {
                if (result.error) {
                    self.displayNotification({
                        title: "Export",
                        message: result.error,
                        type: "warning",
                    });
                    return;
                }
                // Download the file
                var byteString = atob(result.data);
                var ab = new ArrayBuffer(byteString.length);
                var ia = new Uint8Array(ab);
                for (var i = 0; i < byteString.length; i++) {
                    ia[i] = byteString.charCodeAt(i);
                }
                var blob = new Blob([ab], {type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"});
                var url = URL.createObjectURL(blob);
                var a = document.createElement("a");
                a.href = url;
                a.download = result.filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            });
        },

        _onGanttGroupByChange: function (ev) {
            var group = $(ev.currentTarget).data("group");
            if (group === this.ganttGroupBy) {
                return;
            }
            this.ganttGroupBy = group;
            this.$(".o_gantt_group_by").removeClass("active");
            $(ev.currentTarget).addClass("active");
            if (this.ganttData) {
                this._renderGantt();
            }
        },

        _onGanttOpenTask: function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var taskId = parseInt($(ev.currentTarget).data("task-id"), 10);
            if (!taskId) { return; }
            this.do_action({
                type: "ir.actions.act_window",
                res_model: "project.task",
                res_id: taskId,
                views: [[false, "form"]],
                target: "current",
            });
        },

        _onGanttSearchInput: function () {
            var self = this;
            if (this._ganttSearchDebounce) {
                clearTimeout(this._ganttSearchDebounce);
            }
            this._ganttSearchDebounce = setTimeout(function () {
                self._ganttShowDropdown();
            }, 150);
        },

        _onGanttSearchFocus: function () {
            this._ganttShowDropdown();
        },

        _onGanttFacetedInputClick: function () {
            this.$(".o_gantt_search_input").focus();
        },

        _onGanttSearchKeydown: function (ev) {
            // Backspace on empty input removes last facet
            if (ev.which === 8 && !$(ev.currentTarget).val() && this.ganttFacets.length) {
                if (this._ganttSearchField) {
                    // Cancel field selection
                    this._ganttSearchField = null;
                    this._ganttUpdatePlaceholder();
                    this._ganttShowDropdown();
                } else {
                    this.ganttFacets.pop();
                    this._ganttRenderFacets();
                    this._ganttApplyFilter();
                }
                ev.preventDefault();
                return;
            }
            if (ev.which === 13 && !this._ganttSearchField) {
                var first = findGanttTasks(this._ganttShownTasks || [], $(ev.currentTarget).val())[0];
                if (first) {
                    ev.preventDefault();
                    this._ganttGoToTask(first.id);
                }
                return;
            }
            // Escape closes dropdown
            if (ev.which === 27) {
                this._ganttHideDropdown();
                this._ganttSearchField = null;
                this._ganttUpdatePlaceholder();
                $(ev.currentTarget).val("");
            }
        },

        _onGanttSearchFieldSelect: function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var fieldName = $(ev.currentTarget).data("field");
            var fieldDef = this._GANTT_SEARCH_FIELDS.find(function (f) {
                return f.field === fieldName;
            });
            if (fieldDef) {
                this._ganttSearchField = fieldDef;
                this.$(".o_gantt_search_input").val("").attr(
                    "placeholder", fieldDef.label + "\u2026"
                ).focus();
                this._ganttShowDropdown();
            }
        },

        _onGanttSearchValueSelect: function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var value = $(ev.currentTarget).data("value");
            var field = this._ganttSearchField;
            if (field && value) {
                this.ganttFacets.push({
                    field: field.field,
                    fieldLabel: field.label,
                    value: String(value),
                });
                this._ganttSearchField = null;
                this.$(".o_gantt_search_input").val("");
                this._ganttUpdatePlaceholder();
                this._ganttRenderFacets();
                this._ganttHideDropdown();
                this._ganttApplyFilter();
            }
        },

        _onGanttSearchTaskSelect: function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            this._ganttGoToTask(parseInt($(ev.currentTarget).data("task-id"), 10));
        },

        _ganttGoToTask: function (taskId) {
            this._ganttHideDropdown();
            this.$(".o_gantt_search_input").val("").blur();
            if (!this.ganttTimeline || !this._ganttItemsDS || !taskId) {
                return;
            }
            var item = this._ganttItemsDS.get({
                filter: function (it) {
                    var userRow = /^ut_\d+_(\d+)$/.exec(String(it.id));
                    return it.id === taskId || (userRow && parseInt(userRow[1], 10) === taskId);
                },
            })[0];
            if (!item) {
                return;
            }
            this.ganttTimeline.setSelection([item.id]);
            this.ganttTimeline.focus(item.id, {zoom: false, animation: {duration: 400}});
            this._ganttFlashGroup(item.group);
        },

        _ganttFlashGroup: function (groupId) {
            var groupsDS = this._ganttGroupsDS;
            var group = groupsDS && groupsDS.get(groupId);
            if (!group) {
                return;
            }
            var baseClass = (group.className || "").replace(/\s*o_gantt_found/g, "");
            groupsDS.update({id: groupId, className: (baseClass + " o_gantt_found").trim()});
            setTimeout(function () {
                if (groupsDS.get(groupId)) {
                    groupsDS.update({id: groupId, className: baseClass});
                }
            }, 2500);
        },

        _onGanttFacetRemove: function (ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var idx = $(ev.currentTarget).closest(".o_gantt_facet").data("index");
            this.ganttFacets.splice(idx, 1);
            this._ganttRenderFacets();
            this._ganttApplyFilter();
        },

        _ganttRenderFacets: function () {
            var $container = this.$(".o_gantt_faceted_input");
            $container.find(".o_gantt_facet").remove();
            var $input = $container.find(".o_gantt_search_input");
            var self = this;
            this.ganttFacets.forEach(function (facet, idx) {
                var $chip = $(
                    '<span class="o_gantt_facet badge d-inline-flex align-items-center gap-1" ' +
                    'data-index="' + idx + '">' +
                    '<span class="o_gantt_facet_label">' + _.escape(facet.fieldLabel) + ':</span> ' +
                    '<span class="o_gantt_facet_value">' + _.escape(facet.value) + '</span>' +
                    '<i class="o_gantt_facet_remove fa fa-times ms-1" style="cursor:pointer;" />' +
                    '</span>'
                );
                $chip.insertBefore($input);
            });
        },

        _ganttUpdatePlaceholder: function () {
            var placeholder = this.ganttFacets.length ? "" : "Search\u2026";
            this.$(".o_gantt_search_input").attr("placeholder", placeholder);
        },

        _ganttShowDropdown: function () {
            var $dd = this.$(".o_gantt_search_dropdown");
            var inputVal = (this.$(".o_gantt_search_input").val() || "").toLowerCase().trim();

            if (!this._ganttSearchField) {
                // Show field list
                var fields = this._GANTT_SEARCH_FIELDS;
                if (inputVal) {
                    fields = fields.filter(function (f) {
                        return f.label.toLowerCase().indexOf(inputVal) !== -1;
                    });
                }
                var found = findGanttTasks(this._ganttShownTasks || [], inputVal);
                if (!fields.length && !found.length) {
                    $dd.removeClass("show").empty();
                    return;
                }
                var html = "";
                if (found.length) {
                    html += '<div class="px-2 py-1 text-muted small fw-bold">Go to task</div>';
                    found.forEach(function (task) {
                        html += '<a href="#" class="dropdown-item o_gantt_search_task small text-truncate" ' +
                            'data-task-id="' + task.id + '">' +
                            '<i class="fa fa-crosshairs me-2 text-muted" />' +
                            (task.key ? '<span class="text-muted me-1">' + _.escape(task.key) + "</span>" : "") +
                            _.escape(task.name || "") + "</a>";
                    });
                }
                if (fields.length) {
                    html += '<div class="px-2 py-1 text-muted small fw-bold">Search in\u2026</div>';
                    fields.forEach(function (f) {
                        html += '<a href="#" class="dropdown-item o_gantt_search_field small" data-field="' +
                            f.field + '">' +
                            '<i class="fa ' + f.icon + ' me-2 text-muted" />' + _.escape(f.label) +
                            '</a>';
                    });
                }
                $dd.html(html).addClass("show");
            } else {
                // Show unique values for the selected field
                var allTasks = (this.ganttData && this.ganttData.tasks) || [];
                var field = this._ganttSearchField.field;
                var seen = {};
                var values = [];
                allTasks.forEach(function (t) {
                    var raw = t[field] || "";
                    // user_names can be comma-separated - split into individual values
                    var parts = field === "user_names" ? raw.split(",") : [raw];
                    parts.forEach(function (p) {
                        var v = p.trim();
                        if (v && !seen[v]) {
                            seen[v] = true;
                            values.push(v);
                        }
                    });
                });
                values.sort(function (a, b) { return a.localeCompare(b); });
                if (inputVal) {
                    values = values.filter(function (v) {
                        return v.toLowerCase().indexOf(inputVal) !== -1;
                    });
                }
                if (!values.length) {
                    $dd.html(
                        '<div class="px-3 py-2 text-muted small">No matches</div>'
                    ).addClass("show");
                    return;
                }
                var html = '<div class="px-2 py-1 text-muted small fw-bold">' +
                    _.escape(this._ganttSearchField.label) + '</div>';
                var displayField = this._ganttSearchField.field;
                values.forEach(function (v) {
                    var displayVal = v;
                    // Capitalize task_type values (stored as lowercase keys like "feature", "bug")
                    if (displayField === "task_type") {
                        displayVal = v.replace(/_/g, " ").replace(/\b\w/g, function (c) {
                            return c.toUpperCase();
                        });
                    }
                    html += '<a href="#" class="dropdown-item o_gantt_search_value small" ' +
                        'data-value="' + _.escape(v) + '">' + _.escape(displayVal) + '</a>';
                });
                $dd.html(html).addClass("show");
            }
        },

        _ganttHideDropdown: function () {
            this.$(".o_gantt_search_dropdown").removeClass("show").empty();
        },

        _ganttApplyFilter: function () {
            if (this.ganttData) {
                this._renderGantt();
            }
            this._ganttUpdatePlaceholder();
        },

        _ganttUpdateNoDatesCount: function () {
            var count = (this.ganttData && this.ganttData.tasks_without_dates) || 0;
            var $badge = this.$(".o_gantt_no_dates_count");
            if (count > 0) {
                $badge.text(count + " task" + (count > 1 ? "s" : "") + " without dates").removeClass("d-none");
            } else {
                $badge.addClass("d-none");
            }
        },

        _onGanttNoDatesClick: function () {
            var pids = this._ganttProjectIdsOrCurrent();
            if (!pids.length) { return; }
            var unscheduledIds = ((this.ganttData && this.ganttData.unscheduled) || []).map(function (t) {
                return t.id;
            });
            var domain = [["id", "in", unscheduledIds]];
            this.do_action({
                type: "ir.actions.act_window",
                name: "Tasks Without Dates",
                res_model: "project.task",
                views: [[false, "list"], [false, "form"]],
                domain: domain,
                target: "current",
            });
        },

        _ganttProjectIdsOrCurrent: function () {
            return this.ganttProjectIds.length ? this.ganttProjectIds : (this.projectId ? [this.projectId] : []);
        },

        _ganttMatchesFacets: function (task) {
            return (this.ganttFacets || []).every(function (facet) {
                var val = (task[facet.field] || "").toLowerCase();
                var search = facet.value.toLowerCase();
                if (facet.field === "key" || facet.field === "task_type") {
                    return val === search;
                }
                return val.indexOf(search) !== -1;
            });
        },

        _ganttTaskName: function (taskId) {
            var task = _.findWhere((this.ganttData && this.ganttData.tasks) || [], {id: taskId});
            if (!task) {
                return "#" + taskId;
            }
            return task.key ? task.key + " " + task.name : task.name;
        },

        _ganttSyncToolbar: function () {
            var self = this;
            var data = this.ganttData || {};
            var $options = this.$(".o_gantt_columns_options").empty();
            var groupColumn = this._GANTT_GROUP_COLUMN[this.ganttGroupBy];
            var groupLabel = this.$('.o_gantt_group_by[data-group="' + this.ganttGroupBy + '"]').text().trim();
            this._GANTT_COLUMNS.forEach(function (col) {
                var isGroupColumn = col.key === groupColumn;
                var $label = $('<label class="dropdown-item d-flex align-items-center gap-2 mb-0 small"/>')
                    .toggleClass("text-muted", isGroupColumn);
                var $input = $('<input type="checkbox" class="form-check-input mt-0 o_gantt_column_toggle"/>')
                    .attr("data-column", col.key)
                    .prop("checked", (self.ganttColumns || []).indexOf(col.key) !== -1)
                    .prop("disabled", isGroupColumn);
                $label.attr("title", isGroupColumn ?
                    col.name + ": hidden while the chart is grouped by " + groupLabel :
                    col.help);
                $label.append($input).append(document.createTextNode(col.name));
                $options.append($label);
            });
            this.$(".o_gantt_show_baseline").toggleClass("active", this.ganttShowBaseline);
            this.$(".o_gantt_stage_history").toggleClass("active", this.ganttShowStageHistory);
            this.$(".o_gantt_save_baseline").toggleClass("d-none", !data.is_project_manager);
        },

        _ganttAnalyzeDependencies: function (tasks) {
            var taskById = {};
            tasks.forEach(function (t) { taskById[t.id] = t; });
            var analysis = {edges: {}};
            tasks.forEach(function (task) {
                var lags = task.depend_on_lags || {};
                (task.depend_on_ids || []).forEach(function (predId) {
                    var pred = taskById[predId];
                    if (!pred) {
                        return;
                    }
                    var lag = lags[predId] || 0;
                    var info = {lag: lag, violated: false};
                    if (pred.plan_date_end && task.plan_date_start) {
                        info.violated = dayNumber(task.plan_date_start) < dayNumber(pred.plan_date_end) + lag;
                    }
                    analysis.edges[predId + "_" + task.id] = info;
                });
            });
            return analysis;
        },

        _ganttBaselineItems: function (items) {
            var self = this;
            var taskById = {};
            this.ganttData.tasks.forEach(function (t) { taskById[t.id] = t; });
            var baselineItems = [];
            items.forEach(function (item) {
                var task = taskById[self._ganttRealTaskId(item.id)];
                if (!task || !task.baseline_date_start || !task.baseline_date_end) {
                    return;
                }
                var title = "Baseline: " + utcDateTimeToLocalDay(task.baseline_date_start) + " \u2192 " +
                    utcDateTimeToLocalDay(task.baseline_date_end);
                baselineItems.push({
                    id: "bl_" + item.id,
                    group: item.group,
                    start: utcDateTimeToDate(task.baseline_date_start),
                    end: utcDateTimeToDate(task.baseline_date_end),
                    content: "",
                    className: "gantt-baseline",
                    title: _.escape(title),
                    editable: false,
                    selectable: false,
                });
            });
            return baselineItems;
        },

        _ganttUnscheduledMatches: function () {
            var self = this;
            return ((this.ganttData && this.ganttData.unscheduled) || []).filter(function (t) {
                return self._ganttMatchesFacets(t);
            });
        },

        _ganttAddUnscheduledGroups: function (groups, orderIdx, isMultiProject) {
            var self = this;
            var entries = this._ganttUnscheduledMatches();
            if (!entries.length) {
                return orderIdx;
            }
            groups.push({
                id: "unscheduled",
                content: '<span class="o_gantt_label_name"><i class="fa fa-calendar-times-o me-1"></i>' +
                    'Unscheduled (' + entries.length + ')</span>',
                order: orderIdx++,
                nestedGroups: entries.map(function (t) { return "us_" + t.id; }),
                showNested: false,
                className: "gantt-unscheduled-group",
            });
            entries.forEach(function (task) {
                var labelPrefix = isMultiProject && task.project_name ? task.project_name + " | " : "";
                groups.push({
                    id: "us_" + task.id,
                    content: self._ganttGroupLabel(task, 1, {prefix: labelPrefix, isUnscheduled: true}),
                    order: orderIdx++,
                    treeLevel: 1,
                });
            });
            return orderIdx;
        },

        _ganttRealTaskId: function (itemId) {
            if (typeof itemId === "number") {
                return itemId;
            }
            var itemKey = String(itemId);
            if (itemKey.indexOf("bl_") === 0 || itemKey.indexOf("us_") === 0 || itemKey.indexOf("sh_") === 0) {
                return null;
            }
            var parts = itemKey.split("_");
            return parseInt(parts[parts.length - 1], 10) || null;
        },

        _ganttRenderColumnHeader: function () {
            var container = this.$(".o_gantt_timeline_wrapper")[0];
            if (!container) { return; }
            var root = container.querySelector(".vis-timeline");
            if (!root) { return; }
            var existing = root.querySelector(".o_gantt_cols_header");
            if (existing) {
                existing.remove();
            }
            var columns = this._ganttActiveColumns();
            var left = root.querySelector(".vis-panel.vis-left");
            var top = root.querySelector(".vis-panel.vis-top");
            var bodyCell = left && left.querySelector(".o_gantt_cell:last-child");
            if (!columns.length || !top || !bodyCell || container.classList.contains("o_gantt_cols_off")) {
                return;
            }
            var header = document.createElement("div");
            header.className = "o_gantt_cols_header";
            header.style.width = (bodyCell.getBoundingClientRect().right -
                root.getBoundingClientRect().left - root.clientLeft) + "px";
            header.style.height = top.offsetHeight + "px";
            var self = this;
            columns.forEach(function (col) {
                var cell = document.createElement("span");
                cell.className = self._ganttCellClass(col);
                cell.style.width = col.width + "px";
                cell.title = col.help;
                cell.textContent = col.label;
                header.appendChild(cell);
            });
            root.appendChild(header);
        },

        _onGanttColumnToggle: function () {
            var self = this;
            var checkedKeys = [];
            this.$(".o_gantt_column_toggle:checked").each(function () {
                checkedKeys.push($(this).data("column"));
            });
            var chosenColumns = this._GANTT_COLUMNS.map(function (col) { return col.key; })
                .filter(function (key) { return checkedKeys.indexOf(key) !== -1; });
            return this._rpc({
                model: "res.users",
                method: "write",
                args: [[this.getSession().uid], {gantt_columns: chosenColumns.join(",")}],
            }).then(function () {
                self.ganttColumns = chosenColumns;
                if (self.ganttData) {
                    self._renderGantt();
                }
            });
        },

        _ganttRenderKeepingWindow: function () {
            this._ganttKeepWindow = this.ganttTimeline ? this.ganttTimeline.getWindow() : null;
            this._renderGantt();
        },

        _onGanttShowBaselineToggle: function () {
            this.ganttShowBaseline = !this.ganttShowBaseline;
            if (!this.ganttData) {
                return;
            }
            this._ganttRenderKeepingWindow();
            var hasBaseline = (this._ganttShownTasks || []).some(function (task) {
                return task.baseline_date_start && task.baseline_date_end;
            });
            if (this.ganttShowBaseline && !hasBaseline) {
                this.displayNotification({
                    title: "Baseline",
                    message: "None of the tasks shown has a baseline yet. A project manager can press Save Baseline to store the current plan.",
                    type: "warning",
                });
            }
        },

        _onGanttStageHistoryToggle: function () {
            this.ganttShowStageHistory = !this.ganttShowStageHistory;
            this._loadGanttData(true);
        },

        _ganttRowItem: function (task, labelPrefix, showStageHistory, hasSubtaskRows) {
            var item = this._ganttItem(task, labelPrefix, hasSubtaskRows);
            var span = showStageHistory && stageHistorySpan(task);
            if (!span) {
                return item;
            }
            var colors = (this._ganttStageColors || {})[task.stage_id || 0] || this._GANTT_STAGE_COLORS["gantt-other"];
            return {
                id: item.id,
                group: item.group,
                start: utcDateTimeToDate(span.start),
                end: utcDateTimeToDate(span.end),
                type: "range",
                content: "",
                className: "gantt-stage gantt-summary",
                style: "background: " + colors[0] + " !important;",
                title: item.title,
                editable: false,
            };
        },

        _ganttAddTaskTree: function (tasks, options) {
            var self = this;
            var orderIdx = options.orderIdx;
            var rowId = options.rowId || function (taskId) { return taskId; };
            var rootIds = [];
            buildTaskTree(tasks).forEach(function (row) {
                var task = row.task;
                var id = rowId(task.id);
                var treeLevel = options.baseLevel + row.depth;
                var labelPrefix = options.isMultiProject && task.project_name ? task.project_name + " | " : "";
                var group = {id: id, order: orderIdx++, treeLevel: treeLevel};
                options.groups.push(group);
                var item = self._ganttRowItem(task, labelPrefix, options.showStageHistory, row.childIds.length > 0);
                item.id = id;
                item.group = id;
                options.items.push(item);
                var historyIds = options.showStageHistory
                    ? self._ganttAddStageHistory(task, options.groups, options.items, orderIdx, treeLevel + 1, options.scope)
                    : [];
                orderIdx += historyIds.length;
                var nestedIds = historyIds.concat(row.childIds.map(rowId));
                group.content = self._ganttGroupLabel(task, treeLevel, {
                    hasChildren: nestedIds.length > 0,
                    prefix: labelPrefix,
                    outsideParentLabel: outsideParentOf(row, options.taskMap),
                });
                if (nestedIds.length) {
                    group.nestedGroups = nestedIds;
                    group.showNested = true;
                }
                if (!row.depth) {
                    rootIds.push(id);
                }
            });
            return {orderIdx: orderIdx, rootIds: rootIds};
        },

        _ganttAddStageHistory: function (task, groups, items, orderIdx, treeLevel, scope) {
            var self = this;
            var history = buildStageHistoryRows(task, scope);
            history.rows.forEach(function (row, index) {
                var colors = (self._ganttStageColors || {})[row.stageId] || self._GANTT_STAGE_COLORS["gantt-other"];
                var title = _.escape(row.stageName) + "<br/>" + self._ganttFormatDateTime(row.start) +
                    " \u2192 " + self._ganttFormatDateTime(row.visitEnd);
                groups.push({
                    id: row.id,
                    content: '<span class="o_gantt_label_name gantt-stage-history-label">' +
                        '<i class="fa fa-history me-1"></i>' + _.escape(row.stageName) + "</span>",
                    order: orderIdx + index,
                    treeLevel: treeLevel,
                });
                items.push({
                    id: row.id,
                    group: row.id,
                    start: utcDateTimeToDate(row.start),
                    end: utcDateTimeToDate(row.end),
                    content: "",
                    className: "gantt-stage-history",
                    style: "background: " + colors[0] + " !important;",
                    title: title,
                    editable: false,
                    selectable: false,
                });
            });
            Array.prototype.push.apply(this._ganttHistoryEdges, history.edges);
            return history.rows.map(function (row) { return row.id; });
        },

        _ganttFormatDateTime: function (value) {
            if (window.moment) {
                return window.moment.utc(value, "YYYY-MM-DD HH:mm:ss").local().format("DD MMM YY HH:mm");
            }
            return value.substring(0, 16);
        },

        _onGanttSaveBaseline: function () {
            var self = this;
            var pids = this._ganttProjectIdsOrCurrent();
            if (!pids.length) { return; }
            var message = pids.length > 1
                ? "Save the current plan of the " + pids.length + " projects shown as their baselines? Each replaces that project's current baseline."
                : "Save the current plan as the project baseline? It replaces the current baseline.";
            Dialog.confirm(this, message, {
                confirm_callback: function () {
                    return self._rpc({
                        model: "project.project",
                        method: "save_gantt_baselines",
                        args: [pids],
                    }).then(function () {
                        self.displayNotification({title: "Baseline", message: "Baseline saved.", type: "success"});
                        self.ganttShowBaseline = true;
                        self._loadGanttData(true);
                    });
                },
            });
        },

        _ganttBindDependencyDrag: function (container) {
            var self = this;
            if (container._ganttDependencyDragBound) {
                return;
            }
            container._ganttDependencyDragBound = true;
            var onPress = function (ev) {
                var handle = ev.target.closest && ev.target.closest(".o_gantt_dep_handle");
                if (!handle) {
                    return;
                }
                ev.stopPropagation();
                if (ev.type === "pointerdown") {
                    ev.preventDefault();
                    self._ganttStartDependencyDrag(handle, ev);
                }
            };
            ["pointerdown", "mousedown", "touchstart"].forEach(function (eventType) {
                container.addEventListener(eventType, onPress, true);
            });
        },

        _ganttStartDependencyDrag: function (handle, startEvent) {
            var self = this;
            var sourceEl = handle.closest(".vis-item");
            var sourceId = sourceEl && this._ganttRealTaskId(sourceEl.getAttribute("data-id"));
            if (!sourceId) {
                return;
            }
            var handleRect = handle.getBoundingClientRect();
            var x1 = handleRect.left + handleRect.width / 2;
            var y1 = handleRect.top + handleRect.height / 2;
            var svgNS = "http://www.w3.org/2000/svg";
            var overlay = document.createElementNS(svgNS, "svg");
            overlay.setAttribute("class", "o_gantt_link_overlay");
            var line = document.createElementNS(svgNS, "line");
            line.setAttribute("x1", x1);
            line.setAttribute("y1", y1);
            line.setAttribute("x2", startEvent.clientX);
            line.setAttribute("y2", startEvent.clientY);
            overlay.appendChild(line);
            document.body.appendChild(overlay);
            document.body.classList.add("o_gantt_linking");

            var onMove = function (ev) {
                line.setAttribute("x2", ev.clientX);
                line.setAttribute("y2", ev.clientY);
            };
            var cleanUp = function () {
                document.removeEventListener("pointermove", onMove);
                document.removeEventListener("pointerup", onUp);
                document.removeEventListener("pointercancel", cleanUp);
                overlay.remove();
                document.body.classList.remove("o_gantt_linking");
            };
            var onUp = function (ev) {
                cleanUp();
                var dropEl = document.elementFromPoint(ev.clientX, ev.clientY);
                var targetEl = dropEl && dropEl.closest(".o_gantt_timeline_wrapper .vis-item");
                var targetId = targetEl && self._ganttRealTaskId(targetEl.getAttribute("data-id"));
                if (targetId && targetId !== sourceId) {
                    self._ganttLinkTasks(sourceId, targetId);
                }
            };
            document.addEventListener("pointermove", onMove);
            document.addEventListener("pointerup", onUp);
            document.addEventListener("pointercancel", cleanUp);
        },

        _ganttLinkTasks: function (predecessorId, successorId) {
            var self = this;
            var successor = _.findWhere(this.ganttData.tasks, {id: successorId});
            if (!successor || !successor.can_write) {
                this.displayNotification({
                    title: "Dependencies",
                    message: "You cannot edit the dependencies of " + this._ganttTaskName(successorId) + ".",
                    type: "warning",
                });
                return;
            }
            if ((successor.depend_on_ids || []).indexOf(predecessorId) !== -1) {
                return;
            }
            this._rpc({
                model: "project.task",
                method: "gantt_add_dependency",
                args: [[successorId], predecessorId],
            }).then(function () {
                self._loadGanttData(true);
            });
        },

        _ganttDependencyTitle: function (edge, info) {
            var title = this._ganttTaskName(edge.pred) + " \u2192 " + this._ganttTaskName(edge.succ);
            if (info.lag) {
                title += "\nLag: " + info.lag + " d";
            }
            if (info.violated) {
                title += "\nWarning: starts before the predecessor's end plus lag.";
            }
            return title;
        },

        _ganttOpenDependencyPopover: function (edge, info, ev) {
            this._ganttClosePopover();
            var successor = _.findWhere(this.ganttData.tasks, {id: edge.succ}) || {};
            var canWrite = !!successor.can_write;
            var $container = this.$(".o_gantt_container");
            var containerRect = $container[0].getBoundingClientRect();
            var html = '<div class="o_gantt_dep_popover card shadow">' +
                '<div class="card-body p-2">' +
                '<div class="small fw-bold mb-2">' + _.escape(this._ganttTaskName(edge.pred)) +
                ' \u2192 ' + _.escape(this._ganttTaskName(edge.succ)) + '</div>';
            if (info.violated) {
                html += '<div class="small text-danger mb-2"><i class="fa fa-exclamation-triangle me-1"></i>' +
                    'Starts before the predecessor ends plus the lag.</div>';
            }
            html += '<label class="small mb-1">Lag, days</label>' +
                '<input type="number" min="0" step="1" class="form-control form-control-sm o_gantt_dep_lag_input" value="' +
                (info.lag || 0) + '"' + (canWrite ? "" : " disabled") + '/>' +
                '<div class="d-flex gap-1 mt-2">';
            if (canWrite) {
                html += '<button class="btn btn-sm btn-primary o_gantt_dep_save">Save</button>' +
                    '<button class="btn btn-sm btn-outline-danger o_gantt_dep_remove">Remove</button>';
            }
            html += '<button class="btn btn-sm btn-secondary o_gantt_dep_close">Close</button></div></div></div>';
            var $popover = $(html).css({
                left: Math.max(0, ev.clientX - containerRect.left + 8) + "px",
                top: Math.max(0, ev.clientY - containerRect.top + 8) + "px",
            });
            this._ganttPopoverEdge = edge;
            $container.append($popover);
        },

        _ganttClosePopover: function () {
            this.$(".o_gantt_dep_popover").remove();
            this._ganttPopoverEdge = null;
        },

        _onGanttDependencySaveLag: function () {
            var self = this;
            var edge = this._ganttPopoverEdge;
            if (!edge) { return; }
            var lagDays = parseInt(this.$(".o_gantt_dep_lag_input").val(), 10);
            if (isNaN(lagDays) || lagDays < 0) {
                this.displayNotification({
                    title: "Dependencies",
                    message: "The lag must be a whole number of days, zero or more.",
                    type: "warning",
                });
                return;
            }
            this._rpc({
                model: "project.task",
                method: "gantt_set_dependency_lag",
                args: [[edge.succ], edge.pred, lagDays],
            }).then(function () {
                self._ganttClosePopover();
                self._loadGanttData(true);
            });
        },

        _onGanttDependencyRemove: function () {
            var self = this;
            var edge = this._ganttPopoverEdge;
            if (!edge) { return; }
            this._rpc({
                model: "project.task",
                method: "gantt_remove_dependency",
                args: [[edge.succ], edge.pred],
            }).then(function () {
                self._ganttClosePopover();
                self._loadGanttData(true);
            });
        },

        _destroyGantt: function () {
            this._ganttClosePopover();
            if (this.ganttTimeline) {
                this.ganttTimeline.destroy();
                this.ganttTimeline = null;
            }
            this.$(".o_gantt_arrows_svg").remove();
            this._ganttEdges = [];
        },

        /**
         * Cap the left sidebar at 25% of the wrapper plus the shown column width,
         * never above 60%. The width is locked at the widest observed label so
         * scrolling into shorter rows does not shrink it; a change of wrapper
         * width or of shown columns drops the lock and the forced label widths,
         * so the natural width is measured again instead of the locked one.
         */
        _ganttCapSidebarWidth: function () {
            var container = this.$(".o_gantt_timeline_wrapper")[0];
            if (!container) { return; }
            var containerW = container.offsetWidth;
            var columnsW = this._ganttShownColumnsWidth(containerW);
            container.classList.toggle("o_gantt_cols_off", !columnsW);
            var maxW = Math.min(Math.floor(containerW * 0.25) + columnsW, Math.floor(containerW * 0.6));
            var left = container.querySelector(".vis-panel.vis-left");
            if (!left) { return; }
            var inners = left.querySelectorAll(".vis-inner");
            var sidebarKey = containerW + ":" + columnsW;
            if (sidebarKey !== this._ganttSidebarKey) {
                this._ganttSidebarKey = sidebarKey;
                this._ganttSidebarW = 0;
                left.style.width = "";
                for (var k = 0; k < inners.length; k++) {
                    inners[k].style.width = "";
                    inners[k].style.maxWidth = "";
                }
            }

            var naturalW = left.offsetWidth;

            var desired = Math.min(naturalW, maxW);
            if (!this._ganttSidebarW || desired > this._ganttSidebarW) {
                this._ganttSidebarW = desired;
            }

            var lockW = Math.min(this._ganttSidebarW, maxW);
            left.style.width = lockW + "px";
            var panels = container.querySelectorAll(
                ".vis-panel.vis-center, .vis-panel.vis-top, .vis-panel.vis-bottom"
            );
            for (var p = 0; p < panels.length; p++) {
                panels[p].style.left = lockW + "px";
            }

            var leftX = left.getBoundingClientRect().left;
            for (var i = 0; i < inners.length; i++) {
                var innerX = inners[i].getBoundingClientRect().left - leftX;
                var innerW = Math.max(0, lockW - innerX - 4) + "px";
                inners[i].style.width = innerW;
                inners[i].style.maxWidth = innerW;
                inners[i].style.overflow = "hidden";
                inners[i].style.whiteSpace = "nowrap";
            }
        },

        /**
         * Mark the last nested-group row before a non-nested row with a CSS class
         * so we can draw a visible bottom border to close the group visually.
         */
        _ganttMarkGroupBorders: function () {
            var container = this.$(".o_gantt_timeline_wrapper")[0];
            if (!container) { return; }
            var fg = container.querySelector(".vis-foreground");
            if (!fg) { return; }
            var groups = fg.querySelectorAll(".vis-group");
            for (var i = 0; i < groups.length; i++) {
                groups[i].classList.remove("gantt-last-nested");
            }
            for (var i = 0; i < groups.length; i++) {
                if (groups[i].classList.contains("vis-nested-group")) {
                    var next = groups[i + 1];
                    if (!next || !next.classList.contains("vis-nested-group")) {
                        groups[i].classList.add("gantt-last-nested");
                    }
                }
            }
            // Mirror on sidebar labels
            var ls = container.querySelector(".vis-labelset");
            if (!ls) { return; }
            var labels = ls.querySelectorAll(".vis-label");
            for (var i = 0; i < labels.length; i++) {
                labels[i].classList.remove("gantt-last-nested");
            }
            for (var i = 0; i < labels.length; i++) {
                if (labels[i].classList.contains("vis-nested-group")) {
                    var next = labels[i + 1];
                    if (!next || !next.classList.contains("vis-nested-group")) {
                        labels[i].classList.add("gantt-last-nested");
                    }
                }
            }
        },

        _ganttCenterItems: function () {
            var container = this.$(".o_gantt_timeline_wrapper")[0];
            if (!container) { return; }
            var groups = container.querySelectorAll(".vis-foreground .vis-group");
            for (var g = 0; g < groups.length; g++) {
                var groupEl = groups[g];
                var groupH = groupEl.offsetHeight;
                var items = groupEl.querySelectorAll(".vis-item");
                for (var i = 0; i < items.length; i++) {
                    var itemEl = items[i];
                    var itemH = itemEl.offsetHeight;
                    if (itemEl.classList.contains("gantt-baseline")) {
                        itemEl.style.top = Math.max(0, groupH - itemH) + "px";
                        continue;
                    }
                    if (groupH > itemH) {
                        // For milestones, center based on the visible diamond (25px diagonal)
                        // not the DOM wrapper which vis-timeline sizes differently for points
                        var isMilestone = itemEl.classList.contains("gantt-milestone");
                        var visualH = isMilestone ? 25 : itemH;
                        var offset = Math.round((groupH - visualH) / 2);
                        itemEl.style.top = offset + "px";
                    }
                }
            }
        },
    });

    core.action_registry.add("project_team_dashboard", TeamDashboard);
    TeamDashboard.shiftGanttWindow = shiftGanttWindow;
    TeamDashboard.ganttPanBounds = ganttPanBounds;
    TeamDashboard.groupTasksByStage = groupTasksByStage;
    TeamDashboard.buildTaskTree = buildTaskTree;
    TeamDashboard.isAncestorPair = isAncestorPair;
    TeamDashboard.findBarOverlaps = findBarOverlaps;
    TeamDashboard.ganttBarRules = ganttBarRules;
    TeamDashboard.subtaskOfMarker = subtaskOfMarker;
    TeamDashboard.outsideParentOf = outsideParentOf;
    TeamDashboard.assignStageColors = assignStageColors;
    TeamDashboard.elbowPath = elbowPath;
    TeamDashboard.buildStageHistoryRows = buildStageHistoryRows;
    TeamDashboard.stageHistorySpan = stageHistorySpan;
    TeamDashboard.findGanttTasks = findGanttTasks;
    TeamDashboard.utcDateTimeToDate = utcDateTimeToDate;
    TeamDashboard.utcDateTimeToLocalDay = utcDateTimeToLocalDay;
    TeamDashboard.formatUtcDateTime = formatUtcDateTime;
    TeamDashboard.shiftByWholeDays = shiftByWholeDays;
    TeamDashboard.keepOpenTaskOutOfGroupToggle = keepOpenTaskOutOfGroupToggle;

    return TeamDashboard;
});
