import { Component, proxy, signal, t, useListener, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { BadgeTag } from "@web/core/tags_list/badge_tag";

import { SEARCH_FIELDS, findGanttTasks, formatTaskType, searchValues } from "./gantt_model";

/**
 * The Gantt's search box: facets narrow the chart by assignee, task, key, stage or type, and the
 * text typed lists the shown tasks to jump to.
 */
export class GanttSearch extends Component {
    static template = "project_team_dashboard.GanttSearch";
    static components = { BadgeTag };
    props = useProps({
        facets: t.array(),
        valueTasks: t.array(),
        goToTasks: t.array(),
        onAddFacet: t.function(),
        onRemoveFacet: t.function(),
        onGoToTask: t.function(),
    });
    rootRef = signal.ref();
    inputRef = signal.ref();

    setup() {
        this.state = proxy({ query: "", field: null, isOpen: false });
        useListener(window, "pointerdown", (ev) => {
            if (this.state.isOpen && !this.rootRef()?.contains(ev.target)) {
                this.close();
            }
        });
    }

    get placeholder() {
        if (this.state.field) {
            return `${this.state.field.label}…`;
        }
        return this.props.facets.length ? "" : _t("Search…");
    }

    get normalizedQuery() {
        return this.state.query.trim().toLowerCase();
    }

    get fieldChoices() {
        const query = this.normalizedQuery;
        return SEARCH_FIELDS.filter((field) => !query || String(field.label).toLowerCase().includes(query));
    }

    get taskChoices() {
        return findGanttTasks(this.props.goToTasks, this.state.query);
    }

    get valueChoices() {
        const fieldName = this.state.field.field;
        const query = this.normalizedQuery;
        return searchValues(this.props.valueTasks, fieldName)
            .filter((value) => !query || value.toLowerCase().includes(query))
            .map((value) => ({ value, label: fieldName === "task_type" ? formatTaskType(value) : value }));
    }

    get isDropdownShown() {
        if (!this.state.isOpen) {
            return false;
        }
        return Boolean(this.state.field || this.fieldChoices.length || this.taskChoices.length);
    }

    open() {
        this.state.isOpen = true;
    }

    close() {
        this.state.isOpen = false;
    }

    focusInput() {
        this.inputRef()?.focus();
    }

    onInput(ev) {
        this.state.query = ev.target.value;
        this.open();
    }

    onKeydown(ev) {
        if (ev.key === "Backspace" && !ev.target.value) {
            if (this.state.field) {
                this.state.field = null;
            } else if (this.props.facets.length) {
                this.props.onRemoveFacet(this.props.facets.length - 1);
            }
            ev.preventDefault();
        } else if (ev.key === "Enter" && !this.state.field) {
            const firstTask = this.taskChoices[0];
            if (firstTask) {
                ev.preventDefault();
                this.goToTask(firstTask.id);
            }
        } else if (ev.key === "Escape") {
            this.reset();
            this.close();
        }
    }

    reset() {
        this.state.field = null;
        this.state.query = "";
    }

    selectField(field) {
        this.state.field = field;
        this.state.query = "";
        this.open();
        this.focusInput();
    }

    selectValue(value) {
        const field = this.state.field;
        this.reset();
        this.close();
        this.props.onAddFacet({ field: field.field, fieldLabel: String(field.label), value });
    }

    goToTask(taskId) {
        this.reset();
        this.close();
        this.inputRef()?.blur();
        this.props.onGoToTask(taskId);
    }
}
