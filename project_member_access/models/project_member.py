from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

ROLES = [("reader", "Reader"), ("contributor", "Contributor"), ("editor", "Editor"), ("admin", "Admin")]
ROLE_RANKS = {role: rank for rank, (role, _label) in enumerate(ROLES)}
RESTRICTED_ACCESS_MODES = ("public", "private")


class ProjectMember(models.Model):
    _name = "project.member"
    _description = "Project Member"
    _rec_name = "user_id"

    project_id = fields.Many2one("project.project", required=True, ondelete="cascade", index=True)
    user_id = fields.Many2one(
        "res.users", string="User", required=True, ondelete="cascade", domain=[("share", "=", False)]
    )
    role = fields.Selection(ROLES, required=True, default="reader")

    _sql_constraints = [
        ("project_user_unique", "unique(project_id, user_id)", "A user is a member of a project once."),
    ]

    @api.constrains("user_id")
    def _check_user_is_internal(self):
        portal_members = self.filtered(lambda record: record.user_id.share)
        if portal_members:
            raise ValidationError(
                _("Only an internal user is a project member. Portal users: %s")
                % ", ".join(portal_members.mapped("user_id.display_name"))
            )

    @api.model_create_multi
    def create(self, vals_list):
        # read before the rows exist: a new row lets its own user read a Private project
        readable_projects = (
            self.env["project.project"]
            .browse({vals["project_id"] for vals in vals_list if vals.get("project_id")})
            ._filter_access_rules("read")
        )
        members = super().create(vals_list)
        members._ensure_added_by_an_admin(readable_projects)
        return members

    def write(self, vals):
        # before the checks in sudo, whose messages name the project
        self.check_access_rights("write")
        self.check_access_rule("write")
        self._ensure_project_and_user_kept(vals)
        if vals.get("role", "admin") != "admin":
            self._ensure_other_admin(
                _(
                    "%(user)s is the last Admin of %(project)s: give another member the Admin role"
                    " before lowering theirs."
                )
            )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_last_admin(self):
        self._ensure_other_admin(
            _("%(user)s is the last Admin of %(project)s: give another member the Admin role before removing them.")
        )

    def _ensure_added_by_an_admin(self, readable_projects):
        """The create rule reads admin_user_ids once the rows exist, where a new Admin
        row lets its own creator in: check the Admins the project had before. A
        Project Administrator also adds the members of a Standard project, and the
        first ones of a project made Public or Private. The refusal names only the
        readable_projects.
        """
        if self.env.su or self.env.user._is_system():
            return
        user = self.env.user
        is_project_manager = user.has_group("project.group_project_manager")
        for project in self.sudo().project_id:
            former_admins = (project.member_ids - self).filtered(lambda record: record.role == "admin").user_id
            if user in former_admins or (
                is_project_manager and (project.access_mode not in RESTRICTED_ACCESS_MODES or not former_admins)
            ):
                continue
            if project in readable_projects:
                raise AccessError(_("Only an Admin of %s adds its members.") % project.display_name)
            raise AccessError(_("Only an Admin of the project adds its members."))

    def _ensure_project_and_user_kept(self, vals):
        """The write rule is checked on the row before the write: a row moved to
        another project, or given to another user, would escape it.
        """
        for field_name in ("project_id", "user_id"):
            if field_name in vals and any(record[field_name].id != vals[field_name] for record in self.sudo()):
                raise ValidationError(
                    _("A member keeps their project and their user: remove the member and add the user again.")
                )

    def _ensure_other_admin(self, message):
        """Refuse when the rows of self are the last Admin rows of a Public or Private project,
        an archived user counting for none. Sudo: the other members are counted whoever asks.
        """
        for member in self.sudo().filtered(
            lambda record: record.role == "admin" and record.project_id.access_mode in RESTRICTED_ACCESS_MODES
        ):
            other_admins = (
                member.project_id.member_ids.filtered(lambda record: record.role == "admin" and record.user_id.active)
                - self
            )
            if not other_admins:
                raise ValidationError(
                    message % {"user": member.user_id.display_name, "project": member.project_id.display_name}
                )
