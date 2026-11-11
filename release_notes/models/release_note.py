import glob
import logging
import os
import re

from markupsafe import Markup

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.modules import Manifest

_logger = logging.getLogger(__name__)

FRONT_MATTER_RE = re.compile(r"\A\s*---[ \t]*\n(.*?)\n---[ \t]*\n?(.*)\Z", re.DOTALL)
MANAGER_GROUP = "release_notes.group_release_note_manager"
PUBLISH_FIELDS = ("is_published", "published_by_id", "published_date", "is_notified")
CLASS_ATTRIBUTE_RE = re.compile(r"""\bclass=(["'])(.*?)\1""")
BUTTON_CLASS = "btn btn-primary"


class ReleaseNote(models.Model):
    _name = "release.note"
    _description = "Release Note"
    _inherit = ["mail.thread"]
    _order = "published_date desc, id desc"

    key = fields.Char(required=True, copy=False, help="Identity the notes loader matches on, e.g. PROJ-123.")
    name = fields.Char(string="Title", required=True, tracking=True)
    summary = fields.Char(help="One line shown in the notification.")
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
        domain=lambda self: [("all_group_ids", "in", self.env.ref(MANAGER_GROUP).ids)],
    )
    is_notified = fields.Boolean(string="Notified", readonly=True, copy=False)

    _key_unique = models.Constraint("unique(key)", "A release note with this key already exists.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("body"):
                vals["body"] = self._unify_buttons(vals["body"])
            stored_vals = self._add_missing_default_values(dict(vals))
            if not self.env.su and any(stored_vals.get(name) for name in PUBLISH_FIELDS):
                raise AccessError(self.env._("A release note is published only with its Publish button."))
            if stored_vals.get("deployer_id"):
                self._check_deployer_assignment(stored_vals["deployer_id"])
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and self._publish_fields_changed(vals):
            raise AccessError(self.env._("A release note is published only with its Publish button."))
        if "key" in vals and not self.env.su and any(note.key != vals["key"] for note in self):
            raise UserError(self.env._("The key of a release note cannot be changed after it is created."))
        if "deployer_id" in vals:
            if any(self.mapped("deployer_id")) and not self._can_manage_deployer():
                raise AccessError(self.env._("Only an administrator can change who deployed a release note."))
            self._check_deployer_assignment(vals["deployer_id"])
        if vals.get("body"):
            vals = dict(vals, body=self._unify_buttons(vals["body"]))
        return super().write(vals)

    def action_publish(self):
        """Publish the notes and announce each one once by OdooBot direct chat.

        Only the note's Deployed by user may publish, and the note needs at least
        one installed model: the models decide who is notified.
        """
        self.check_access("write")
        for note in self:
            if not note.deployer_id:
                raise UserError(
                    self.env._("No deployer is set on release note %s, so nobody can publish it yet.", note.name)
                )
            if note.deployer_id != self.env.user:
                raise UserError(self.env._("Only %s, who deployed this update, can publish it.", note.deployer_id.name))
            if not note.sudo().model_ids.filtered(lambda model: model.model in self.env):
                raise UserError(self.env._("Set at least one model before publishing: it decides who is notified."))
        self.sudo().write(
            {
                "is_published": True,
                "published_by_id": self.env.user.id,
                "published_date": fields.Datetime.now(),
            }
        )
        pending_notes = self.filtered(lambda note: not note.is_notified)
        for note in pending_notes:
            note._notify_recipients()
        pending_notes.sudo().write({"is_notified": True})

    def action_unpublish(self):
        """Hide the notes from internal users; chats already sent stay and are not re-sent."""
        self.check_access("write")
        self.sudo().write({"is_published": False})

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
            raise UserError(self.env._("The deployer of a release note must be a Release Notes manager."))
        if self._can_manage_deployer():
            return
        if deployer_id != self.env.user.id:
            raise AccessError(self.env._("You can only set yourself as the deployer of a release note."))

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
        return candidate_users.filtered(
            lambda user: any(self.env[name].with_user(user).has_access("read") for name in model_names)
        )

    def _notify_recipients(self):
        self.ensure_one()
        odoobot = self.env.ref("base.user_root")
        channel_model = self.env["discuss.channel"].with_user(odoobot)
        note_url = "/odoo/release.note/%s" % self.id
        headline = self.env._("New update: %s", Markup("<b>%s</b>") % self.name)
        body = Markup('<p>&#127881; %s</p><p>%s</p><p><a href="%s">%s</a></p>') % (
            headline,
            self.summary or "",
            note_url,
            self.env._("Open release note"),
        )
        for user in self._get_notification_recipients():
            channel = channel_model._get_or_create_chat([user.partner_id.id])
            channel.message_post(
                body=body,
                author_id=odoobot.partner_id.id,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )

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
            manifest = Manifest.for_addon(module)
            notes_path = manifest and os.path.join(manifest.path, "notes")
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
            raise UserError(self.env._("Release note file %s has no front-matter header between --- lines.", file_name))
        header = {}
        for line in match.group(1).splitlines():
            field_name, separator, value = line.partition(":")
            if separator:
                header[field_name.strip().lower()] = value.strip()
        if not header.get("key") or not header.get("title"):
            raise UserError(
                self.env._("Release note file %s must declare both a key and a title in its header.", file_name)
            )
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
            candidate_tags = tag_model.search(Domain.OR(Domain("name", "=ilike", name) for name in note["tags"]))
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
