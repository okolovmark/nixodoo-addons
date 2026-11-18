import { loadJS } from "@web/core/assets";

const LIB_URL = "/project_team_dashboard/static/lib/vis-timeline/vis-timeline-graph2d.js";

/**
 * vis-timeline is loaded on the first Gantt shown, not with the backend assets. Its UMD build,
 * loaded by a script tag, sets `window.vis`: Odoo 20 defines no AMD `define` it could register with.
 * The build injects its stylesheet itself, as `<style>` elements at the end of the page's head.
 */
export const visTimeline = {
    async load() {
        await loadJS(LIB_URL);
        if (!window.vis?.Timeline) {
            throw new Error("vis-timeline was loaded but defines no vis.Timeline");
        }
        return window.vis;
    },
};
