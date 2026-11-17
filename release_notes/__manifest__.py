{
    "name": "Release Notes",
    "version": "16.0.2.0.0",
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
        "security/ir.model.access.csv",
        "security/release_note_security.xml",
        "data/release_note_tag_data.xml",
        "views/release_note_tag_views.xml",
        "views/release_note_views.xml",
        "views/release_notes_menus.xml",
    ],
    "assets": {
        "web.assets_backend": [
            # right after Odoo's apps menu, so before any module that replaces it
            # (web_responsive): the replaced menu drops these extensions, it never
            # meets them with its own elements in place of Odoo's
            (
                "after",
                "web/static/src/webclient/navbar/navbar.xml",
                "release_notes/static/src/announcement/announcement.xml",
            ),
            "release_notes/static/src/**/*",
        ],
        "web.assets_tests": [
            "release_notes/static/tests/tours/*.js",
        ],
    },
    "application": True,
}
