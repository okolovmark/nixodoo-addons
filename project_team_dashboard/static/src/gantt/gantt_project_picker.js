import { Component, t, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { SelectMenu } from "@web/core/select_menu/select_menu";
import { BadgeTag } from "@web/core/tags_list/badge_tag";

export function projectLabel(project) {
    return project.key ? `${project.key} - ${project.name}` : project.name;
}

/** The projects the Gantt shows, as removable tags, and a picker adding one more. */
export class GanttProjectPicker extends Component {
    static template = "project_team_dashboard.GanttProjectPicker";
    static components = { BadgeTag, SelectMenu };
    props = useProps({
        projects: t.array(),
        projectIds: t.array(),
        onAdd: t.function(),
        onRemove: t.function(),
    });

    get selectedProjects() {
        const projectsById = new Map(this.props.projects.map((project) => [project.id, project]));
        return this.props.projectIds.map((projectId) => {
            const project = projectsById.get(projectId);
            return { id: projectId, label: project ? projectLabel(project) : _t("Project #%s", projectId) };
        });
    }

    get choices() {
        return this.props.projects
            .filter((project) => !this.props.projectIds.includes(project.id))
            .map((project) => ({ value: project.id, label: projectLabel(project) }));
    }

    removeTooltip(project) {
        return _t("Remove %s", project.label);
    }
}
