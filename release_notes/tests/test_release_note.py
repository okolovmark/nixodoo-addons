import importlib.util
import os
import tempfile
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.modules.module import get_resource_path
from odoo.tests import Form, new_test_user, tagged
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


@tagged("post_install", "-at_install")
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
        # created well before any note the tests publish: a note is announced only to users older than it
        users = (
            cls.manager | cls.other_manager | cls.internal_user | cls.portal_user | cls.inactive_admin | cls.admin_user
        )
        cls.env.cr.execute(
            "UPDATE res_users SET create_date = create_date - interval '1 hour' WHERE id IN %s", [tuple(users.ids)]
        )
        users.invalidate_recordset(["create_date"])
        # the notes a database already has are not the tests' business
        published_notes = cls.env["release.note"].search([("is_published", "=", True)])
        for user in users:
            published_notes._mark_seen_by(user)
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

    def _publish_note(self, **values):
        note_values = {
            "is_published": True,
            "published_date": fields.Datetime.now(),
            "model_ids": [Command.set(self.partner_model.ids)],
        }
        note_values.update(values)
        return self._create_note(**note_values)

    def _announcement(self, user):
        return self.Note.with_user(user).get_announcement()

    def _video_values(self, note, raw):
        return {
            "name": "PROJ_demo.mp4",
            "raw": raw,
            "mimetype": "video/mp4",
            "res_model": "release.note",
            "res_id": note.id,
        }

    def _odoobot_chat_messages(self, user, note):
        channels = self.env["mail.channel"].search(
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
        with tempfile.TemporaryDirectory() as module_folder:
            os.mkdir(os.path.join(module_folder, "notes"))
            self._write_note_file(os.path.join(module_folder, "notes"), "proj_t1.html")
            self._write_note_file(module_folder, "outside_notes.html", key="PROJ-OUTSIDE")
            with patch.object(release_note_module, "get_module_path", return_value=module_folder) as path_lookup:
                created_notes = self.Note._load_release_notes("proj_release_notes")
        path_lookup.assert_called_once_with("proj_release_notes")
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
                    'href="/web#action=release_notes.release_note_action">Open</a>\n'
                )
            note = self.Note._load_release_notes(path=folder)
        self.assertEqual(note.key, "PROJ-BUTTON")
        self.assertIn('class="btn btn-primary"', note.body)
        self.assertIn('href="/web#action=release_notes.release_note_action"', note.body)

    def test_unify_buttons_on_load_create_and_write(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "buttons.html"), "w", encoding="utf-8") as note_file:
                note_file.write(
                    "---\nkey: PROJ-BUTTONS\ntitle: Buttons note\n---\n"
                    '<a class="btn btn-primary" href="/web#action=a.b">One</a>'
                    "<a class='btn btn-secondary' href=\"/web#action=c.d\">Two</a>"
                    '<a class="btn btn-outline-info btn-sm" href="/web#action=e.f">Three</a>'
                    '<p class="text-muted">Plain</p>'
                )
            loaded_note = self.Note._load_release_notes(path=folder)
        self.assertEqual(loaded_note.body.count('class="btn btn-primary"'), 3)
        self.assertNotIn("btn-secondary", loaded_note.body)
        self.assertNotIn("btn-outline-info", loaded_note.body)
        self.assertIn('class="text-muted"', loaded_note.body)
        created_note = self._create_note(body='<a class="btn btn-link" href="/web">Go</a>')
        self.assertIn('class="btn btn-primary"', created_note.body)
        created_note.with_user(self.manager).write({"body": '<a class="btn btn-secondary" href="/web">Go</a>'})
        self.assertIn('class="btn btn-primary"', created_note.body)
        self.assertNotIn("btn-secondary", created_note.body)

    def test_publish_announces_without_chat_or_email(self):
        note = self._create_note(deployer_id=self.manager.id, model_ids=[Command.set(self.partner_model.ids)])
        mail_count = self.env["mail.mail"].search_count([])
        with patch.object(type(self.env["bus.bus"]), "_sendmany", autospec=True) as sendmany:
            note.with_user(self.manager).action_publish()
        self.assertTrue(note.is_published)
        self.assertTrue(note.is_notified)
        self.assertEqual(note.published_by_id, self.manager)
        self.assertTrue(note.published_date)
        self.assertFalse(self._odoobot_chat_messages(self.internal_user, note), "No OdooBot chat any more")
        self.assertEqual(self.env["mail.mail"].search_count([]), mail_count, "No email is queued")
        pushed = [(partner, kind) for partner, kind, _payload in sendmany.call_args.args[1]]
        self.assertIn((self.internal_user.partner_id, "release_notes/updated"), pushed)
        self.assertNotIn(self.portal_user.partner_id, [partner for partner, _kind in pushed])
        self.assertIn(note.id, self._announcement(self.internal_user)["note_ids"])
        self.assertNotIn(note.id, self._announcement(self.manager)["note_ids"], "The deployer read it already")
        self.assertIn(self.manager, note.seen_user_ids)

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
            self.env["res.users"].search(self.Note._fields["deployer_id"].get_domain_list(self.Note)),
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

    def test_announcement_needs_read_access_to_a_note_model(self):
        note = self._publish_note(model_ids=[Command.set(self.cron_model.ids)])
        self.assertIn(note.id, self._announcement(self.admin_user)["note_ids"])
        self.assertNotIn(note.id, self._announcement(self.internal_user)["note_ids"])
        portal_note = self._publish_note(key="PROJ-PORTAL")
        self.assertTrue(
            self.env["res.partner"].with_user(self.portal_user).check_access_rights("read", raise_exception=False),
            "The portal user can read the note's model",
        )
        self.assertNotIn(portal_note.id, self._announcement(self.portal_user)["note_ids"], "Only internal users")

    def test_announcement_lists_unseen_notes_newest_first(self):
        now = fields.Datetime.now()
        older = self._publish_note(key="PROJ-OLD", name="Older", published_date=now)
        newest = self._publish_note(key="PROJ-NEW", name="Newest", published_date=now + timedelta(seconds=1))
        self._publish_note(key="PROJ-NOMODEL", name="No model", model_ids=[])
        self._create_note(key="PROJ-DRAFT", model_ids=[Command.set(self.partner_model.ids)])
        announcement = self._announcement(self.internal_user)
        self.assertEqual(announcement["note_ids"], [newest.id, older.id])
        self.assertEqual(announcement["count"], 2)
        self.assertEqual(announcement["title"], "Newest")

    def test_opened_note_is_no_longer_announced(self):
        first = self._publish_note(key="PROJ-ONE")
        second = self._publish_note(key="PROJ-TWO")
        with patch.object(type(self.env["bus.bus"]), "_sendmany", autospec=True) as sendmany:
            first.with_user(self.internal_user).action_mark_seen()
        self.assertEqual(self._announcement(self.internal_user)["note_ids"], [second.id])
        self.assertEqual(sendmany.call_args.args[1], [(self.internal_user.partner_id, "release_notes/updated", {})])
        self.assertIn(self.internal_user, first.seen_user_ids)
        first.with_user(self.internal_user).action_mark_seen()
        self.assertEqual(first.seen_user_ids, self.internal_user, "Seen twice is one row")
        self.assertIn(first.id, self._announcement(self.admin_user)["note_ids"], "Seen by one user only")

    def test_seen_leaves_the_note_row_alone(self):
        note = self._publish_note()
        self.env.flush_all()
        # an UPDATE of the row, even one changing nothing, writes a new row version
        self.env.cr.execute("SELECT ctid FROM release_note WHERE id = %s", [note.id])
        row_version = self.env.cr.fetchone()[0]
        note.with_user(self.internal_user).action_mark_seen()
        self.env.flush_all()
        self.env.cr.execute("SELECT ctid FROM release_note WHERE id = %s", [note.id])
        self.assertEqual(self.env.cr.fetchone()[0], row_version)

    def test_skip_marks_the_shown_notes_seen(self):
        shown = self._publish_note(key="PROJ-ONE") | self._publish_note(key="PROJ-TWO")
        self.assertEqual(set(self._announcement(self.internal_user)["note_ids"]), set(shown.ids))
        published_since = self._publish_note(key="PROJ-LATER")
        self.Note.browse(shown.ids).with_user(self.internal_user).action_mark_seen()
        self.assertEqual(self._announcement(self.internal_user)["note_ids"], [published_since.id])
        self.assertTrue(all(self.internal_user in note.seen_user_ids for note in shown))
        self.assertEqual(self._announcement(self.admin_user)["count"], 3)

    def test_note_published_before_the_user_existed_is_not_announced(self):
        note = self._publish_note(published_date=self.internal_user.create_date - timedelta(days=1))
        self.assertNotIn(note.id, self._announcement(self.internal_user)["note_ids"])

    def test_unpublished_note_leaves_the_announcement(self):
        note = self._publish_note(deployer_id=self.manager.id)
        with patch.object(type(self.env["bus.bus"]), "_sendmany", autospec=True) as sendmany:
            note.with_user(self.manager).action_unpublish()
        self.assertEqual(self._announcement(self.internal_user)["count"], 0)
        self.assertIn(self.internal_user.partner_id, [partner for partner, _kind, _p in sendmany.call_args.args[1]])

    def test_hidden_note_cannot_be_marked_seen(self):
        note = self._create_note(model_ids=[Command.set(self.partner_model.ids)])
        with self.assertRaises(AccessError):
            note.with_user(self.internal_user).action_mark_seen()
        note.with_user(self.manager).action_mark_seen()
        self.assertFalse(note.seen_user_ids, "An unpublished note is never marked seen")

    def test_seen_users_hidden_from_internal_users(self):
        note = self._publish_note()
        with self.assertRaises(AccessError):
            note.with_user(self.internal_user).read(["seen_user_ids"])

    def test_key_links_the_ticket_for_users_who_can_read_it(self):
        note = self._publish_note(key="PROJ 7/1")

        def ticket_url(user):
            note.invalidate_recordset(["ticket_url"])
            return note.with_user(user).ticket_url

        params = self.env["ir.config_parameter"]
        params.set_param("release_notes.ticket_url", False)
        params.set_param("release_notes.ticket_model", False)
        self.assertFalse(ticket_url(self.internal_user), "No link without a URL template")
        params.set_param("release_notes.ticket_url", "/tickets/{key}")
        self.assertEqual(ticket_url(self.internal_user), "/tickets/PROJ%207%2F1")
        params.set_param("release_notes.ticket_model", "ir.cron")
        self.assertFalse(ticket_url(self.internal_user), "No read access to ir.cron")
        self.assertEqual(ticket_url(self.admin_user), "/tickets/PROJ%207%2F1")
        params.set_param("release_notes.ticket_model", "no.such.model")
        self.assertFalse(ticket_url(self.admin_user))

    def test_demo_is_the_newest_video_of_the_note(self):
        note = self._publish_note()
        self.assertFalse(note.demo_attachment_id)
        attachment_model = self.env["ir.attachment"]
        first = attachment_model.create(self._video_values(note, b"\x00"))
        note.invalidate_recordset(["demo_attachment_id"])
        self.assertEqual(note.demo_attachment_id, first)
        second = attachment_model.create(self._video_values(note, b"\x01"))
        attachment_model.create({"name": "notes.txt", "raw": b"text", "res_model": "release.note", "res_id": note.id})
        note.invalidate_recordset(["demo_attachment_id"])
        self.assertEqual(note.demo_attachment_id, second, "The newest video, not the newest attachment")
        user_note = note.with_user(self.internal_user)
        self.assertEqual(user_note.demo_attachment_id.read(["name"])[0]["name"], "PROJ_demo.mp4")

    def test_migration_counts_published_notes_as_seen(self):
        published = self._publish_note(is_notified=True)
        unpublished_after_its_chat = self._create_note(key="PROJ-UNPUBLISHED", is_notified=True)
        draft = self._create_note(key="PROJ-DRAFT")
        path = get_resource_path("release_notes", "migrations", "16.0.2.0.0", "post-migration.py")
        spec = importlib.util.spec_from_file_location("release_notes_post_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate(self.env.cr, "16.0.1.0.0")
        (published | unpublished_after_its_chat | draft).invalidate_recordset(["seen_user_ids"])
        self.assertIn(self.internal_user, published.seen_user_ids)
        self.assertIn(self.internal_user, unpublished_after_its_chat.seen_user_ids, "Its chat went out already")
        self.assertNotIn(self.portal_user, published.seen_user_ids)
        self.assertFalse(draft.seen_user_ids)
        self.assertEqual(self._announcement(self.internal_user)["count"], 0)

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
