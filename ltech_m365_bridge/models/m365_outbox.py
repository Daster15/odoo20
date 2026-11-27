import json
from datetime import timedelta

from odoo import api, fields, models

TRACKED_LEAVE_FIELDS = {
    "state", "employee_id", "work_entry_type_id",
    "request_date_from", "request_date_to", "request_duration",
    "request_date_from_period", "request_date_to_period", "request_hour_from", "request_hour_to",
    "date_from", "date_to",
}
SCAN_PARAM = "ltech_m365_bridge.last_leave_scan"


class M365Outbox(models.Model):
    """Changes waiting to be pulled by the middleware (it sits behind NAT, so Odoo never pushes)."""
    _name = "m365.outbox"
    _description = "M365 Bridge Outbox"
    _order = "id"

    model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    event = fields.Selection([("changed", "Changed"), ("deleted", "Deleted")], required=True, default="changed")
    payload = fields.Text()
    state = fields.Selection([("pending", "Pending"), ("done", "Picked up")], default="pending",
                             required=True, index=True)
    done_date = fields.Datetime()

    @api.model
    def _enqueue(self, model, ids, event="changed", payload=None):
        if not ids:
            return
        already = set(self.sudo().search([("model", "=", model), ("res_id", "in", list(ids)),
                                          ("event", "=", event), ("state", "=", "pending")]).mapped("res_id"))
        vals = [{"model": model, "res_id": rid, "event": event,
                 "payload": json.dumps(payload) if payload else False}
                for rid in ids if rid not in already]
        if vals:
            self.sudo().create(vals)

    @api.model
    def _cron_safety_scan(self):
        """Catch leave changes that bypassed write() (e.g. SQL, exotic flows). write_date is bumped by _write."""
        ICP = self.env["ir.config_parameter"].sudo()
        now = fields.Datetime.now()
        last = ICP.get_str(SCAN_PARAM)
        if last:
            since = fields.Datetime.from_string(last) - timedelta(minutes=10)
            leaves = self.env["hr.leave"].sudo().with_context(active_test=False).search([("write_date", ">=", since)])
            self._enqueue("hr.leave", leaves.ids)
        ICP.set_str(SCAN_PARAM, fields.Datetime.to_string(now))

    @api.model
    def _cron_purge(self):
        self.sudo().search([("state", "=", "done"),
                            ("done_date", "<", fields.Datetime.now() - timedelta(days=30))]).unlink()
