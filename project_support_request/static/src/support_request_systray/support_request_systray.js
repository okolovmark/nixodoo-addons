import { Component, onMounted, onWillStart, onWillUnmount, proxy } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { rpc } from "@web/core/network/rpc";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const { DateTime } = luxon;

const PAGE_SIZE = 5;
const REFRESH_INTERVAL = 60000;
const PRIORITY_CLASSES = {
    0: "text-muted opacity-50",
    1: "text-muted",
    2: "text-info",
    3: "text-warning",
    4: "text-danger",
};
// Low, the level a task is created with.
const DEFAULT_PRIORITY_LEVEL = "1";

/** The Support item of the header: the user's open requests and a form to file a new one. */
export class SupportRequestSystray extends Component {
    static components = { Dropdown };
    static template = "project_support_request.SupportRequestSystray";

    setup() {
        this.action = useService("action");
        this.notification = useService("notification");
        this.dropdown = useDropdownState();
        this.state = proxy({
            configured: false,
            activeTab: "bugs",
            bugsCount: 0,
            othersCount: 0,
            tickets: [],
            total: 0,
            loading: false,
            creating: false,
            createName: "",
            createType: "bug",
            showCreateForm: false,
            showAll: false,
            taskTypes: [],
            priorityLabels: {},
        });

        // Not awaited: the header does not wait for the item.
        onWillStart(() => {
            this.fetchConfig();
            this.fetchData();
        });
        onMounted(() => {
            this.refreshInterval = setInterval(() => {
                if (!this.dropdown.isOpen) {
                    this.fetchData();
                }
            }, REFRESH_INTERVAL);
        });
        onWillUnmount(() => clearInterval(this.refreshInterval));
    }

    async fetchConfig() {
        try {
            const config = await rpc("/project_support_request/config", {});
            this.state.taskTypes = config.task_types || [];
            this.state.priorityLabels = config.priority_levels || {};
        } catch {
            this.state.taskTypes = [];
        }
    }

    async fetchData() {
        this.state.loading = true;
        try {
            const result = await rpc("/project_support_request/systray", { tab: this.state.activeTab });
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

    get openCount() {
        return this.state.bugsCount + this.state.othersCount;
    }

    get visibleTickets() {
        return this.state.showAll ? this.state.tickets : this.state.tickets.slice(0, PAGE_SIZE);
    }

    get hasMoreTickets() {
        return this.state.total > PAGE_SIZE && !this.state.showAll;
    }

    get remainingTicketCount() {
        return this.state.total - PAGE_SIZE;
    }

    ticketAge(createDate) {
        if (!createDate) {
            return "";
        }
        // A creation date ahead of the browser's clock reads as just now.
        const minutes = Math.max(
            0,
            Math.floor(DateTime.now().diff(deserializeDateTime(createDate), "minutes").minutes)
        );
        if (minutes < 60) {
            return _t("%sm ago", minutes);
        }
        const hours = Math.floor(minutes / 60);
        if (hours < 24) {
            return _t("%sh ago", hours);
        }
        return _t("%sd ago", Math.floor(hours / 24));
    }

    priorityLevel(level) {
        return Object.hasOwn(PRIORITY_CLASSES, level) ? level : DEFAULT_PRIORITY_LEVEL;
    }

    priorityClass(level) {
        return PRIORITY_CLASSES[this.priorityLevel(level)];
    }

    priorityTitle(level) {
        const label = this.state.priorityLabels[this.priorityLevel(level)];
        return label ? _t("Priority: %s", label) : undefined;
    }

    onBeforeOpen() {
        this.state.activeTab = "bugs";
        this.state.showCreateForm = false;
        this.state.showAll = false;
        return this.fetchData();
    }

    async onSwitchTab(tab) {
        if (this.state.activeTab === tab) {
            return;
        }
        this.state.activeTab = tab;
        this.state.showCreateForm = false;
        this.state.showAll = false;
        await this.fetchData();
    }

    openTicket(ticketId) {
        this.dropdown.close();
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "project.task",
            res_id: ticketId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    onShowCreateForm() {
        this.state.showCreateForm = true;
        this.state.createName = "";
        if (this.state.activeTab === "bugs") {
            this.state.createType = "bug";
        } else {
            const firstNonBug = this.state.taskTypes.find((taskType) => taskType.value !== "bug");
            this.state.createType = firstNonBug ? firstNonBug.value : "feature";
        }
    }

    onCancelCreate() {
        this.state.showCreateForm = false;
    }

    onCreateNameInput(ev) {
        this.state.createName = ev.target.value;
    }

    onCreateTypeChange(ev) {
        this.state.createType = ev.target.value;
    }

    async onSubmitCreate() {
        if (!this.state.createName.trim()) {
            this.notification.add(_t("Please enter a ticket title."), { type: "warning" });
            return;
        }
        this.state.creating = true;
        try {
            const result = await rpc("/project_support_request/create", {
                name: this.state.createName.trim(),
                task_type: this.state.createType || "bug",
            });
            if (result.error) {
                this.notification.add(result.error, { type: "warning" });
                return;
            }
            this.state.showCreateForm = false;
            this.openTicket(result.ticket_id);
        } catch {
            this.notification.add(_t("Error creating ticket"), { type: "danger" });
        } finally {
            this.state.creating = false;
        }
    }

    async onShowMore() {
        this.state.loading = true;
        try {
            const result = await rpc("/project_support_request/systray", {
                tab: this.state.activeTab,
                limit: 0,
            });
            this.state.tickets = result.tickets || [];
            this.state.total = result.total || 0;
            this.state.showAll = true;
        } catch {
            this.notification.add(_t("Error loading tickets"), { type: "danger" });
        }
        this.state.loading = false;
    }

    async onViewClosed() {
        this.dropdown.close();
        try {
            const config = await rpc("/project_support_request/config", {});
            if (!config.project_id) {
                this.notification.add(
                    _t(
                        "No active project develops this Odoo: a Settings administrator ticks Develops This Odoo on the AI-driven project that develops it, or restores the archived one that carries it."
                    ),
                    { type: "warning" }
                );
                return;
            }
            await this.action.doAction({
                type: "ir.actions.act_window",
                name: _t("Closed Tickets"),
                res_model: "project.task",
                views: [
                    [false, "list"],
                    [false, "form"],
                ],
                domain: [
                    ["project_id", "=", config.project_id],
                    ["stage_id.fold", "=", true],
                    ["uat_user_ids", "in", [config.uid]],
                ],
                target: "current",
            });
        } catch {
            this.notification.add(_t("Error opening closed tickets"), { type: "danger" });
        }
    }
}

registry
    .category("systray")
    .add("project_support_request.SupportRequestSystray", { Component: SupportRequestSystray }, { sequence: 8 });
