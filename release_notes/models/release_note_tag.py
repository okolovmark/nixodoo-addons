from odoo import fields, models


class ReleaseNoteTag(models.Model):
    _name = "release.note.tag"
    _description = "Release Note Tag"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer()

    _sql_constraints = [
        ("name_unique", "unique(name)", "A release note tag with this name already exists."),
    ]
