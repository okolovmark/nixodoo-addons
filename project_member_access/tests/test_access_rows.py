from odoo import Command
from odoo.exceptions import UserError
from odoo.fields import Domain
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import MODULE, MemberAccessCommon

SHARE_REFUSAL = {"type": "ir.actions.client", "tag": "display_notification"}
BURNDOWN = "project.task.burndown.chart.report"


@tagged("post_install", "-at_install")
class TestAccessRows(MemberAccessCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.personas = (
            cls.reader
            | cls.contributor
            | cls.editor
            | cls.admin
            | cls.manager
            | cls.sysadmin
            | cls.outsider
            | cls.employee
            | cls.portal
        )
        cls.module_accesses = cls.env["ir.access"].browse(
            cls.env["ir.model.data"].search([("module", "=", MODULE), ("model", "=", "ir.access")]).mapped("res_id")
        )

    def test_every_row_evaluates_for_every_persona(self):
        """In Python and in SQL, and no read restriction is FALSE: Odoo 20 raises on a
        search whose read domain is FALSE, where the user should find nothing.
        """
        accesses_with_domain = self.module_accesses.filtered("domain")
        rights = self.module_accesses - accesses_with_domain
        self.assertEqual(set(rights.mapped("model_id.model")), {"project.member"}, "only the rights on members")
        self.assertTrue(all(rights.mapped("group_id")))
        for access in accesses_with_domain:
            for user in self.personas:
                with self.subTest(access=access.name, user=user.login), self.env.cr.savepoint():
                    eval_context = self.env["ir.access"].with_user(user)._eval_context()
                    domain = Domain(safe_eval(access.domain, eval_context))
                    model = self.env[access.model_id.model].with_user(user).sudo().with_context(active_test=False)
                    if model._name == BURNDOWN:
                        # its rows are only grouped, never fetched
                        model._read_group(domain, ["date:month", "stage_id"], ["__count"])
                    else:
                        model.search_count(domain)
                        model.search([], limit=5).filtered_domain(domain)
                    if not access.group_id and access.for_read:
                        self.assertFalse(domain.optimize(model).is_false())

    def test_every_persona_searches_the_models_it_reads(self):
        models_read = {
            "project.project": self.personas,
            "project.task": self.personas,
            "project.member": self.personas - self.portal,
            "project.milestone": self.personas - self.portal,
            "project.update": self.personas - self.portal,
            "project.pull.request": self.personas - self.portal,
            BURNDOWN: self.personas - self.employee - self.portal,
        }
        for model_name, users in models_read.items():
            for user in users:
                with self.subTest(model=model_name, user=user.login):
                    self.env[model_name].with_user(user).search_count([])

    def test_sharing_is_refused_inside(self):
        self.projects.privacy_visibility = "portal"
        for project in self.public_project | self.private_project:
            with self.subTest(project=project.name):
                action = project.with_user(self.manager).action_open_share_project_wizard()
                self.assertEqual({key: action[key] for key in SHARE_REFUSAL}, SHARE_REFUSAL)
                self.assertEqual(action["params"]["type"], "danger")
        for task in self.public_task | self.private_task:
            with self.subTest(task=task.project_id.name):
                action = task.with_user(self.manager).action_open_share_task_wizard()
                self.assertEqual({key: action[key] for key in SHARE_REFUSAL}, SHARE_REFUSAL)

    def test_share_wizards_are_refused_inside(self):
        """Reached by an RPC or an action URL past the refused share actions."""
        self.projects.privacy_visibility = "portal"
        collaborators = [Command.create({"partner_id": self.portal.partner_id.id, "access_mode": "view"})]
        for project in self.public_project | self.private_project:
            ProjectWizard = self.env["project.share.wizard"].with_user(self.manager)
            with self.subTest(project=project.name):
                with self.assertRefused(UserError, "is not shared with external people", in_place=True):
                    ProjectWizard.with_context(active_model="project.project", active_id=project.id).create(
                        {"collaborator_ids": collaborators}
                    )
                with self.assertRefused(UserError, "is not shared with external people", in_place=True):
                    ProjectWizard.create(
                        {"res_model": "project.project", "res_id": project.id, "collaborator_ids": collaborators}
                    )
                self.assertFalse(project.collaborator_ids)
        for task in self.public_task | self.private_task:
            for wizard_model in ("task.share.wizard", "portal.share"):
                Wizard = self.env[wizard_model].with_user(self.manager)
                with self.subTest(project=task.project_id.name, wizard=wizard_model):
                    with self.assertRefused(UserError, "is not shared with external people"):
                        Wizard.with_context(active_model="project.task", active_id=task.id).create(
                            {"partner_ids": self.portal.partner_id.ids}
                        )
        standard_wizard = (
            self.env["project.share.wizard"]
            .with_user(self.manager)
            .with_context(active_model="project.project", active_id=self.standard_project.id)
            .create({"collaborator_ids": collaborators})
        )
        self.assertEqual(standard_wizard.resource_ref, self.standard_project)
        self.assertEqual(self.standard_project.collaborator_ids.partner_id, self.portal.partner_id)

    def test_sharing_a_standard_project_is_left_to_core(self):
        self.standard_project.privacy_visibility = "portal"
        project_action = self.standard_project.with_user(self.manager).action_open_share_project_wizard()
        self.assertEqual(project_action["res_model"], "project.share.wizard")
        task_action = self.standard_task.with_user(self.manager).action_open_share_task_wizard()
        self.assertEqual(task_action["res_model"], "task.share.wizard")
