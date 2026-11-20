{
    "name": "Project Team Dashboard",
    "summary": "A Team Dashboard of workload cards, a multi-project Gantt chart with dependency lags and baselines, a Backlog list, and planned dates that roll up from sub-tasks",
    "version": "16.0.1.2.6",
    "category": "Services/Project",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["hr_timesheet", "project_ai_driven"],
    "data": [
        "security/ir.model.access.csv",
        "security/project_team_dashboard_security.xml",
        "views/res_users_views.xml",
        "views/project_task_views.xml",
        "views/project_task_backlog_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "project_team_dashboard/static/src/js/team_dashboard.js",
            "project_team_dashboard/static/src/css/team_dashboard.css",
            "project_team_dashboard/static/src/xml/team_dashboard.xml",
        ],
        "web.qunit_suite_tests": [
            "project_team_dashboard/static/tests/team_dashboard_tests.js",
        ],
    },
    "pre_init_hook": "pre_init_hook",
    "post_init_hook": "post_init_hook",
}
