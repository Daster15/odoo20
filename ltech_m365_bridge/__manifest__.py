{
    "name": "LTECH Microsoft 365 Bridge",
    "version": "20.0.1.0.0",
    "summary": "REST API + outbox for the M365 middleware (time off -> Outlook, Outlook -> Odoo calendar)",
    "author": "LTECH A/S",
    "license": "LGPL-3",
    "category": "Technical",
    "depends": ["hr_holidays", "calendar"],
    "data": [
        "security/security.xml",
        "security/ir.access.csv",
        "data/cron.xml",
        "views/views.xml",
    ],
    "installable": True,
}
