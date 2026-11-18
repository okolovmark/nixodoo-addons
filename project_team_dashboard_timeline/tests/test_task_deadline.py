from datetime import date, datetime

from odoo.tests.common import tagged

from odoo.addons.project_team_dashboard.tests.common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestTaskDeadline(TeamDashboardCommon):
    """A stage change writes the closing date, which project_timeline's planned end depends on."""

    def _create_task(self, project):
        return self.env["project.task"].create(
            {
                "name": "Planned",
                "project_id": project.id,
                "stage_id": self.stage_in_progress.id,
                "planned_date_end": datetime(2026, 3, 2, 12),
            }
        )

    def test_a_stage_change_keeps_a_deadline_set_by_hand(self):
        task = self._create_task(self.project)
        task.date_deadline = date(2026, 4, 1)

        for stage in (self.stage_other, self.stage_done):
            task.stage_id = stage

            self.assertEqual(task.date_deadline, date(2026, 4, 1), stage.name)

    def test_a_stage_change_aligns_no_deadline_in_a_project_ticked_ai_driven(self):
        task = self._create_task(self.project_plain)
        # saved before the project is ticked, as in two requests
        self.env.flush_all()
        self.project_plain.is_ai_driven = True

        task.stage_id = self.stage_done

        self.assertFalse(task.date_deadline)
