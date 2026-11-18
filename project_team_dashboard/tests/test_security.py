from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import TeamDashboardCommon


@tagged("post_install", "-at_install")
class TestDashboardAccess(TeamDashboardCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.other_company = cls.env["res.company"].create({"name": "Other Company"})
        cls.manager = cls.env["res.users"].create(
            dict(
                cls._user_vals("Baseline Manager", ["base.group_user", "project.group_project_manager"]),
                company_id=cls.company.id,
                company_ids=[Command.set(cls.company.ids)],
            )
        )
        cls.project_user = cls.env["res.users"].create(
            dict(
                cls._user_vals("Baseline Reader", ["base.group_user", "project.group_project_user"]),
                company_id=cls.company.id,
                company_ids=[Command.set(cls.company.ids)],
            )
        )
        cls.project_own, cls.project_shared, cls.project_other = cls.env["project.project"].create(
            [
                {"name": "Own Company Project", "company_id": cls.company.id},
                {"name": "Company-less Project", "company_id": False},
                {"name": "Other Company Project", "company_id": cls.other_company.id},
            ]
        )
        cls.baseline_own, cls.baseline_shared, cls.baseline_other = (
            cls._create_baseline(project) for project in (cls.project_own, cls.project_shared, cls.project_other)
        )
        cls.lag_own = cls._create_lag(cls.project_own)
        cls.lag_other = cls._create_lag(cls.project_other)

    @classmethod
    def _create_baseline(cls, project):
        task = cls.env["project.task"].create({"name": "Baselined %s" % project.name, "project_id": project.id})
        return cls.env["project.baseline"].create(
            {"project_id": project.id, "line_ids": [Command.create({"task_id": task.id})]}
        )

    @classmethod
    def _create_lag(cls, project):
        predecessor, successor = cls.env["project.task"].create(
            [{"name": "Predecessor", "project_id": project.id}, {"name": "Successor", "project_id": project.id}]
        )
        successor.depend_on_ids = predecessor
        return cls.env["project.task.dependency.lag"].create(
            {"task_id": successor.id, "depends_on_id": predecessor.id, "lag_days": 2}
        )

    def test_the_access_rows_of_the_module(self):
        project_manager = self.env.ref("project.group_project_manager")
        expected_rows = {
            "access_project_baseline_manager": ("project.baseline", project_manager, "crud", False),
            "project_baseline_rule_company": (
                "project.baseline",
                self.env["res.groups"],
                "crud",
                "[('project_id.company_id', 'in', company_ids + [False])]",
            ),
            "access_project_baseline_line_manager": (
                "project.baseline.line",
                project_manager,
                "crud",
                "[('baseline_id', 'access', 'read')]",
            ),
            "access_project_task_dependency_lag_manager": (
                "project.task.dependency.lag",
                project_manager,
                "r",
                "[('task_id', 'access', 'read')]",
            ),
        }
        for xmlid, expected_row in expected_rows.items():
            access = self.env.ref("project_team_dashboard.%s" % xmlid)
            self.assertEqual((access.model_id.model, access.group_id, access.operation, access.domain), expected_row)

    def test_a_manager_reads_the_baselines_of_own_and_company_less_projects(self):
        baselines = self.env["project.baseline"].with_user(self.manager)

        found = baselines.search([("id", "in", (self.baseline_own | self.baseline_shared | self.baseline_other).ids)])

        self.assertEqual(found, self.baseline_own | self.baseline_shared)
        with self.assertRaises(AccessError):
            self.baseline_other.with_user(self.manager).read(["date"])

    def test_baseline_lines_follow_their_baseline(self):
        lines = self.env["project.baseline.line"].with_user(self.manager)
        all_lines = (self.baseline_own | self.baseline_shared | self.baseline_other).line_ids

        found = lines.search([("id", "in", all_lines.ids)])

        self.assertEqual(found, (self.baseline_own | self.baseline_shared).line_ids)
        with self.assertRaises(AccessError):
            self.baseline_other.line_ids.with_user(self.manager).read(["planned_date_start"])

    def test_a_manager_writes_the_lines_of_a_baseline_he_reads(self):
        line = self.baseline_own.line_ids.with_user(self.manager)
        line.planned_date_start = "2026-03-02 09:00:00"
        self.assertEqual(str(line.planned_date_start), "2026-03-02 09:00:00")

    def test_a_lag_follows_the_access_of_its_task(self):
        lags = self.env["project.task.dependency.lag"].with_user(self.manager)

        found = lags.search([("id", "in", (self.lag_own | self.lag_other).ids)])

        self.assertEqual(found, self.lag_own)
        self.assertEqual(self.lag_own.with_user(self.manager).lag_days, 2)
        with self.assertRaises(AccessError):
            self.lag_other.with_user(self.manager).read(["lag_days"])

    def test_a_manager_only_reads_lags(self):
        with self.assertRaises(AccessError):
            self.lag_own.with_user(self.manager).lag_days = 3

    def test_a_project_user_reaches_neither_baselines_nor_lags(self):
        for records in (self.baseline_own, self.baseline_own.line_ids, self.lag_own):
            with self.subTest(model=records._name), self.assertRaises(AccessError):
                records.with_user(self.project_user).read(["id"])
