import glob
import logging
import os
import re
from urllib.parse import quote

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_module_path
from odoo.osv import expression

_logger = logging.getLogger(__name__)

FRONT_MATTER_RE = re.compile(r"\A\s*---[ \t]*\n(.*?)\n---[ \t]*\n?(.*)\Z", re.DOTALL)
MANAGER_GROUP = "release_notes.group_release_note_manager"
PUBLISH_FIELDS = ("is_published", "published_by_id", "published_date", "is_notified")
CLASS_ATTRIBUTE_RE = re.compile(r"""\bclass=(["'])(.*?)\1""")
BUTTON_CLASS = "btn btn-primary"
TICKET_URL_PARAM = "release_notes.ticket_url"
TICKET_MODEL_PARAM = "release_notes.ticket_model"
ANNOUNCEMENT_NOTIFICATION = "release_notes/updated"
SEEN_RELATION = "release_note_seen_user_rel"


class ReleaseNote(models.Model):
    _name = "release.note"
    _description = "Release Note"
    _inherit = ["mail.thread"]
    _order = "published_date desc, id desc"

    key = fields.Char(required=True, copy=False, help="Identity the notes loader matches on, e.g. PROJ-123.")
    name = fields.Char(string="Title", required=True, tracking=True)
    summary = fields.Char(help="One line under the title: what changed for the user.")
    body = fields.Html()
    tag_ids = fields.Many2many("release.note.tag", string="Tags")
    model_ids = fields.Many2many(
        "ir.model",
        string="Models",
        help="Business models the change is about; users who can read one of them are notified.",
    )
    is_published = fields.Boolean(string="Published", copy=False, tracking=True)
    published_by_id = fields.Many2one("res.users", string="Published by", readonly=True, copy=False)
    published_date = fields.Datetime(readonly=True, copy=False)
    deployer_id = fields.Many2one(
        "res.users",
        string="Deployed by",
        copy=False,
        tracking=True,
        domain=lambda self: [("groups_id", "in", self.env.ref(MANAGER_GROUP).ids)],
    )
    is_notified = fields.Boolean(
        string="Announced",
        readonly=True,
        copy=False,
        help="Set on the first publish: the note was announced in the main menu of its users.",
    )
    seen_user_ids = fields.Many2many(
        "res.users",
        SEEN_RELATION,
        "note_id",
        "user_id",
        string="Seen by",
        readonly=True,
        copy=False,
        groups=MANAGER_GROUP,
        help="Users who opened or skipped the note: it is no longer announced to them.",
    )
    ticket_url = fields.Char(
        compute="_compute_ticket_url",
        help="Link the key opens, for users who can read the ticket model.",
    )
    demo_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Demo video",
        compute="_compute_demo_attachment_id",
        help="The newest video attached to the note.",
    )

    _sql_constraints = [
        ("key_unique", "unique(key)", "A release note with this key already exists."),
    ]

    @api.depends("key")
    @api.depends_context("uid")
    def _compute_ticket_url(self):
        params = self.env["ir.config_parameter"].sudo()
        url_template = params.get_param(TICKET_URL_PARAM)
        ticket_model = params.get_param(TICKET_MODEL_PARAM)
        can_open = bool(url_template) and (
            not ticket_model
            or (ticket_model in self.env and self.env[ticket_model].check_access_rights("read", raise_exception=False))
        )
        for note in self:
            note.ticket_url = (
                url_template.replace("{key}", quote(note.key, safe="")) if can_open and note.key else False
            )

    # no field path to depend on: attachments point at the note by res_model and res_id
    @api.depends()
    def _compute_demo_attachment_id(self):
        videos = (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", self._name),
                    ("res_id", "in", self.ids),
                    ("mimetype", "=like", "video/%"),
                ],
                order="create_date desc, id desc",
            )
        )
        for note in self:
            note.demo_attachment_id = videos.filtered(lambda video, note=note: video.res_id == note.id)[:1]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("body"):
                vals["body"] = self._unify_buttons(vals["body"])
            stored_vals = self._add_missing_default_values(dict(vals))
            if not self.env.su and any(stored_vals.get(name) for name in PUBLISH_FIELDS):
                raise AccessError(_("A release note is published only with its Publish button."))
            if stored_vals.get("deployer_id"):
                self._check_deployer_assignment(stored_vals["deployer_id"])
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and self._publish_fields_changed(vals):
            raise AccessError(_("A release note is published only with its Publish button."))
        if "key" in vals and not self.env.su and any(note.key != vals["key"] for note in self):
            raise UserError(_("The key of a release note cannot be changed after it is created."))
        if "deployer_id" in vals:
            if any(self.mapped("deployer_id")) and not self._can_manage_deployer():
                raise AccessError(_("Only an administrator can change who deployed a release note."))
            self._check_deployer_assignment(vals["deployer_id"])
        if vals.get("body"):
            vals = dict(vals, body=self._unify_buttons(vals["body"]))
        return super().write(vals)

    def action_publish(self):
        """Publish the notes; each one is announced in its users' main menu.

        Only the note's Deployed by user may publish, and the note needs at least
        one installed model: the models decide who is announced the note.
        """
        self.check_access_rights("write")
        self.check_access_rule("write")
        for note in self:
            if not note.deployer_id:
                raise UserError(_("No deployer is set on release note %s, so nobody can publish it yet.") % note.name)
            if note.deployer_id != self.env.user:
                raise UserError(_("Only %s, who deployed this update, can publish it.") % note.deployer_id.name)
            if not note.sudo().model_ids.filtered(lambda model: model.model in self.env):
                raise UserError(_("Set at least one model before publishing: it decides who is notified."))
        self.sudo().write(
            {
                "is_published": True,
                "published_by_id": self.env.user.id,
                "published_date": fields.Datetime.now(),
            }
        )
        self.filtered(lambda note: not note.is_notified).sudo().write({"is_notified": True})
        # the deployer read the note before publishing it: it is not news to them
        self._mark_seen_by(self.env.user)
        self._send_announcement_update()

    def action_unpublish(self):
        """Hide the notes from internal users and from the main menu announcement."""
        self.check_access_rights("write")
        self.check_access_rule("write")
        self.sudo().write({"is_published": False})
        self._send_announcement_update()

    @api.model
    def get_announcement(self):
        """What the main menu announces to the current user.

        :return: dict with ``count``, ``note_ids`` (newest first) and ``title``
            (of the newest note)
        """
        notes = self._get_announced_notes()
        return {"count": len(notes), "note_ids": notes.ids, "title": notes[:1].name or ""}

    def action_mark_seen(self):
        """Mark the published notes among these as seen by the current user.

        The main menu's View marks the note it opens, Skip the notes its panel
        shows: never a note published since the panel was drawn.
        """
        self.check_access_rights("read")
        self.check_access_rule("read")
        self.filtered("is_published")._mark_seen_by(self.env.user)
        return True

    def _get_announced_notes(self):
        """Published notes the current user has neither opened nor skipped.

        Only notes published after the user was created, and only notes about a
        model the user can read.
        """
        user = self.env.user
        if user.share:
            return self.browse()
        notes = self.sudo().search(
            [
                ("is_published", "=", True),
                ("published_date", ">", user.create_date),
                ("seen_user_ids", "not in", user.ids),
            ],
            order="published_date desc, id desc",
        )
        return notes.with_env(self.env).filtered(lambda note: note._is_about_a_model_of(user))

    def _mark_seen_by(self, user):
        """Record that ``user`` saw these notes.

        A plain insert into the relation, not a write: the note's row is left
        alone, so users opening the same note never wait on each other.
        """
        if not self:
            return
        self.env.cr.execute(
            """
            INSERT INTO release_note_seen_user_rel (note_id, user_id)
            SELECT note_id, %s FROM unnest(%s) AS note_id
            ON CONFLICT DO NOTHING
            """,
            (user.id, self.ids),
        )
        self.invalidate_recordset(["seen_user_ids"])
        self._send_announcement_update(user)

    def _is_about_a_model_of(self, user):
        self.ensure_one()
        return any(
            self.env[name].with_user(user).check_access_rights("read", raise_exception=False)
            for name in self.sudo().model_ids.mapped("model")
            if name in self.env
        )

    def _send_announcement_update(self, users=None):
        """Tell the open web clients of ``users`` (default: the notes' audience) to reload the announcement."""
        if users is None:
            users = self.env["res.users"]
            for note in self:
                users |= note._get_notification_recipients()
        self.env["bus.bus"]._sendmany([(user.partner_id, ANNOUNCEMENT_NOTIFICATION, {}) for user in users])

    def _publish_fields_changed(self, vals):
        for field_name in (name for name in PUBLISH_FIELDS if name in vals):
            field = self._fields[field_name]
            for note in self:
                new_value = field.convert_to_record(field.convert_to_cache(vals[field_name], note), note)
                if new_value != note[field_name]:
                    return True
        return False

    @api.model
    def _unify_buttons(self, body):
        """Give every button in the body the one primary look.

        The backend theme draws ``btn-secondary``, ``btn-link`` and the outline
        variants as bare links, so a note with two kinds of button reads as one
        button and one link.
        """

        def unify(match):
            if "btn" not in match.group(2).split():
                return match.group(0)
            return 'class="%s"' % BUTTON_CLASS

        unified_body = CLASS_ATTRIBUTE_RE.sub(unify, str(body))
        return Markup(unified_body) if isinstance(body, Markup) else unified_body

    def _can_manage_deployer(self):
        return self.env.su or self.env.user.has_group("base.group_system")

    def _check_deployer_assignment(self, deployer_id):
        if deployer_id and not self.env["res.users"].browse(deployer_id).has_group(MANAGER_GROUP):
            raise UserError(_("The deployer of a release note must be a Release Notes manager."))
        if self._can_manage_deployer():
            return
        if deployer_id != self.env.user.id:
            raise AccessError(_("You can only set yourself as the deployer of a release note."))

    def _get_notification_recipients(self):
        self.ensure_one()
        model_names = [name for name in self.sudo().model_ids.mapped("model") if name in self.env]
        if not model_names:
            return self.env["res.users"]
        candidate_users = (
            self.env["res.users"]
            .sudo()
            .search(
                [
                    ("share", "=", False),
                    ("id", "!=", self.env.ref("base.user_root").id),
                ]
            )
        )
        return candidate_users.filtered(lambda user: self._is_about_a_model_of(user))

    @api.model
    def _load_release_notes(self, module=None, path=None):
        """Create an unpublished note for every file in a notes folder whose key is new.

        A project's holder module calls it from a data file with its own name, so
        it runs on each install or update of that module. An existing key is
        skipped, so a note is never overwritten from its file. A file that cannot
        be read as a note (no valid header, not UTF-8), or that declares a key an
        earlier file already has, is logged as an error and skipped: the other
        notes load and the update goes on, so a note never stops a deploy.

        :param module: name of the module whose `notes/` folder is read
        :param path: folder to read instead of a module's `notes/`
        :return: the created notes
        """
        notes_path = path
        if not notes_path and module:
            module_path = get_module_path(module)
            notes_path = module_path and os.path.join(module_path, "notes")
        if not notes_path or not os.path.isdir(notes_path):
            return self.browse()
        file_by_key = {}
        parsed_notes = []
        for file_path in sorted(glob.glob(os.path.join(notes_path, "*.html"))):
            try:
                note = self._parse_release_note_file(file_path)
            except (UserError, OSError, UnicodeDecodeError) as error:
                _logger.error("Release note file %s skipped: %s", os.path.basename(file_path), error)
                continue
            if note["key"] in file_by_key:
                _logger.error(
                    "Release note file %s skipped: file %s already declares the key %s.",
                    note["file_name"],
                    file_by_key[note["key"]],
                    note["key"],
                )
                continue
            file_by_key[note["key"]] = note["file_name"]
            parsed_notes.append(note)
        existing_keys = set(self.search([("key", "in", list(file_by_key))]).mapped("key"))
        new_notes = [note for note in parsed_notes if note["key"] not in existing_keys]
        return self.create([self._prepare_loaded_note_vals(note) for note in new_notes])

    def _parse_release_note_file(self, file_path):
        file_name = os.path.basename(file_path)
        with open(file_path, encoding="utf-8-sig") as note_file:
            content = note_file.read()
        match = FRONT_MATTER_RE.match(content)
        if not match:
            raise UserError(_("Release note file %s has no front-matter header between --- lines.") % file_name)
        header = {}
        for line in match.group(1).splitlines():
            field_name, separator, value = line.partition(":")
            if separator:
                header[field_name.strip().lower()] = value.strip()
        if not header.get("key") or not header.get("title"):
            raise UserError(_("Release note file %s must declare both a key and a title in its header.") % file_name)
        return {
            "file_name": file_name,
            "key": header["key"],
            "title": header["title"],
            "summary": header.get("summary") or False,
            "tags": self._split_header_list(header.get("tags")),
            "models": self._split_header_list(header.get("models")),
            "body": match.group(2).strip(),
        }

    def _split_header_list(self, header_value):
        return [item.strip() for item in (header_value or "").split(",") if item.strip()]

    def _prepare_loaded_note_vals(self, note):
        tag_model = self.env["release.note.tag"]
        tag_by_name = {}
        if note["tags"]:
            candidate_tags = tag_model.search(expression.OR([[("name", "=ilike", name)] for name in note["tags"]]))
            tag_by_name = {tag.name.lower(): tag for tag in candidate_tags}
        tags = tag_model.browse()
        for name in note["tags"]:
            if name.lower() not in tag_by_name:
                tag_by_name[name.lower()] = tag_model.create({"name": name})
            tags |= tag_by_name[name.lower()]
        business_models = self.env["ir.model"].search([("model", "in", note["models"])])
        return {
            "key": note["key"],
            "name": note["title"],
            "summary": note["summary"],
            "body": note["body"],
            "tag_ids": [Command.set(tags.ids)],
            "model_ids": [Command.set(business_models.ids)],
        }
