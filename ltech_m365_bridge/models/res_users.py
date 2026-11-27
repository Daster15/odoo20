from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    m365_sync = fields.Boolean("Import Outlook calendar", help="The M365 middleware imports this user's "
                               "Outlook calendar into Odoo. Mailbox = user email (or login).")
