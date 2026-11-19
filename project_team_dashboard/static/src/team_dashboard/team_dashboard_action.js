import { Component, onWillStart, proxy, toRaw, useProps } from "@odoo/owl";
import { AutoComplete } from "@web/core/autocomplete/autocomplete";
import { DateTimeInput } from "@web/core/datetime/datetime_input";
import { serializeDate, today } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { KeepLast } from "@web/core/utils/concurrency";
import { useService } from "@web/core/utils/hooks";
import { pick } from "@web/core/utils/objects";
import { useSetupAction } from "@web/search/action_hook";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { GanttView, makeGanttSettings } from "@project_team_dashboard/gantt/gantt_view";
import { formatDay, TeamDashboardMemberCard } from "./member_card";

const BACKLOG_ACTION = "project_team_dashboard.action_project_task_backlog";
// what a breadcrumb return brings back, besides the Gantt's settings
const KEPT_STATE_KEYS = [
    "mode",
    "viewMode",
    "projectId",
    "managerId",
    "maxDepth",
    "departmentId",
    "period",
    "customStart",
    "customEnd",
];
const MODE_TEAM_KEYS = { manager: "managerId", department: "departmentId" };

/**
 * The days a period covers, as the server dates the dashboard methods take. Weeks start on
 * Monday, as the server's own default period does.
 *
 * @param {string} period
 * @param {luxon.DateTime} day the day the period is relative to
 * @param {{start: luxon.DateTime|false, end: luxon.DateTime|false}} customRange
 */
export function getPeriodDates(period, day, customRange) {
    let start = day.startOf("week");
    let end = start.plus({ days: 6 });
    if (period === "last_week") {
        start = start.minus({ weeks: 1 });
        end = start.plus({ days: 6 });
    } else if (period === "this_month") {
        start = day.startOf("month");
        end = day.endOf("month").startOf("day");
    } else if (period === "last_month") {
        start = day.minus({ months: 1 }).startOf("month");
        end = start.endOf("month").startOf("day");
    } else if (period === "custom" && customRange.start && customRange.end) {
        start = customRange.start;
        end = customRange.end;
    }
    return { date_start: serializeDate(start), date_end: serializeDate(end) };
}

function projectLabel(project) {
    return project.key ? `${project.key} - ${project.name}` : project.name;
}

function departmentLabel(department) {
    return department.complete_name || department.name;
}

