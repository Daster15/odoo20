{
    "name": "LTECH Document Search",
    "summary": "Full-text search of project documents (Solr + Tika) with highlighting",
    "version": "20.0.1.0.0",
    "category": "Project",
    "author": "LTECH A/S",
    "license": "LGPL-3",
    "depends": ["web", "project"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "data/ir_cron.xml",
        "views/doc_search_menu.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "ltech_doc_search/static/src/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
