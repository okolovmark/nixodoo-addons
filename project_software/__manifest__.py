{
    "name": "Project Software",
    "summary": "Software task attributes, a checklist and test cases on project tasks",
    "version": "20.0.1.0.0",
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
            "project_software/static/src/css/project_software.css",
        ],
    },
}
