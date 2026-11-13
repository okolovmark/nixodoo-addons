{
    "name": "Project AI-driven",
    "summary": "Task attributes, a checklist and test cases on the tasks of AI-driven projects",
    "version": "20.0.2.0.0",
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
        "views/project_task_views.xml",
        "views/project_project_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "project_ai_driven/static/src/css/project_ai_driven.css",
        ],
    },
}
