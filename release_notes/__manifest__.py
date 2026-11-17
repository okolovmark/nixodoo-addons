{
    "name": "Release Notes",
    "version": "20.0.2.0.0",
    "summary": "Release notes shown to users in Odoo, announced in the main menu on publish",
    "description": "Release notes loaded from the notes folder of a project module, published by "
    "their deployer and announced in the main menu of the users of the affected models.",
    "category": "Productivity",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "author": "Mark Okolov",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "security/release_notes_groups.xml",
        "security/ir.access.csv",
        "data/release_note_tag_data.xml",
        "views/release_note_tag_views.xml",
        "views/release_note_views.xml",
        "views/release_notes_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "release_notes/static/src/**/*",
        ],
        "web.assets_tests": [
            "release_notes/static/tests/tours/*.js",
        ],
    },
    "application": True,
}
