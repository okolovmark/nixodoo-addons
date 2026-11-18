import { Component, proxy, t, useProps } from "@odoo/owl";

/** Lag and removal of one dependency, opened from its arrow. */
export class GanttDependencyPopover extends Component {
    static template = "project_team_dashboard.GanttDependencyPopover";
    props = useProps({
        title: t.string(),
        lag: t.number(),
        isViolated: t.boolean(),
        canWrite: t.boolean(),
        onSave: t.function(),
        onRemove: t.function(),
        close: t.function(),
    });

    setup() {
        this.state = proxy({ lag: String(this.props.lag) });
    }

    save() {
        this.props.onSave(this.state.lag);
    }
}
