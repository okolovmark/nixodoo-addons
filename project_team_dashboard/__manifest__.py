{
    "name": "Project Team Dashboard",
    "summary": "A Team Dashboard of workload cards, a multi-project Gantt chart with dependency lags and baselines, a Backlog list, and planned dates that roll up from sub-tasks",
    "version": "20.0.1.2.0",
    "category": "Services/Project",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["hr_timesheet", "project_ai_driven"],
    "data": [
        "security/ir.access.csv",
        "views/res_users_views.xml",
        "views/project_task_views.xml",
        "views/project_task_backlog_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "project_team_dashboard/static/src/**/*.js",
            "project_team_dashboard/static/src/**/*.xml",
            "project_team_dashboard/static/src/**/*.css",
        ],
        "web.assets_unit_tests": [
            "project_team_dashboard/static/tests/**/*.test.js",
        ],
    },
}
