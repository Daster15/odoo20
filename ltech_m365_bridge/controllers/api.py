"""REST API for the M365 middleware.

Auth: `Authorization: Bearer <Odoo API key>` of a user in group "M365 Bridge API".
The middleware only calls us (it is behind NAT) – Odoo never calls it.
"""
import functools
import json
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)
PREFIX = "/m365/v1"
MAX_BATCH = 500


def _error(status, msg):
    return request.make_json_response({"error": msg}, status=status)


def api_route(path, methods):
    def deco(fn):
        @http.route(PREFIX + path, type="http", auth="public", methods=methods, csrf=False, save_session=False)
        @functools.wraps(fn)
        def wrapper(self, **kw):
            header = request.httprequest.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return _error(401, "missing bearer token")
            uid = request.env["res.users.apikeys"].sudo()._check_credentials(scope="rpc", key=header[7:].strip())
            if not uid:
                return _error(401, "invalid API key")
            request.update_env(user=uid)
            if not request.env.user.has_group("ltech_m365_bridge.group_m365_api"):
                return _error(403, "user is not in group 'M365 Bridge API'")
            try:
                body = {}
                if request.httprequest.method == "POST":
                    body = json.loads(request.httprequest.get_data() or b"{}")
                with request.env.cr.savepoint():
                    return request.make_json_response(fn(self, body, **kw))
            except (ValueError, KeyError, TypeError) as e:
                return _error(400, str(e))
            except Exception as e:
                _logger.exception("M365 bridge API error on %s", path)
                return _error(500, f"{type(e).__name__}: {e}")
        return wrapper
    return deco


class M365BridgeAPI(http.Controller):

    @api_route("/ping", ["GET"])
    def ping(self, body):
        return {"ok": True, "user": request.env.user.login}

    # ---------- Odoo -> Outlook (time off) ----------

    @api_route("/outbox", ["GET"])
    def outbox(self, body, limit="200"):
        rows = request.env["m365.outbox"].sudo().search(
            [("state", "=", "pending")], limit=min(int(limit), MAX_BATCH), order="id")
        return {"items": [{"id": r.id, "model": r.model, "res_id": r.res_id, "event": r.event,
                           "payload": json.loads(r.payload) if r.payload else None} for r in rows]}

    @api_route("/outbox/ack", ["POST"])
    def outbox_ack(self, body):
        ids = [int(i) for i in body["ids"]][:MAX_BATCH]
        request.env["m365.outbox"].sudo().browse(ids).exists().write(
            {"state": "done", "done_date": fields.Datetime.now()})
        return {"acked": len(ids)}

    @api_route("/leave/<int:leave_id>", ["GET"])
    def leave(self, body, leave_id):
        leave = request.env["hr.leave"].sudo().with_context(active_test=False).browse(leave_id).exists()
        return {"leave": leave._m365_api_dict() if leave else None}

    @api_route("/leave/status", ["POST"])
    def leave_status(self, body):
        Link = request.env["m365.leave.link"].sudo()
        items = body["items"][:MAX_BATCH]
        existing = {l.leave_id: l for l in Link.search([("leave_id", "in", [int(i["leave_id"]) for i in items])])}
        now = fields.Datetime.now()
        for i in items:
            vals = {"upn": i.get("upn"), "event_id": i.get("event_id"), "state": i["state"],
                    "error": i.get("error"), "sync_date": now}
            if int(i["leave_id"]) in existing:
                existing[int(i["leave_id"])].write(vals)
            else:
                Link.create({**vals, "leave_id": int(i["leave_id"])})
        return {"saved": len(items)}

    # ---------- Outlook -> Odoo (calendar) ----------

    @api_route("/users", ["GET"])
    def users(self, body):
        users = request.env["res.users"].sudo().search([("m365_sync", "=", True), ("share", "=", False)])
        return {"items": [{"id": u.id, "upn": (u.email or u.login or "").strip().lower()} for u in users]}

    @api_route("/calendar/events", ["POST"])
    def calendar_events(self, body):
        items = body["items"]
        if len(items) > MAX_BATCH:
            raise ValueError(f"max {MAX_BATCH} items per call")
        return request.env["calendar.event"]._m365_upsert(body["upn"], items)
