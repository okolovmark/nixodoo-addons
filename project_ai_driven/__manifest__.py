{
    "name": "Project AI-driven",
    "summary": "Task attributes, a checklist, test cases, pull requests and sessions for AI-driven projects, a stage log of every task, the images of task texts as task attachments, a Markdown brief of every task",
    "version": "16.0.2.16.1",
    "category": "Services/Project",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["project"],
    "data": [
        "security/ir.model.access.csv",
        "data/mail_message_subtype_data.xml",
        "views/project_task_checklist_item_views.xml",
        "views/project_task_testcase_views.xml",
        "views/project_pull_request_views.xml",
        "views/ir_module_module_views.xml",
        "views/project_task_views.xml",
        "views/project_project_views.xml",
        "views/mail_message_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "project_ai_driven/static/src/css/project_ai_driven.css",
        ],
    },
    "pre_init_hook": "pre_init_hook",
    "post_init_hook": "post_init_hook",
}
