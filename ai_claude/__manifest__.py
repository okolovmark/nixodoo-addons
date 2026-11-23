{
    "name": "AI Claude",
    "summary": "Answer an assistant's questions through the Claude Messages API, and review its AI conversations",
    "version": "16.0.1.0.0",
    "category": "Productivity",
    "author": "Mark Okolov",
    "website": "https://github.com/okolovmark/nixodoo-addons",
    "license": "LGPL-3",
    "depends": ["mail"],
    "external_dependencies": {"python": ["anthropic", "httpx2"]},
    "data": [
        "security/ir.model.access.csv",
        "views/ai_conversation_views.xml",
    ],
    "pre_init_hook": "pre_init_hook",
}
