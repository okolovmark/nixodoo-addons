{
    "name": "Project AI-driven",
    "summary": "Task attributes, a checklist, test cases and pull requests for AI-driven projects, a stage log of every task",
    "version": "20.0.2.2.0",
    "category": "Services/Project",
    "author": "Kaertech",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "security/ir.access.csv",
        "data/mail_message_subtype_data.xml",
        "views/project_task_subtask_views.xml",
        "views/project_task_testcase_views.xml",
        "views/project_pull_request_views.xml",
        "views/project_task_views.xml",
        "views/project_project_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "project_ai_driven/static/src/css/project_ai_driven.css",
        ],
    },
}
