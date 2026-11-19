{
    "name": "Project Support Request",
    "summary": "A Support item in the header that files requests in the project that develops this Odoo",
    "version": "20.0.1.1.0",
    "category": "Services/Project",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["project_ai_driven"],
    "data": [
        "security/project_task_security.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "project_support_request/static/src/**/*",
        ],
        "web.assets_tests": [
            "project_support_request/static/tests/tours/*.js",
        ],
        "web.assets_unit_tests": [
            "project_support_request/static/tests/**/*.test.js",
        ],
    },
    "uninstall_hook": "uninstall_hook",
}
