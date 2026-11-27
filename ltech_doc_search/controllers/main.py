import requests
from markupsafe import escape

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models import ocr
from ..models.ir_attachment import FILE_TYPE_KEYS, PROJECT_MODELS

HL = {
    "hl": "true",
    "hl.method": "unified",
    "hl.encoder": "html",          # escapes document text -> safe to render
    "hl.tag.pre": "<mark>",
    "hl.tag.post": "</mark>",
}


def _terms(field, values, tag=None):
    tag = f" tag={tag}" if tag else ""
    return "{!terms f=%s%s}%s" % (field, tag, ",".join(str(v) for v in values))


class DocSearchController(http.Controller):

    def _solr(self, params):
        solr, _tika = request.env["ir.attachment"]._solr_cfg()
        r = requests.post(f"{solr}/select", data=params, timeout=15)
        r.raise_for_status()
        return r.json()

    @http.route("/doc_search/query", type="jsonrpc", auth="user")
    def query(self, q="", project_ids=None, file_types=None, sort="score", page=0, limit=20):
        env = request.env
        empty = {"total": 0, "results": [], "facets": {"projects": [], "types": []}}
        q = (q or "").strip()
        readable = env["project.project"].search([]).ids
        if len(q) < 2 or not readable:
            return empty

        limit = max(1, min(int(limit), 50))
        page = max(0, int(page))
        fq = [_terms("project_id", readable)]
        if project_ids:
            fq.append(_terms("project_id", [int(p) for p in project_ids], "prj"))
        types = [t for t in (file_types or []) if t in FILE_TYPE_KEYS]
        if types:
            fq.append(_terms("file_type", types, "ft"))

        data = self._solr({
            **HL,
            "q": q,
            "defType": "edismax",
            "qf": "name^4 record_name^2 content",
            "mm": "100%",
            "fq": fq,
            "fl": "id,name,res_model,res_id,record_name,project_id,file_type,mimetype,create_date",
            "sort": "create_date desc" if sort == "date" else "score desc",
            "start": page * limit,
            "rows": limit,
            "hl.fl": "name,content",
            "hl.snippets": 3,
            "hl.fragsize": 180,
            "hl.maxAnalyzedChars": 1000000,
            "facet": "true",
            "facet.field": ["{!ex=prj}project_id", "{!ex=ft}file_type"],
            "facet.mincount": 1,
            "facet.limit": 50,
            "json.nl": "arrarr",
            "wt": "json",
        })

        docs = data["response"]["docs"]
        hl = data.get("highlighting", {})
        ff = data.get("facet_counts", {}).get("facet_fields", {})
        prj_facet = [(int(v), c) for v, c in ff.get("project_id", [])]

        # second safety net: ORM record rules on attachments / parent records
        allowed = {
            a["id"]: a["ocr_page_count"]
            for a in env["ir.attachment"].search_read(
                [("id", "in", [int(d["id"]) for d in docs])], ["ocr_page_count"])
        }
        pids = {p for p, _c in prj_facet} | {d.get("project_id") for d in docs}
        names = {p.id: p.display_name for p in env["project.project"].search([("id", "in", list(pids))])}

        results = []
        for d in docs:
            if int(d["id"]) not in allowed:
                continue
            h = hl.get(d["id"], {})
            results.append({
                "id": int(d["id"]),
                "name": d.get("name", ""),
                "name_html": (h.get("name") or [str(escape(d.get("name", "")))])[0],
                "snippets": h.get("content", []),
                "res_model": d.get("res_model"),
                "res_id": d.get("res_id"),
                "record_name": d.get("record_name", ""),
                "project_id": d.get("project_id"),
                "project_name": names.get(d.get("project_id"), ""),
                "file_type": d.get("file_type", "other"),
                "mimetype": d.get("mimetype", ""),
                "create_date": d.get("create_date"),
                "ocr_pages": allowed[int(d["id"])],
            })

        return {
            "total": data["response"]["numFound"],
            "results": results,
            "facets": {
                "projects": [{"id": p, "name": names[p], "count": c} for p, c in prj_facet if p in names],
                "types": [{"key": v, "count": c} for v, c in ff.get("file_type", [])],
            },
        }

    @http.route("/doc_search/upload", type="http", auth="user", methods=["POST"], csrf=True)
    def upload(self, res_model, res_id, ufile, **kw):
        try:
            if res_model not in PROJECT_MODELS:
                raise UserError(request.env._("Unsupported model: %s", res_model))
            record = request.env[res_model].browse(int(res_id)).exists()
            if not record:
                raise UserError(request.env._("The record does not exist."))
            record.check_access("read")
            att = request.env["ir.attachment"].create({
                "name": ufile.filename,
                "raw": ufile.read(),
                "res_model": res_model,
                "res_id": record.id,
            })
            # visible in the record's chatter, like a normal upload
            record.message_post(
                body=request.env._("Document added via Document Search."),
                attachment_ids=att.ids,
            )
            return request.make_json_response({"id": att.id, "name": att.name})
        except (AccessError, UserError) as e:
            return request.make_json_response({"error": str(e)}, status=400)

    @http.route("/doc_search/index_status", type="jsonrpc", auth="user")
    def index_status(self, ids):
        atts = request.env["ir.attachment"].search([("id", "in", [int(i) for i in ids])])
        return {a.id: a.solr_state for a in atts}

    @http.route("/doc_search/preview", type="jsonrpc", auth="user")
    def preview(self, attachment_id, q=""):
        att = request.env["ir.attachment"].search([("id", "=", int(attachment_id))])
        if not att:
            return {"html": ""}
        params = {
            **HL,
            "q": "*:*",
            "fq": f"id:{att.id}",
            "fl": "id,content",
            "rows": 1,
            "hl.fl": "content",
            "hl.bs.type": "WHOLE",       # whole document text, not fragments
            "hl.fragsize": 0,
            "hl.maxAnalyzedChars": 10000000,
        }
        if q:
            params.update({"hl.q": q, "hl.qparser": "edismax", "qf": "content"})
        data = self._solr(params)
        hl = data.get("highlighting", {}).get(str(att.id), {}).get("content")
        if hl:
            return {"html": hl[0]}
        docs = data["response"]["docs"]
        return {"html": str(escape(docs[0].get("content", ""))) if docs else ""}

    def _attachment(self, attachment_id):
        """Attachment readable by the current user (record rules apply)."""
        return request.env["ir.attachment"].search([("id", "=", int(attachment_id))])

    @http.route("/doc_search/boxes", type="jsonrpc", auth="user")
    def boxes(self, attachment_id, q=""):
        """Rectangles of the words matching `q` on OCR'd images / scanned PDF pages."""
        att = self._attachment(attachment_id)
        if not att or not att.ocr_page_count:
            return {"kind": None, "pages": [], "count": 0}
        return dict(ocr.match_boxes(att.ocr_layout, q), v=(att.checksum or "")[:10])

    @http.route("/doc_search/page/<int:attachment_id>/<int:page>", type="http", auth="user")
    def page_image(self, attachment_id, page, **kw):
        """One PDF page rendered as JPEG (preview of scanned PDFs)."""
        att = self._attachment(attachment_id)
        if not att or att.mimetype != "application/pdf" or not 1 <= page <= att.ocr_page_count:
            raise request.not_found()
        raw = att.raw
        raw = getattr(raw, "content", raw)
        try:
            data = ocr.render_pdf_page(raw, page)
        except Exception:
            raise request.not_found()
        return request.make_response(data, headers=[
            ("Content-Type", "image/jpeg"),
            ("Cache-Control", "private, max-age=86400"),
        ])
