from datetime import timedelta

from odoo import Command, fields
from odoo.tests import HttpCase, new_test_user


class TestAnnouncementTour(HttpCase):
    def test_main_menu_announces_and_view_marks_seen(self):
        user = new_test_user(self.env, "rn_tour_user", groups="base.group_user", password="rn_tour_user")
        # older than the notes: a note is announced only to users created before it was published
        self.env.cr.execute(
            "UPDATE res_users SET create_date = create_date - interval '1 hour' WHERE id = %s", [user.id]
        )
        user.invalidate_recordset(["create_date"])
        self.env["release.note"].search([("is_published", "=", True)])._mark_seen_by(user)
        partner_model = self.env["ir.model"]._get("res.partner")
        now = fields.Datetime.now()
        notes = self.env["release.note"].create(
            [
                {
                    "key": "TOUR-OLD",
                    "name": "Tour note older",
                    "is_published": True,
                    "published_date": now,
                    "model_ids": [Command.set(partner_model.ids)],
                },
                {
                    "key": "TOUR-NEW",
                    "name": "Tour note newest",
                    "is_published": True,
                    "published_date": now + timedelta(seconds=1),
                    "model_ids": [Command.set(partner_model.ids)],
                },
            ]
        )
        self.start_tour("/odoo", "release_notes_announcement_tour", login="rn_tour_user")
        notes.invalidate_recordset(["seen_user_ids"])
        self.assertTrue(all(user in note.seen_user_ids for note in notes))
