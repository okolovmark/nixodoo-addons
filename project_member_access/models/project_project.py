from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .project_member import RESTRICTED_ACCESS_MODES, ROLE_RANKS

# Rules need one field per role threshold: two leaves on member_ids.user_id and
# member_ids.role would each match a different member row.
ROLE_USER_FIELDS = {
    "member_user_ids": "reader",
    "contributor_user_ids": "contributor",
    "editor_user_ids": "editor",
    "admin_user_ids": "admin",
}


class ProjectProject(models.Model):
    _inherit = "project.project"

    access_mode = fields.Selection(
        [("standard", "Standard"), ("public", "Public"), ("private", "Private")],
        string="Access",
        required=True,
        default="standard",
        tracking=True,
        help="Standard: the Visibility of the project decides, members change nothing. Public: every internal"
        " user reads the project and its tasks. Private: only its members do. In a Public or Private project"
        " the role of a member decides what they change.",
    )
    member_ids = fields.One2many("project.member", "project_id", string="Project Members", copy=True)
    member_user_ids = fields.Many2many(
        "res.users",
        "project_project_member_user_rel",
        "project_id",
        "user_id",
        string="Member Users",
        compute="_compute_role_user_ids",
        store=True,
        compute_sudo=True,
    )
    contributor_user_ids = fields.Many2many(
        "res.users",
        "project_project_contributor_user_rel",
        "project_id",
        "user_id",
        string="Contributor Users",
        compute="_compute_role_user_ids",
        store=True,
        compute_sudo=True,
        help="The members with the role Contributor, Editor or Admin.",
    )
    editor_user_ids = fields.Many2many(
        "res.users",
        "project_project_editor_user_rel",
        "project_id",
        "user_id",
        string="Editor Users",
        compute="_compute_role_user_ids",
        store=True,
        compute_sudo=True,
        help="The members with the role Editor or Admin.",
    )
    admin_user_ids = fields.Many2many(
        "res.users",
        "project_project_admin_user_rel",
        "project_id",
        "user_id",
        string="Admin Users",
        compute="_compute_role_user_ids",
        store=True,
        compute_sudo=True,
    )

    @api.depends("member_ids.user_id", "member_ids.role")
    def _compute_role_user_ids(self):
        for record in self:
            for field_name, lowest_role in ROLE_USER_FIELDS.items():
                record[field_name] = record.member_ids.filtered(
                    lambda member, rank=ROLE_RANKS[lowest_role]: ROLE_RANKS[member.role] >= rank
                ).user_id

    @api.constrains("access_mode", "member_ids")
    def _check_restricted_project_has_admin(self):
        projects_without_admin = self.sudo().filtered(
            lambda record: record.access_mode in RESTRICTED_ACCESS_MODES
            and "admin" not in record.member_ids.mapped("role")
        )
        if projects_without_admin:
            raise ValidationError(
                _("A Public or Private project needs a member with the role Admin.\nProjects: %s")
                % ", ".join(projects_without_admin.mapped("display_name"))
            )

    @api.constrains("access_mode", "develops_this_odoo")
    def _check_private_project_does_not_develop_this_odoo(self):
        private_projects = self.filtered(lambda record: record.access_mode == "private" and record.develops_this_odoo)
        if private_projects:
            raise ValidationError(
                _(
                    "The project that develops this Odoo cannot be Private: every internal user files"
                    " requests there.\nProjects: %s"
                )
                % ", ".join(private_projects.mapped("display_name"))
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._ensure_role_users_not_written(vals)
        projects = super().create(vals_list)
        projects._revoke_access_tokens()
        return projects

    def write(self, vals):
        self._ensure_role_users_not_written(vals)
        result = super().write(vals)
        if "access_mode" in vals:
            self._get_restricted_projects()._revoke_task_access_tokens()
        self._revoke_access_tokens()
        return result

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        recipients_data = super()._notify_get_recipients(message, msg_vals, **kwargs)
        return self.sudo()._filter_reading_recipients(self, recipients_data)

    def _portal_ensure_token(self):
        # a token opens the project to whoever holds it, whatever their role
        if self.access_mode in RESTRICTED_ACCESS_MODES:
            return ""
        return super()._portal_ensure_token()

    def _ensure_role_users_not_written(self, vals):
        # stored computes without inverse: a written value would hold until the members change
        if ROLE_USER_FIELDS.keys() & vals.keys():
            raise UserError(_("The users of each role follow the members of the project: change its members instead."))

    def _get_restricted_projects(self):
        return self.filtered(lambda record: record.access_mode in RESTRICTED_ACCESS_MODES)

    def _revoke_access_tokens(self):
        """Clear the access token of those of these projects that are Public or Private,
        whatever filled it: values, a context default, a copy, an import.
        """
        tokened_projects = self.sudo()._get_restricted_projects().filtered("access_token")
        # guarded: the write comes back here
        if tokened_projects:
            tokened_projects.write({"access_token": False})

    def _revoke_task_access_tokens(self):
        """Clear the access tokens of all the tasks of these projects, archived included:
        core revokes them only when the Visibility leaves Portal.
        """
        self.env["project.task"].sudo().with_context(active_test=False).search(
            [("project_id", "in", self.ids)]
        )._revoke_access_tokens()

    def _filter_reading_recipients(self, record, recipients_data):
        """Return the recipients of a notification on record, this project or one of its
        tasks: in a Public or Private project, only those whose active user reads record
        by their own access rights and rules, in all their companies as a mail link opens it.
        """
        if self.access_mode not in RESTRICTED_ACCESS_MODES:
            return recipients_data
        context = dict(record.env.context)
        # The poster's companies would raise AccessError for a recipient outside them.
        context.pop("allowed_company_ids", None)
        reader_uids = set()
        for uid in {data.get("uid") for data in recipients_data} - {None}:
            record_as_recipient = record.with_env(record.env(user=uid, su=False, context=context))
            if record_as_recipient.check_access_rights(
                "read", raise_exception=False
            ) and record_as_recipient._filter_access_rules("read"):
                reader_uids.add(uid)
        return [data for data in recipients_data if data.get("uid") in reader_uids]
