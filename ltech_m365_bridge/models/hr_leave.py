from odoo import api, fields, models

from .m365_outbox import TRACKED_LEAVE_FIELDS


class HrLeave(models.Model):
    _inherit = "hr.leave"

    m365_sync_state = fields.Selection(
        [("synced", "In Outlook"), ("removed", "Not in Outlook"), ("error", "Error")],
        string="Outlook sync", compute="_compute_m365_status")
    m365_sync_error = fields.Text(string="Outlook sync error", compute="_compute_m365_status")

    def _compute_m365_status(self):
        links = {l.leave_id: l for l in self.env["m365.leave.link"].sudo().search([("leave_id", "in", self.ids)])}
        for leave in self:
            link = links.get(leave.id)
            leave.m365_sync_state = link.state if link else False
            leave.m365_sync_error = link.error if link else False

    @api.model_create_multi
    def create(self, vals_list):
        leaves = super().create(vals_list)
        self.env["m365.outbox"]._enqueue("hr.leave", leaves.ids)
        return leaves

    def write(self, vals):
        res = super().write(vals)
        if TRACKED_LEAVE_FIELDS & set(vals):
            self.env["m365.outbox"]._enqueue("hr.leave", self.ids)
        return res

    def unlink(self):
        ids = self.ids
        res = super().unlink()
        self.env["m365.outbox"]._enqueue("hr.leave", ids, event="deleted")
        return res

    def _m365_api_dict(self):
        """Everything the middleware needs, in one call.

        Keeps the Odoo 17 payload contract (holiday_status_id, request_unit_half/hours, active)
        so the middleware works unchanged. Odoo 20: leave type = work_entry_type_id,
        partial days = request_duration ('am'/'pm' half day, 'specific' hours), no `active` field.
        """
        self.ensure_one()
        emp = self.employee_id
        wet = self.work_entry_type_id
        return {
            "id": self.id,
            "state": self.state,
            "active": True,
            "employee_id": [emp.id, emp.name] if emp else False,
            "holiday_status_id": [wet.id, wet.name] if wet else False,
            "request_date_from": fields.Date.to_string(self.request_date_from),
            "request_date_to": fields.Date.to_string(self.request_date_to),
            "request_duration": self.request_duration,
            "request_unit_half": self.request_duration in ("am", "pm"),
            "request_unit_hours": self.request_duration == "specific",
            "date_from": fields.Datetime.to_string(self.date_from),
            "date_to": fields.Datetime.to_string(self.date_to),
            "write_date": fields.Datetime.to_string(self.write_date),
            "employee": {"name": emp.name, "work_email": emp.work_email, "tz": emp.tz} if emp else None,
        }