export class TeamDashboardAction extends Component {
    static template = "project_team_dashboard.TeamDashboardAction";
    static components = { AutoComplete, DateTimeInput, GanttView, TeamDashboardMemberCard };

    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.keepLast = new KeepLast();
        this.formatDay = formatDay;
        this.projects = [];
        this.managerUsers = [];
        this.departments = [];
        // Odoo creates the action anew on a breadcrumb return, with the state it exported
        const keptState = this.props.state;
        this.state = proxy({
            mode: "project",
            viewMode: "cards",
            data: null,
            projectId: this.props.action.context?.default_project_id || false,
            managerId: user.userId,
            maxDepth: 0,
            departmentId: false,
            period: "this_week",
            customStart: false,
            customEnd: false,
            ganttReloadCount: 0,
            ...keptState?.dashboard,
        });
        // the Gantt is mounted only while shown: its settings live here, to outlive it
        this.ganttSettings = proxy(makeGanttSettings(keptState?.gantt));
        useSetupAction({ getLocalState: () => this.exportState() });
        this.projectSources = [
            {
                options: (term) =>
                    this.pickerOptions(this.projects, projectLabel, term, (project) =>
                        this.selectProject(project.id)
                    ),
            },
        ];
        this.managerSources = [
            {
                options: (term) =>
                    this.pickerOptions(
                        this.managerUsers,
                        (manager) => manager.name,
                        term,
                        (manager) => this.selectTeam("managerId", manager.id)
                    ),
            },
        ];
        this.departmentSources = [
            {
                options: (term) =>
                    this.pickerOptions(this.departments, departmentLabel, term, (department) =>
                        this.selectTeam("departmentId", department.id)
                    ),
            },
        ];
        onWillStart(() => this.loadInitialState());
    }

    async loadInitialState() {
        const [projects, managerUsers, departments, [currentUser]] = await Promise.all([
            this.orm.searchRead("project.project", [["is_template", "=", false]], ["name", "key"], {
                order: "name, id",
            }),
            this.orm.searchRead("res.users", [["share", "=", false]], ["name"], {
                order: "name, id",
            }),
            this.orm.searchRead("hr.department", [], ["name", "complete_name"]),
            // hr.employee is HR-only on this version: the user's department is read off the user.
            this.orm.read("res.users", [user.userId], ["department_id", "dashboard_default_project_id"]),
        ]);
        this.projects = projects;
        this.managerUsers = managerUsers;
        // complete_name is not stored, so the server cannot order on it.
        this.departments = departments.sort((a, b) =>
            departmentLabel(a).localeCompare(departmentLabel(b))
        );
        if (!this.props.state) {
            this.state.departmentId = currentUser.department_id?.[0] || false;
            if (!this.state.projectId) {
                const defaultProject = currentUser.dashboard_default_project_id;
                this.state.projectId = defaultProject ? defaultProject[0] : projects[0]?.id || false;
            }
        }
        this.acceptedTeams = pick(this.state, ...Object.values(MODE_TEAM_KEYS));
        await this.loadDashboard();
    }

    exportState() {
        const ganttSettings = toRaw(this.ganttSettings);
        return {
            // a team still waiting for the server's answer is left out for the one on screen
            dashboard: { ...pick(toRaw(this.state), ...KEPT_STATE_KEYS), ...this.acceptedTeams },
            gantt: {
                ...ganttSettings,
                projectIds: [...ganttSettings.projectIds],
                facets: [...ganttSettings.facets],
            },
        };
    }

    /**
     * Loads the cards of the current mode. Only the last request lands, so a slow answer for a
     * mode or team the user already left never overwrites the one on screen; and when the one that
     * lands fails, the managers and departments go back to the last ones the server accepted, so a
     * refused team never stays picked.
     */
    async loadDashboard() {
        const request = this.dashboardRequest();
        const teamKey = MODE_TEAM_KEYS[this.state.mode];
        const teamId = teamKey && this.state[teamKey];
        const pending = request
            ? this.orm.call("project.project", request.method, [], request.kwargs)
            : Promise.resolve({ members: [] });
        try {
            this.state.data = await this.keepLast.add(pending);
        } catch (error) {
            Object.assign(this.state, this.acceptedTeams);
            throw error;
        }
        if (teamKey) {
            this.acceptedTeams[teamKey] = teamId;
        }
    }

    dashboardRequest() {
        const { mode, projectId, managerId, departmentId } = this.state;
        const dates = getPeriodDates(this.state.period, today(), {
            start: this.state.customStart,
            end: this.state.customEnd,
        });
        if (mode === "manager") {
            return (
                managerId && {
                    method: "get_team_dashboard_by_manager",
                    kwargs: { user_id: managerId, ...dates, max_depth: this.state.maxDepth },
                }
            );
        }
        if (mode === "department") {
            return (
                departmentId && {
                    method: "get_team_dashboard_by_department",
                    kwargs: { department_id: departmentId, ...dates },
                }
            );
        }
        return (
            projectId && {
                method: "get_team_dashboard_data",
                kwargs: { project_id: projectId, ...dates },
            }
        );
    }

    pickerOptions(records, getLabel, term, onSelect) {
        const needle = term.toLowerCase();
        return records
            .filter((record) => getLabel(record).toLowerCase().includes(needle))
            .map((record) => ({ label: getLabel(record), onSelect: () => onSelect(record) }));
    }

    selectedProjectLabel() {
        const project = this.projects.find((project) => project.id === this.state.projectId);
        return project ? projectLabel(project) : "";
    }

    selectedManagerName() {
        return this.managerUsers.find((manager) => manager.id === this.state.managerId)?.name || "";
    }

    selectedDepartmentLabel() {
        const department = this.departments.find((dep) => dep.id === this.state.departmentId);
        return department ? departmentLabel(department) : "";
    }

    selectMode(mode) {
        if (mode === this.state.mode) {
            return;
        }
        this.state.mode = mode;
        this.state.viewMode = "cards";
        this.state.data = null;
        return this.loadDashboard();
    }

    selectProject(projectId) {
        this.state.projectId = projectId;
        return this.loadDashboard();
    }

    /**
     * A manager or a department the server refuses (another team's) gives way to the last one it
     * accepted (`loadDashboard`), and the error goes on to the error dialog with the server's message.
     *
     * @param {"managerId"|"departmentId"} key
     * @param {number} teamId
     */
    selectTeam(key, teamId) {
        this.state[key] = teamId;
        return this.loadDashboard();
    }

    selectDepth(maxDepth) {
        this.state.maxDepth = Number(maxDepth);
        return this.loadDashboard();
    }

    selectPeriod(period) {
        this.state.period = period;
        if (period !== "custom") {
            return this.loadDashboard();
        }
    }

    setCustomDate(key, date) {
        this.state[key] = date || false;
        if (this.state.customStart && this.state.customEnd) {
            return this.loadDashboard();
        }
    }

    selectView(viewMode) {
        this.state.viewMode = viewMode;
    }

    refresh() {
        if (this.state.viewMode === "gantt") {
            this.state.ganttReloadCount++;
        }
        return this.loadDashboard();
    }

    openTask(taskId) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: taskId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openProject() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "project.project",
            res_id: this.state.projectId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openBacklog(userId) {
        const filterContext = userId
            ? { search_default_user_ids: userId }
            : { search_default_unassigned: true };
        return this.action.doAction(BACKLOG_ACTION, {
            additionalContext: {
                search_default_project_id: this.state.projectId,
                default_project_id: this.state.projectId,
                ...filterContext,
            },
        });
    }

    /** The project's tasks, as Odoo's own task lists show them: without the task templates. */
    projectTasksDomain() {
        return [
            ["project_id", "=", this.state.projectId],
            ["has_template_ancestor", "=", false],
        ];
    }

    openKanban(userId) {
        const domain = this.projectTasksDomain();
        if (userId) {
            domain.push(["user_ids", "in", [userId]]);
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Kanban"),
            res_model: "project.task",
            views: [
                [false, "kanban"],
                [false, "form"],
            ],
            domain,
            context: { default_project_id: this.state.projectId },
        });
    }

    openMyTasks() {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("My Tasks"),
            res_model: "project.task",
            views: [
                [false, "kanban"],
                [false, "list"],
                [false, "form"],
            ],
            domain: this.projectTasksDomain(),
            context: { search_default_my_tasks: 1, default_project_id: this.state.projectId },
        });
    }

    openClosedTasks(userId) {
        const domain = [
            ["user_ids", "in", [userId]],
            ["is_closed", "=", true],
            ["has_template_ancestor", "=", false],
        ];
        if (this.state.mode === "project" && this.state.projectId) {
            domain.push(["project_id", "=", this.state.projectId]);
        } else {
            // as Odoo's own task lists across projects: no personal to-dos, no project templates
            domain.push(["project_id", "!=", false], ["has_project_template", "=", false]);
        }
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Closed Tasks"),
            res_model: "project.task",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain,
            target: "current",
        });
    }
}

registry.category("actions").add("project_team_dashboard", TeamDashboardAction);
