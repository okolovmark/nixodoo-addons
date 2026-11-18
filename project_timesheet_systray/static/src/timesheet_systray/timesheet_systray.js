/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const { Component, useState, onWillStart, onWillUnmount, onMounted } = owl;

export class TimesheetSystray extends Component {
    setup() {
        this.rpc = useService("rpc");
        this.actionService = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            running: false,
            timesheet: false,
            tasks: [],
            totalTasks: 0,
            showAll: false,
            message: "",
            elapsed: 0,
            isOpen: false,
            loading: true,
        });

        this._timerInterval = null;
        this._refreshInterval = null;

        onWillStart(async () => {
            await this._fetchData();
        });

        onMounted(() => {
            this._onDocClick = (ev) => {
                if (this.state.isOpen && !ev.target.closest(".o_timesheet_systray")) {
                    this.state.isOpen = false;
                }
            };
            document.addEventListener("click", this._onDocClick, true);
            this._refreshInterval = setInterval(() => {
                if (!this.state.isOpen) {
                    this._fetchData();
                }
            }, 60000);
        });

        onWillUnmount(() => {
            this._clearTimer();
            if (this._refreshInterval) {
                clearInterval(this._refreshInterval);
                this._refreshInterval = null;
            }
            if (this._onDocClick) {
                document.removeEventListener("click", this._onDocClick, true);
            }
        });
    }

    async _fetchData() {
        this.state.loading = true;
        try {
            const result = await this.rpc("/project_timesheet_systray/systray", {});
            this._applyResult(result);
        } catch (e) {
            this.state.running = false;
            this.state.timesheet = false;
            this.state.message = "Error loading data";
        }
        this.state.loading = false;
    }

    _applyResult(result) {
        this.state.running = result.running;
        this.state.timesheet = result.timesheet;
        this.state.tasks = result.tasks || [];
        this.state.totalTasks = result.total_tasks || 0;
        this.state.showAll = false;
        this.state.message = result.message || "";
        this._clearTimer();
        if (result.running && result.timesheet) {
            this.state.elapsed = Math.floor(result.timesheet.elapsed_seconds || 0);
            this._startClientTimer();
        } else {
            this.state.elapsed = 0;
        }
    }

    _startClientTimer() {
        this._clearTimer();
        this._timerInterval = setInterval(() => {
            this.state.elapsed += 1;
        }, 1000);
    }

    _clearTimer() {
        if (this._timerInterval) {
            clearInterval(this._timerInterval);
            this._timerInterval = null;
        }
    }

    get elapsedFormatted() {
        const total = this.state.elapsed;
        const h = Math.floor(total / 3600);
        const m = Math.floor((total % 3600) / 60);
        const s = total % 60;
        const pad = (n) => String(n).padStart(2, "0");
        return `${pad(h)}:${pad(m)}:${pad(s)}`;
    }

    get visibleTasks() {
        if (this.state.showAll) {
            return this.state.tasks;
        }
        return this.state.tasks.slice(0, 5);
    }

    get hasMoreTasks() {
        return this.state.totalTasks > 5 && !this.state.showAll;
    }

    get remainingTaskCount() {
        return this.state.totalTasks - 5;
    }

    get iconClass() {
        if (this.state.running) {
            return "o_ts_icon_running";
        }
        return "o_ts_icon_idle";
    }

    toggleDropdown(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.isOpen = !this.state.isOpen;
        if (this.state.isOpen) {
            this._fetchData();
        }
    }

    async onStopTimer(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!this.state.timesheet) return;
        this.state.loading = true;
        try {
            const result = await this.rpc("/project_timesheet_systray/stop", {
                timesheet_id: this.state.timesheet.id,
            });
            if (result.error) {
                this.notification.add(result.error, { type: "warning" });
            }
            await this._fetchData();
        } catch (e) {
            this.notification.add("Error stopping timer", { type: "danger" });
        }
        this.state.loading = false;
    }

    async onStartTimer(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!this.state.timesheet) return;
        await this._startTimerForTask(this.state.timesheet.task_id);
    }

    async onStartTaskTimer(taskId, ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        await this._startTimerForTask(taskId);
    }

    async _startTimerForTask(taskId) {
        this.state.loading = true;
        try {
            const result = await this.rpc("/project_timesheet_systray/start", {
                task_id: taskId,
            });
            if (result.error) {
                this.notification.add(result.error, { type: "warning" });
                this.state.loading = false;
                return;
            }
            this._applyResult(result);
        } catch (e) {
            this.notification.add("Error starting timer", { type: "danger" });
        }
        this.state.loading = false;
    }

    onOpenTask(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        if (!this.state.timesheet) return;
        this._openTaskForm(this.state.timesheet.task_id);
    }

    onOpenTaskById(taskId, ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        this._openTaskForm(taskId);
    }

    _openTaskForm(taskId) {
        this.state.isOpen = false;
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: taskId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    async onShowMore(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.loading = true;
        try {
            const result = await this.rpc("/project_timesheet_systray/systray", { limit: 0 });
            this.state.tasks = result.tasks || [];
            this.state.totalTasks = result.total_tasks || 0;
            this.state.showAll = true;
        } catch (e) {
            this.notification.add("Error loading tasks", { type: "danger" });
        }
        this.state.loading = false;
    }

}

TimesheetSystray.template = "project_timesheet_systray.TimesheetSystray";

registry.category("systray").add("project_timesheet_systray.TimesheetSystray", {
    Component: TimesheetSystray,
}, { sequence: 5 });
