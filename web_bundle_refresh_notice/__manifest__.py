{
    "name": "Web Bundle Refresh Notice",
    "summary": "Tabs opened before a module deploy are asked to refresh, as Odoo asks after a version upgrade",
    "version": "16.0.1.0.0",
    "category": "Hidden/Tools",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["bus", "web"],
    "assets": {
        "web.assets_backend": [
            "web_bundle_refresh_notice/static/src/**/*",
        ],
        "web.qunit_suite_tests": [
            "web_bundle_refresh_notice/static/tests/*_tests.js",
        ],
    },
}
