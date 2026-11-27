from datetime import datetime, timedelta

from odoo import api, fields, models
from odoo.tools import plaintext2html

SENSITIVITY = {"normal": "public", "personal": "private", "private": "private", "confidential": "confidential"}


class CalendarEvent(models.Model):
    _inherit = "calendar.event"

    m365_event_id = fields.Char("Outlook event ID", index=True, copy=False, readonly=True)
    m365_upn = fields.Char("Outlook mailbox", index=True, copy=False, readonly=True)

    _m365_uniq = models.Constraint("UNIQUE(m365_upn, m365_event_id)", "Outlook event already imported.")

    @api.model
    def _m365_upsert(self, upn, items):
        """Apply a batch of Outlook changes for one mailbox. Idempotent – the middleware may replay."""
        upn = (upn or "").strip().lower()
        user = self.env["res.users"].sudo().search(
            ["|", ("login", "=ilike", upn), ("email", "=ilike", upn)], limit=1)
        if not user:
            raise ValueError(f"No Odoo user for mailbox {upn}")
        # dont_notify: no invitations/alarms and (if microsoft_calendar is installed) no push back to Outlook
        Event = self.sudo().with_context(
            dont_notify=True, no_mail_to_attendees=True, mail_create_nolog=True,
            mail_create_nosubscribe=True, mail_notrack=True, tracking_disable=True, active_test=False)
        ids = [i["id"] for i in items]
        existing = {e.m365_event_id: e for e in Event.search([("m365_upn", "=", upn), ("m365_event_id", "in", ids)])}
        extra = {"need_sync_m": False} if "need_sync_m" in Event._fields else {}
        stats = {"created": 0, "updated": 0, "deleted": 0}
        for item in items:
            rec = existing.get(item["id"])
            if item.get("removed"):
                if rec:
                    rec.unlink()
                    stats["deleted"] += 1
                continue
            vals = self._m365_vals(item)
            if rec:
                rec.write({**vals, **extra})
                stats["updated"] += 1
            else:
                Event.create({**vals, **extra, "m365_event_id": item["id"], "m365_upn": upn,
                              "user_id": user.id, "partner_ids": [(6, 0, [user.partner_id.id])]})
                stats["created"] += 1
        return stats

    @api.model
    def _m365_vals(self, item):
        # same convention as microsoft_calendar._microsoft_to_odoo_values: naive UTC, all-day stop = end - 1 day
        if item.get("all_day"):
            start = datetime.fromisoformat(item["start"][:10])
            stop = datetime.fromisoformat(item["end"][:10]) - timedelta(days=1)
        else:
            start = datetime.fromisoformat(item["start"][:19])
            stop = datetime.fromisoformat(item["end"][:19])
        return {
            "name": item.get("subject") or "(no subject)",
            "allday": bool(item.get("all_day")),
            "start": start,
            "stop": max(stop, start),
            "show_as": "free" if item.get("show_as") == "free" else "busy",
            "privacy": SENSITIVITY.get(item.get("sensitivity"), "public"),
            "location": item.get("location") or False,
            "description": plaintext2html(item["body_preview"]) if item.get("body_preview") else False,
        }
