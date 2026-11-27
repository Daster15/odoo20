from odoo import fields, models


class M365LeaveLink(models.Model):
    """Sync status reported back by the middleware (kept apart from hr.leave to avoid its write() logic)."""
    _name = "m365.leave.link"
    _description = "M365 Time Off Sync Status"
    _rec_name = "leave_id"

    leave_id = fields.Integer(required=True, index=True)  # plain int: survives leave deletion
    upn = fields.Char("Mailbox")
    event_id = fields.Char("Outlook event ID")
    state = fields.Selection([("synced", "In Outlook"), ("removed", "Not in Outlook"), ("error", "Error")])
    error = fields.Text()
    sync_date = fields.Datetime()

    _leave_uniq = models.Constraint("UNIQUE(leave_id)", "One status per time off.")
