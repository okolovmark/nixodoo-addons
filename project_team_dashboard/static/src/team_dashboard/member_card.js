import { Component, markup, proxy, t, useProps } from "@odoo/owl";
import { deserializeDate, formatDate } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/core/utils/numbers";

export function formatHours(hours) {
    return formatFloat(hours, { digits: [false, 1] });
}

export function formatDay(serverDate) {
    return formatDate(deserializeDate(serverDate));
}

export class TeamDashboardTaskQueue extends Component {
    static template = "project_team_dashboard.TeamDashboardTaskQueue";

    props = useProps({
        title: t.string(),
        tasks: t.array(),
        isHighlighted: t.boolean().optional(false),
        showProject: t.boolean(),
        onOpenTask: t.function(),
    });

    setup() {
        this.state = proxy({ isOpen: false });
        this.formatHours = formatHours;
        this.formatDay = formatDay;
    }

    toggle() {
        this.state.isOpen = !this.state.isOpen;
    }
}

export class TeamDashboardMemberCard extends Component {
    static template = "project_team_dashboard.TeamDashboardMemberCard";
    static components = { TeamDashboardTaskQueue };

    props = useProps({
        member: t.object(),
        mode: t.selection(["project", "manager", "department"]),
        onOpenTask: t.function(),
        onOpenBacklog: t.function(),
        onOpenKanban: t.function(),
        onOpenClosedTasks: t.function(),
    });

    setup() {
        this.formatHours = formatHours;
        this.formatDay = formatDay;
    }

    showsProjects() {
        return this.props.mode !== "project";
    }

    openTasksText(member) {
        const count = member.total_open_tasks;
        return count === 1 ? _t("1 task") : _t("%(count)s tasks", { count });
    }

    remainingHoursText(hours) {
        return _t("%(hours)sh remaining", { hours: formatHours(hours) });
    }

    overEstimateText(hours) {
        return _t("%(hours)sh over estimate", { hours: formatHours(hours) });
    }

    todayHoursText(member) {
        return _t("Today: %(hours)sh", { hours: formatHours(member.hours_logged_today) });
    }

    subtaskOfText(task) {
        return _t("sub-task of %(parent)s", { parent: task.parent_key });
    }

    currentHoursText(task) {
        return _t("%(remaining)sh remaining / %(planned)sh estimated", {
            remaining: formatHours(task.remaining_hours),
            planned: formatHours(task.planned_hours),
        });
    }

    deadlineText(task) {
        const date = formatDay(task.planned_date_end);
        return task.is_overdue ? _t("Deadline: %(date)s (OVERDUE)", { date }) : _t("Deadline: %(date)s", { date });
    }

    estimatedHoursText(member) {
        return _t("%(hours)sh estimated", { hours: formatHours(member.total_planned_hours) });
    }

    etaText(member) {
        return _t("ETA: %(date)s", { date: markup`<strong>${formatDay(member.estimated_completion)}</strong>` });
    }

    /** Share of the allocated hours already spent; above 100 when the task runs over. */
    spentPercent(task) {
        return ((task.planned_hours - task.remaining_hours) / task.planned_hours) * 100;
    }

    progressBarClass(task) {
        const percent = this.spentPercent(task);
        if (percent > 100) {
            return "bg-danger";
        }
        return percent > 80 ? "bg-warning" : "bg-success";
    }

    progressBarWidth(task) {
        return Math.max(0, Math.min(this.spentPercent(task), 100));
    }
}
