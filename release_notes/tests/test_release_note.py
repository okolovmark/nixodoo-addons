import os
import tempfile
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.modules import Manifest
from odoo.tests import Form, new_test_user
from odoo.tests.common import TransactionCase

from odoo.addons.release_notes.models import release_note as release_note_module

LOADER_LOGGER = "odoo.addons.release_notes.models.release_note"
NOTE_TEMPLATE = """---
key: %(key)s
title: %(title)s
summary: Short summary
tags: %(tags)s
models: %(models)s
---
<h2>What changed</h2><p>Details.</p>
"""


class TestReleaseNote(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Note = cls.env["release.note"]
        cls.manager = new_test_user(
            cls.env, "rn_manager", groups="base.group_user,release_notes.group_release_note_manager"
        )
        cls.other_manager = new_test_user(
            cls.env, "rn_other_manager", groups="base.group_user,release_notes.group_release_note_manager"
        )
        cls.internal_user = new_test_user(cls.env, "rn_internal", groups="base.group_user")
        cls.portal_user = new_test_user(cls.env, "rn_portal", groups="base.group_portal")
        cls.inactive_admin = new_test_user(cls.env, "rn_inactive", groups="base.group_system")
        cls.inactive_admin.active = False
        cls.admin_user = new_test_user(cls.env, "rn_admin", groups="base.group_system")
        cls.odoobot = cls.env.ref("base.user_root")
        cls.cron_model = cls.env["ir.model"]._get("ir.cron")
        cls.partner_model = cls.env["ir.model"]._get("res.partner")

    def _write_note_file(self, folder, file_name, **values):
        note_values = {
            "key": "PROJ-T1",
            "title": "Test note",
            "tags": "Sales",
            "models": "res.partner",
        }
        note_values.update(values)
        with open(os.path.join(folder, file_name), "w", encoding="utf-8") as note_file:
            note_file.write(NOTE_TEMPLATE % note_values)

    def _create_note(self, **values):
        note_values = {"key": "PROJ-TEST", "name": "Release test", "summary": "A summary"}
        note_values.update(values)
        return self.Note.create(note_values)

    def _odoobot_chat_messages(self, user, note):
        channels = self.env["discuss.channel"].search(
            [
                ("channel_type", "=", "chat"),
                ("channel_partner_ids", "in", user.partner_id.ids),
                ("channel_partner_ids", "in", self.odoobot.partner_id.ids),
            ]
        )
        return channels.message_ids.filtered(
            lambda message: message.author_id == self.odoobot.partner_id and note.name in message.body
        )

    def test_loader_creates_unpublished_note_with_tags_and_models(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_note_file(
                folder, "proj_t1.html", tags="Sales, Brand New Tag", models="res.partner, no.such.model"
            )
            self._write_note_file(folder, "ignored.txt", key="PROJ-TXT")
            created_notes = self.Note._load_release_notes(path=folder)
        self.assertEqual(created_notes.mapped("key"), ["PROJ-T1"])
        self.assertFalse(created_notes.is_published)
        self.assertEqual(created_notes.name, "Test note")
        self.assertEqual(created_notes.summary, "Short summary")
        self.assertIn("What changed", created_notes.body)
        self.assertEqual(sorted(created_notes.tag_ids.mapped("name")), ["Brand New Tag", "Sales"])
        self.assertEqual(
            created_notes.tag_ids.filtered(lambda tag: tag.name == "Sales"),
            self.env.ref("release_notes.release_note_tag_sales"),
            "An existing tag must be reused, not duplicated",
        )
        self.assertEqual(created_notes.model_ids, self.partner_model, "Unknown models are skipped")

    def test_loader_never_duplicates_or_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_note_file(folder, "proj_t1.html")
            first_load = self.Note._load_release_notes(path=folder)
            first_load.write({"name": "Edited in Odoo"})
            self._write_note_file(folder, "proj_t1.html", title="Changed in the file")
            second_load = self.Note._load_release_notes(path=folder)
        self.assertFalse(second_load)
        self.assertEqual(self.Note.search_count([("key", "=", "PROJ-T1")]), 1)
        self.assertEqual(first_load.name, "Edited in Odoo")

    def test_parser_rejects_malformed_file(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_note_file(folder, "no_title.html", title="")
            with self.assertRaisesRegex(UserError, "no_title.html"):
                self.Note._parse_release_note_file(os.path.join(folder, "no_title.html"))
            with open(os.path.join(folder, "no_header.html"), "w", encoding="utf-8") as note_file:
                note_file.write("<p>No header</p>")
            with self.assertRaisesRegex(UserError, "no_header.html"):
                self.Note._parse_release_note_file(os.path.join(folder, "no_header.html"))

    def test_loader_skips_unreadable_file_and_loads_the_rest(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_note_file(folder, "a_no_title.html", key="PROJ-NOTITLE", title="")
            with open(os.path.join(folder, "b_no_header.html"), "w", encoding="utf-8") as note_file:
                note_file.write("<p>No header</p>")
            with open(os.path.join(folder, "c_not_utf8.html"), "wb") as note_file:
                note_file.write(b"---\nkey: PROJ-LATIN\ntitle: Caf\xe9\n---\n<p>Body</p>\n")
            self._write_note_file(folder, "d_good.html", key="PROJ-GOOD")
            with self.assertLogs(LOADER_LOGGER, level="ERROR") as captured:
                created_notes = self.Note._load_release_notes(path=folder)
        self.assertEqual(created_notes.mapped("key"), ["PROJ-GOOD"], "A bad file must not stop the others")
        logged = "\n".join(captured.output)
        for file_name in ("a_no_title.html", "b_no_header.html", "c_not_utf8.html"):
            self.assertIn(file_name, logged)
        self.assertNotIn("d_good.html", logged)
        self.assertFalse(self.Note.search([("key", "in", ["PROJ-NOTITLE", "PROJ-LATIN"])]))

    def test_loader_reads_notes_folder_of_module(self):
        with tempfile.TemporaryDirectory() as addons_folder:
            module_folder = os.path.join(addons_folder, "proj_release_notes")
            os.makedirs(os.path.join(module_folder, "notes"))
            self._write_note_file(os.path.join(module_folder, "notes"), "proj_t1.html")
            self._write_note_file(module_folder, "outside_notes.html", key="PROJ-OUTSIDE")
            holder_manifest = Manifest(path=module_folder, manifest_content={})
            with patch.object(release_note_module, "Manifest") as manifest_class:
                manifest_class.for_addon.return_value = holder_manifest
                created_notes = self.Note._load_release_notes("proj_release_notes")
        manifest_class.for_addon.assert_called_once_with("proj_release_notes")
        self.assertEqual(created_notes.mapped("key"), ["PROJ-T1"])

    def test_loader_without_folder_does_nothing(self):
        self.assertFalse(self.Note._load_release_notes(path="/nonexistent/release/notes"))
        self.assertFalse(self.Note._load_release_notes(), "No module and no path: nothing to read")
        self.assertFalse(self.Note._load_release_notes("release_notes"), "The engine ships no notes of its own")
        with self.assertLogs("odoo.modules.module", level="WARNING"):
            self.assertFalse(self.Note._load_release_notes("no_such_release_notes_module"))

    def test_loader_reads_file_with_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "bom.html"), "w", encoding="utf-8-sig") as note_file:
                note_file.write(
                    NOTE_TEMPLATE
                    % {
                        "key": "PROJ-BOM",
                        "title": "BOM note",
                        "tags": "Sales",
                        "models": "res.partner",
                    }
                )
            created_notes = self.Note._load_release_notes(path=folder)
        self.assertEqual(created_notes.key, "PROJ-BOM")

    def test_loader_reuses_tag_regardless_of_case(self):
        tag_count = self.env["release.note.tag"].search_count([])
        with tempfile.TemporaryDirectory() as folder:
            self._write_note_file(folder, "proj_t1.html", tags="sales, SALES")
            created_notes = self.Note._load_release_notes(path=folder)
        self.assertEqual(created_notes.tag_ids, self.env.ref("release_notes.release_note_tag_sales"))
        self.assertEqual(self.env["release.note.tag"].search_count([]), tag_count)

    def test_loader_keeps_first_file_of_duplicate_key(self):
        with tempfile.TemporaryDirectory() as folder:
            self._write_note_file(folder, "first.html")
            self._write_note_file(folder, "second.html", title="Same key again")
            with self.assertLogs(LOADER_LOGGER, level="ERROR") as captured:
                created_notes = self.Note._load_release_notes(path=folder)
        self.assertEqual(created_notes.name, "Test note", "The first file of a key is the one loaded")
        self.assertEqual(self.Note.search_count([("key", "=", "PROJ-T1")]), 1)
        self.assertEqual(len(captured.output), 1)
        self.assertIn("second.html", captured.output[0])
        self.assertIn("first.html", captured.output[0])

    def test_body_keeps_button_link(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "button.html"), "w", encoding="utf-8") as note_file:
                note_file.write(
                    "---\nkey: PROJ-BUTTON\ntitle: Button note\n---\n"
                    '<p>Try it</p><a class="btn btn-primary" '
                    'href="/odoo/action-release_notes.release_note_action">Open</a>\n'
                )
            note = self.Note._load_release_notes(path=folder)
        self.assertEqual(note.key, "PROJ-BUTTON")
        self.assertIn('class="btn btn-primary"', note.body)
        self.assertIn('href="/odoo/action-release_notes.release_note_action"', note.body)

    def test_unify_buttons_on_load_create_and_write(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "buttons.html"), "w", encoding="utf-8") as note_file:
                note_file.write(
                    "---\nkey: PROJ-BUTTONS\ntitle: Buttons note\n---\n"
                    '<a class="btn btn-primary" href="/odoo/action-a.b">One</a>'
                    "<a class='btn btn-secondary' href=\"/odoo/action-c.d\">Two</a>"
                    '<a class="btn btn-outline-info btn-sm" href="/odoo/action-e.f">Three</a>'
                    '<p class="text-muted">Plain</p>'
                )
            loaded_note = self.Note._load_release_notes(path=folder)
        self.assertEqual(loaded_note.body.count('class="btn btn-primary"'), 3)
        self.assertNotIn("btn-secondary", loaded_note.body)
        self.assertNotIn("btn-outline-info", loaded_note.body)
        self.assertIn('class="text-muted"', loaded_note.body)
        created_note = self._create_note(body='<a class="btn btn-link" href="/odoo">Go</a>')
        self.assertIn('class="btn btn-primary"', created_note.body)
        created_note.with_user(self.manager).write({"body": '<a class="btn btn-secondary" href="/odoo">Go</a>'})
        self.assertIn('class="btn btn-primary"', created_note.body)
        self.assertNotIn("btn-secondary", created_note.body)

    def test_publish_by_deployer_notifies_once_without_email(self):
        note = self._create_note(deployer_id=self.manager.id, model_ids=[Command.set(self.partner_model.ids)])
        mail_count = self.env["mail.mail"].search_count([])
        note.with_user(self.manager).action_publish()
        self.assertTrue(note.is_published)
        self.assertTrue(note.is_notified)
        self.assertEqual(note.published_by_id, self.manager)
        self.assertTrue(note.published_date)
        self.assertEqual(len(self._odoobot_chat_messages(self.internal_user, note)), 1)
        self.assertEqual(self.env["mail.mail"].search_count([]), mail_count, "No email is queued")
        message = self._odoobot_chat_messages(self.internal_user, note)
        self.assertIn("<b>Release test</b>", message.body)
        self.assertIn('href="/odoo/release.note/%s"' % note.id, message.body)

        note.with_user(self.manager).action_unpublish()
        self.assertFalse(note.is_published)
        note.with_user(self.manager).action_publish()
        self.assertEqual(len(self._odoobot_chat_messages(self.internal_user, note)), 1, "Re-publish never re-sends")

    def test_chat_message_escapes_title(self):
        note = self._create_note(
            name="Fix <b>& more",
            deployer_id=self.manager.id,
            model_ids=[Command.set(self.partner_model.ids)],
        )
        note.with_user(self.manager).action_publish()
        channels = self.env["discuss.channel"].search(
            [
                ("channel_type", "=", "chat"),
                ("channel_partner_ids", "in", self.internal_user.partner_id.ids),
                ("channel_partner_ids", "in", self.odoobot.partner_id.ids),
            ]
        )
        message = channels.message_ids.filtered(
            lambda message: 'href="/odoo/release.note/%s"' % note.id in message.body
        )
        self.assertEqual(len(message), 1)
        self.assertIn("<b>Fix &lt;b&gt;&amp; more</b>", message.body)

    def test_publish_refused_without_models(self):
        note = self._create_note(deployer_id=self.manager.id)
        with self.assertRaisesRegex(UserError, "Set at least one model"):
            note.with_user(self.manager).action_publish()
        self.assertFalse(note.is_published)

    def test_publish_fields_not_writable_directly(self):
        note = self._create_note(deployer_id=self.manager.id, model_ids=[Command.set(self.partner_model.ids)])
        manager_note = note.with_user(self.manager)
        forged_values = {
            "is_published": True,
            "published_by_id": self.other_manager.id,
            "published_date": "2026-01-01 00:00:00",
            "is_notified": True,
        }
        for field_name, value in forged_values.items():
            with self.assertRaises(AccessError, msg=field_name):
                manager_note.write({field_name: value})
        with self.assertRaises(AccessError):
            self.Note.with_user(self.manager).create({"key": "PROJ-FORGED", "name": "Forged", "is_published": True})
        self.assertFalse(note.is_published or note.is_notified or note.published_by_id)

        manager_note.action_publish()
        self.assertTrue(note.is_published)
        self.assertEqual(note.published_by_id, self.manager)
        note.with_user(self.other_manager).action_unpublish()
        self.assertFalse(note.is_published)
        with self.assertRaises(AccessError):
            note.with_user(self.internal_user).action_unpublish()

    def test_manager_creates_note_from_form(self):
        with Form(self.Note.with_user(self.manager)) as note_form:
            note_form.name = "Form note"
            note_form.key = "PROJ-FORM"
            note_form.model_ids.add(self.partner_model)
        note = note_form.save()
        self.assertEqual(note.key, "PROJ-FORM")
        self.assertFalse(note.is_published or note.is_notified or note.published_by_id)
        direct_note = self.Note.with_user(self.manager).create(
            {"key": "PROJ-DIRECT", "name": "Direct note", "is_published": False}
        )
        self.assertFalse(direct_note.is_published)

    def test_publish_fields_refused_through_context_defaults(self):
        forged_defaults = {
            "default_is_published": True,
            "default_is_notified": True,
            "default_deployer_id": self.other_manager.id,
            "default_published_by_id": self.other_manager.id,
            "default_published_date": "2026-01-01 00:00:00",
        }
        for context_key, value in forged_defaults.items():
            with self.assertRaises(AccessError, msg=context_key):
                self.Note.with_user(self.manager).with_context(**{context_key: value}).create(
                    {"key": "PROJ-DEFAULT", "name": "Defaulted"}
                )
        self.assertFalse(self.Note.search([("key", "=", "PROJ-DEFAULT")]))

    def test_unchanged_publish_field_write_passes(self):
        note = self._create_note()
        manager_note = note.with_user(self.manager)
        manager_note.write({"is_published": False, "name": "Echoed"})
        self.assertEqual(note.name, "Echoed")
        with self.assertRaises(AccessError):
            manager_note.write({"is_published": True})
        self.assertFalse(note.is_published)

    def test_unchanged_publish_fields_echoed_on_published_note_pass(self):
        note = self._create_note(deployer_id=self.manager.id, model_ids=[Command.set(self.partner_model.ids)])
        manager_note = note.with_user(self.manager)
        manager_note.action_publish()
        published_date = note.published_date
        manager_note.write(
            {
                "is_published": True,
                "published_by_id": self.manager.id,
                "published_date": fields.Datetime.to_string(published_date),
                "name": "Echoed after publish",
            }
        )
        self.assertEqual(note.name, "Echoed after publish")
        self.assertEqual(note.published_by_id, self.manager)
        self.assertEqual(note.published_date, published_date)

    def test_publish_field_change_refused_on_any_record_of_batch(self):
        published_note = self._create_note(deployer_id=self.manager.id, model_ids=[Command.set(self.partner_model.ids)])
        published_note.with_user(self.manager).action_publish()
        unpublished_note = self._create_note(key="PROJ-TEST-2", name="Second note")
        with self.assertRaises(AccessError):
            (published_note | unpublished_note).with_user(self.manager).write({"is_published": True})
        self.assertFalse(unpublished_note.is_published)

    def test_key_cannot_change(self):
        note = self._create_note()
        with self.assertRaisesRegex(UserError, "key of a release note cannot be changed"):
            note.with_user(self.manager).write({"key": "PROJ-RENAMED"})
        note.with_user(self.manager).write({"key": "PROJ-TEST", "name": "Same key kept"})
        self.assertEqual(note.key, "PROJ-TEST")

    def test_deployer_must_be_manager(self):
        note = self._create_note()
        with self.assertRaisesRegex(UserError, "must be a Release Notes manager"):
            note.with_user(self.admin_user).write({"deployer_id": self.internal_user.id})
        with self.assertRaisesRegex(UserError, "must be a Release Notes manager"):
            self._create_note(key="PROJ-NONMGR", deployer_id=self.internal_user.id)
        self.assertNotIn(
            self.internal_user,
            self.env["res.users"].search(self.Note._fields["deployer_id"].get_comodel_domain(self.Note)),
        )

    def test_publish_refused_for_non_deployer_and_empty_deployer(self):
        note = self._create_note(deployer_id=self.manager.id)
        with self.assertRaisesRegex(UserError, "Only rn_manager"):
            note.with_user(self.other_manager).action_publish()
        with self.assertRaisesRegex(UserError, "Only rn_manager"):
            note.with_user(self.admin_user).action_publish()
        empty_note = self._create_note(key="PROJ-EMPTY")
        with self.assertRaisesRegex(UserError, "No deployer is set"):
            empty_note.with_user(self.admin_user).action_publish()
        self.assertFalse(note.is_published or empty_note.is_published)

    def test_recipients_need_read_access_to_a_note_model(self):
        cron_note = self._create_note(model_ids=[Command.set(self.cron_model.ids)])
        cron_recipients = cron_note._get_notification_recipients()
        self.assertIn(self.admin_user, cron_recipients)
        self.assertNotIn(self.internal_user, cron_recipients, "No read access to ir.cron")
        self.assertNotIn(self.inactive_admin, cron_recipients)
        self.assertNotIn(self.odoobot, cron_recipients)

        partner_note = self._create_note(key="PROJ-PARTNER", model_ids=[Command.set(self.partner_model.ids)])
        partner_recipients = partner_note._get_notification_recipients()
        self.assertIn(self.internal_user, partner_recipients)
        self.assertNotIn(self.portal_user, partner_recipients)

        self.assertFalse(self._create_note(key="PROJ-NOMODEL")._get_notification_recipients())

    def test_user_without_access_gets_no_message(self):
        note = self._create_note(deployer_id=self.manager.id, model_ids=[Command.set(self.cron_model.ids)])
        note.with_user(self.manager).action_publish()
        self.assertTrue(self._odoobot_chat_messages(self.admin_user, note))
        self.assertFalse(self._odoobot_chat_messages(self.internal_user, note))

    def test_unpublished_note_hidden_from_internal_user(self):
        hidden_note = self._create_note()
        visible_note = self._create_note(key="PROJ-VISIBLE", is_published=True)
        visible_to_user = self.Note.with_user(self.internal_user).search([("key", "in", ["PROJ-TEST", "PROJ-VISIBLE"])])
        self.assertEqual(visible_to_user, visible_note)
        visible_to_manager = self.Note.with_user(self.manager).search([("id", "in", (hidden_note | visible_note).ids)])
        self.assertEqual(len(visible_to_manager), 2)
        with self.assertRaises(AccessError):
            visible_note.with_user(self.internal_user).write({"name": "Changed"})
        visible_note.with_user(self.internal_user).read(["name", "body", "tag_ids", "deployer_id"])
        hidden_note.write({"model_ids": [Command.set(self.partner_model.ids)]})
        manager_read = hidden_note.with_user(self.manager).read(["model_ids"])
        self.assertEqual(manager_read[0]["model_ids"], self.partner_model.ids)
        self.assertEqual(hidden_note.with_user(self.manager).model_ids.display_name, "Contact")

    def test_deployer_write_rules(self):
        note = self._create_note()
        manager_note = note.with_user(self.manager)
        with self.assertRaises(AccessError):
            manager_note.write({"deployer_id": self.other_manager.id})
        manager_note.write({"deployer_id": self.manager.id})
        self.assertEqual(note.deployer_id, self.manager)
        with self.assertRaises(AccessError):
            manager_note.write({"deployer_id": self.manager.id})
        with self.assertRaises(AccessError):
            note.with_user(self.other_manager).write({"deployer_id": False})
        with self.assertRaises(AccessError):
            self.Note.with_user(self.manager).create(
                {"key": "PROJ-OTHER", "name": "Other", "deployer_id": self.other_manager.id}
            )
        self.Note.with_user(self.manager).create({"key": "PROJ-SELF", "name": "Self", "deployer_id": self.manager.id})
        note.with_user(self.admin_user).write({"deployer_id": self.other_manager.id})
        self.assertEqual(note.deployer_id, self.other_manager)
