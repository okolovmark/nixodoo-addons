{
    "name": "Release Notes",
    "version": "16.0.1.0.0",
    "summary": "Release notes shown to users in Odoo, delivered by OdooBot on publish",
    "description": "Release notes loaded from the notes folder of a project module, published by "
    "their deployer and announced to the users of the affected models in an OdooBot chat.",
    "category": "Productivity",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "author": "Kaertech",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "security/release_notes_groups.xml",
        "security/ir.model.access.csv",
        "security/release_note_security.xml",
        "data/release_note_tag_data.xml",
        "views/release_note_tag_views.xml",
        "views/release_note_views.xml",
        "views/release_notes_menus.xml",
    ],
    "application": True,
}
