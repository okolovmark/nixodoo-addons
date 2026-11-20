/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { sprintf } from "@web/core/utils/strings";
import { priorityDot, ticketAge } from "./support_request_utils";

const { Component, useState, onWillStart, onWillUnmount, onMounted } = owl;

export class SupportRequestSystray extends Component {
    setup() {
        this.rpc = useService("rpc");
        this.actionService = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            configured: true,
            activeTab: "bugs",
            bugsCount: 0,
            othersCount: 0,
            tickets: [],
            total: 0,
            isOpen: false,
            loading: true,
            creating: false,
            createName: "",
            createType: "bug",
            showCreateForm: false,
            showAll: false,
            taskTypes: [],
        });

        this._refreshInterval = null;

        onWillStart(async () => {
            await this._fetchConfig();
            await this._fetchData();
        });

        onMounted(() => {
            this._onDocClick = (ev) => {
                if (this.state.isOpen && !ev.target.closest(".o_odoo_requests_systray")) {
                    this.state.isOpen = false;
                    this.state.showCreateForm = false;
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
            if (this._refreshInterval) {
                clearInterval(this._refreshInterval);
                this._refreshInterval = null;
            }
            if (this._onDocClick) {
                document.removeEventListener("click", this._onDocClick, true);
            }
        });
    }

    async _fetchConfig() {
        try {
            const config = await this.rpc("/project_support_request/config", {});
            this.state.taskTypes = config.task_types || [];
        } catch {
            this.state.taskTypes = [];
        }
    }

    async _fetchData() {
        this.state.loading = true;
        try {
            const result = await this.rpc("/project_support_request/systray", {
                tab: this.state.activeTab,
            });
            this.state.configured = result.configured;
            this.state.bugsCount = result.bugs_count || 0;
            this.state.othersCount = result.others_count || 0;
            this.state.tickets = result.tickets || [];
            this.state.total = result.total || 0;
        } catch {
            this.state.configured = false;
            this.state.tickets = [];
        }
        this.state.loading = false;
    }

    get totalBadge() {
        return this.state.bugsCount + this.state.othersCount;
    }

    get hasBadge() {
        return this.totalBadge > 0;
    }

    get visibleTickets() {
        if (this.state.showAll) {
            return this.state.tickets;
        }
        return this.state.tickets.slice(0, 5);
    }

    get hasMoreTickets() {
        return this.state.total > 5 && !this.state.showAll;
    }

    get remainingTicketCount() {
        return this.state.total - 5;
    }

    get ticketAge() {
        return ticketAge;
    }

    get priorityDot() {
        return priorityDot;
    }

    priorityTitle(label) {
        return sprintf(this.env._t("Priority: %s"), label);
    }

    _openRequest(requestId) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: requestId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    toggleDropdown(ev) {
        ev.preventDefault();
        ev.stopPropagation();
        this.state.isOpen = !this.state.isOpen;
        this.state.showCreateForm = false;
        this.state.showAll = false;
        if (this.state.isOpen) {
            this.state.activeTab = "bugs";
            this._fetchData();
        }
    }

    async onSwitchTab(tab, ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        if (this.state.activeTab === tab) return;
        this.state.activeTab = tab;
        this.state.showCreateForm = false;
        this.state.showAll = false;
        await this._fetchData();
    }

    onTicketClick(ticketId, ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        this.state.isOpen = false;
        this._openRequest(ticketId);
    }

    onShowCreateForm(ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        this.state.showCreateForm = true;
        this.state.createName = "";
        if (this.state.activeTab === "bugs") {
            this.state.createType = "bug";
        } else {
            const firstNonBug = this.state.taskTypes.find(t => t.value !== "bug");
            this.state.createType = firstNonBug ? firstNonBug.value : "feature";
        }
    }

    onCancelCreate(ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        this.state.showCreateForm = false;
    }

    onCreateNameInput(ev) {
        this.state.createName = ev.target.value;
    }

    onCreateTypeChange(ev) {
        this.state.createType = ev.target.value;
    }

    async onSubmitCreate(ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        if (!this.state.createName.trim()) {
            this.notification.add(this.env._t("Please enter a title for the support request."), { type: "warning" });
            return;
        }
        this.state.creating = true;
        try {
            const result = await this.rpc("/project_support_request/create", {
                name: this.state.createName.trim(),
                task_type: this.state.createType || "bug",
            });
            if (result.error) {
                this.notification.add(result.error, { type: "warning" });
                this.state.creating = false;
                return;
            }
            this.state.showCreateForm = false;
            this.state.isOpen = false;
            this._openRequest(result.ticket_id);
        } catch {
            this.notification.add(this.env._t("Error creating the support request"), { type: "danger" });
        }
        this.state.creating = false;
    }

    async onShowMore(ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        this.state.loading = true;
        try {
            const result = await this.rpc("/project_support_request/systray", {
                tab: this.state.activeTab,
                limit: 0,
            });
            this.state.tickets = result.tickets || [];
            this.state.total = result.total || 0;
            this.state.showAll = true;
        } catch {
            this.notification.add(this.env._t("Error loading support requests"), { type: "danger" });
        }
        this.state.loading = false;
    }

    async onViewClosedClick(ev) {
        if (ev) {
            ev.preventDefault();
            ev.stopPropagation();
        }
        this.state.isOpen = false;
        try {
            const configResult = await this.rpc("/project_support_request/config", {});
            if (!configResult.project_id) {
                this.notification.add(
                    this.env._t(
                        "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo on the AI-driven project that develops it, or restores the archived one that carries it."
                    ),
                    { type: "warning" }
                );
                return;
            }
            this.actionService.doAction({
                type: "ir.actions.act_window",
                name: this.env._t("Closed Support Requests"),
                res_model: "project.task",
                views: [
                    [false, "list"],
                    [false, "form"],
                ],
                domain: [
                    ["project_id", "=", configResult.project_id],
                    ["stage_id.fold", "=", true],
                    ["uat_user_ids", "in", [configResult.uid]],
                ],
                target: "current",
            });
        } catch {
            this.notification.add(this.env._t("Error opening closed support requests"), { type: "danger" });
        }
    }
}

SupportRequestSystray.template = "project_support_request.SupportRequestSystray";

registry.category("systray").add("project_support_request.SupportRequestSystray", {
    Component: SupportRequestSystray,
}, { sequence: 8 });
