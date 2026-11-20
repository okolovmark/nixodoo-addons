import base64
import io
from datetime import datetime, timedelta
from unittest.mock import patch

import openpyxl

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tools.translate import code_translations

from .common import TeamDashboardCommon

FRENCH = {
    "Project not found": "Projet introuvable",
    "Unassigned": "Non assigné",
    "No projects selected": "Aucun projet sélectionné",
    "No tasks with planned dates": "Aucune tâche planifiée",
    "Gantt Chart": "Diagramme de Gantt",
    "Key": "Clé",
    "Task": "Tâche",
    "Type": "Type de tâche",
    "Assignee": "Responsable",
    "Start": "Début",
    "End": "Fin",
    "Progress": "Avancement",
    "Stage": "Étape",
    "User not found": "Utilisateur introuvable",
    "No employee record found for this user": "Aucun employé pour cet utilisateur",
    "Department not found": "Département introuvable",
    "No employee record found for this user in the selected companies": "Aucun employé dans les sociétés choisies",
    "Department not in the selected companies": "Département hors des sociétés choisies",
}


@tagged("post_install", "-at_install")
class TestServerMessagesTranslated(TeamDashboardCommon):
    """The messages and labels the server sends to the screen are in the user's language."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("fr_FR")
        cls.startClassPatcher(
            patch.dict(code_translations.python_translations, {("project_team_dashboard", "fr_FR"): FRENCH})
        )
        cls.Project = cls.env["project.project"].with_context(lang="fr_FR")
        now = fields.Datetime.now()
        cls.env["project.task"].create(
            {
                "name": "Nobody's task",
                "project_id": cls.project.id,
                "stage_id": cls.stage_backlog.id,
                "user_ids": [Command.set([])],
                "planned_date_start": now,
                "planned_date_end": now + timedelta(days=2),
            }
        )

    def test_dashboard_errors_and_the_unassigned_card(self):
        with self.subTest(case="unknown project"):
            self.assertEqual(self.Project.get_team_dashboard_data(999999)["error"], "Projet introuvable")
        with self.subTest(case="unassigned card"):
            members = self.Project.get_team_dashboard_data(self.project.id)["members"]
            self.assertEqual(next(m for m in members if m["user_id"] is False)["user_name"], "Non assigné")
        with self.subTest(case="unknown manager"):
            self.assertEqual(self.Project.get_team_dashboard_by_manager(999999)["error"], "Utilisateur introuvable")
        with self.subTest(case="manager without employee"):
            no_employee = self.env["res.users"].create({"name": "No Employee", "login": "no_employee_fr"})
            self.assertEqual(
                self.Project.get_team_dashboard_by_manager(no_employee.id)["error"],
                "Aucun employé pour cet utilisateur",
            )
        with self.subTest(case="unknown department"):
            self.assertEqual(self.Project.get_team_dashboard_by_department(999999)["error"], "Département introuvable")

    def test_messages_about_a_team_in_a_company_the_user_may_select(self):
        company_b = self.env["res.company"].create({"name": "Company B"})
        approver = self.env["res.users"].create(
            {
                "name": "Approver in A and B",
                "login": "approver_a_b_fr",
                "group_ids": [
                    Command.set(
                        (
                            self.env.ref("project.group_project_user")
                            | self.env.ref("hr_timesheet.group_hr_timesheet_approver")
                        ).ids
                    )
                ],
                "company_ids": [Command.set((self.env.company | company_b).ids)],
            }
        )
        user_b = self.env["res.users"].create(
            {
                "name": "Manager in B",
                "login": "manager_b_fr",
                "company_id": company_b.id,
                "company_ids": [Command.set(company_b.ids)],
            }
        )
        department_b = self.env["hr.department"].create({"name": "Department in B", "company_id": company_b.id})
        self.env["hr.employee"].create({"name": "Manager in B", "user_id": user_b.id, "company_id": company_b.id})
        Project = self.Project.with_user(approver).with_context(allowed_company_ids=self.env.company.ids)
        self.assertEqual(
            Project.get_team_dashboard_by_manager(user_b.id)["error"], "Aucun employé dans les sociétés choisies"
        )
        self.assertEqual(
            Project.get_team_dashboard_by_department(department_b.id)["error"],
            "Département hors des sociétés choisies",
        )

    def test_export_errors_sheet_and_column_titles(self):
        self.assertEqual(self.Project.export_gantt_excel([])["error"], "Aucun projet sélectionné")
        self.assertEqual(self.Project.export_gantt_excel([self.project_plain.id])["error"], "Aucune tâche planifiée")
        result = self.Project.export_gantt_excel([self.project.id])
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        self.assertEqual(sheet.title, "Diagramme de Gantt")
        self.assertEqual(
            [cell.value for cell in sheet[1]][:8],
            ["Clé", "Tâche", "Type de tâche", "Responsable", "Début", "Fin", "Avancement", "Étape"],
        )

    def test_the_day_headers_name_the_months_in_the_user_language(self):
        project = self.env["project.project"].create({"name": "Month Turn"})
        self.env["project.task"].create(
            {
                "name": "Across the month end",
                "project_id": project.id,
                "planned_date_start": datetime(2026, 1, 30, 12),
                "planned_date_end": datetime(2026, 2, 2, 12),
            }
        )
        result = self.Project.export_gantt_excel([project.id])
        sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
        self.assertEqual(
            [cell.value for cell in sheet[1]][8:],
            ["29 janv.", "30 janv.", "31 janv.", "01 févr.", "02 févr.", "03 févr."],
        )

    def test_a_sheet_title_too_long_or_with_characters_excel_refuses_is_cut_to_one_it_takes(self):
        """Odoo's patched `add_worksheet` (odoo/_monkeypatches/xlsxwriter.py) makes the translated title fit,
        but for an apostrophe at either end."""
        for translation, sheet_title in (
            ("Diagramme de Gantt des tâches planifiées du projet", "Diagramme de Gantt des tâches p"),
            ("Gantt: [tâches] / jalons *?\\", "Gantt tâches  jalons "),
            ("'Gantt' des jalons'", "Gantt' des jalons"),
            ("Gantt des tâches de l'équipe d'Ana", "Gantt des tâches de l'équipe d"),
            ("'*'", "Gantt Chart"),
        ):
            with (
                self.subTest(translation=translation),
                patch.dict(
                    code_translations.python_translations,
                    {("project_team_dashboard", "fr_FR"): {**FRENCH, "Gantt Chart": translation}},
                ),
            ):
                result = self.Project.export_gantt_excel([self.project.id])
                sheet = openpyxl.load_workbook(io.BytesIO(base64.b64decode(result["data"]))).active
                self.assertEqual(sheet.title, sheet_title)
