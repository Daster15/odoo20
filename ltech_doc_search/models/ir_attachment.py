import logging

import requests

from odoo import api, fields, models

from . import ocr

_logger = logging.getLogger(__name__)

PROJECT_MODELS = ("project.project", "project.task")
FILE_TYPE_KEYS = ("pdf", "word", "excel", "powerpoint", "image", "email", "text", "other")


def _file_type(mimetype):
    m = (mimetype or "").lower()
    if m == "application/pdf":
        return "pdf"
    if m.startswith("image/"):
        return "image"
    if "word" in m or m.endswith("opendocument.text") or m == "application/rtf":
        return "word"
    if "sheet" in m or "excel" in m or m == "text/csv":
        return "excel"
    if "presentation" in m or "powerpoint" in m:
        return "powerpoint"
    if m in ("message/rfc822", "application/vnd.ms-outlook"):
        return "email"
    if m.startswith("text/"):
        return "text"
    return "other"


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    solr_state = fields.Selection(
        [("pending", "Pending"), ("done", "Indexed"), ("error", "Error")],
        default="pending", index=True, copy=False,
    )
    # word boxes of OCR'd images / scanned PDFs (see models/ocr.py), used to
    # draw highlight rectangles over the document preview
    ocr_layout = fields.Json(copy=False, prefetch=False)
    ocr_page_count = fields.Integer(copy=False)

    @api.model
    def _solr_cfg(self):
        icp = self.env["ir.config_parameter"].sudo()
        solr = icp.get_str("doc_search.solr_url", "http://solr:8983/solr/odoo_docs").rstrip("/")
        tika = icp.get_str("doc_search.tika_url", "http://tika:9998/tika")
        return solr, tika

    def _solr_related(self):
        """Return (project, record) the attachment belongs to."""
        self.ensure_one()
        if self.res_model == "project.project":
            project = self.env["project.project"].browse(self.res_id).exists()
            return project, project
        if self.res_model == "project.task":
            task = self.env["project.task"].browse(self.res_id).exists()
            return task.project_id, task
        return self.env["project.project"], None

    def _solr_doc(self, project, record, text):
        self.ensure_one()
        return {
            "id": str(self.id),
            "name": self.name or "",
            "content": text or "",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "record_name": record.display_name if record else "",
            "project_id": project.id,
            "company_id": self.company_id.id or 0,
            "mimetype": self.mimetype or "",
            "file_type": _file_type(self.mimetype),
            "create_date": self.create_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _tika(self, tika, raw, ocr_pdf=True):
        headers = {"Accept": "text/plain",
                   "Content-Type": self.mimetype or "application/octet-stream"}
        if not ocr_pdf:
            headers["X-Tika-PDFOcrStrategy"] = "no_ocr"  # we OCR scanned pages ourselves
        r = requests.put(tika, data=raw, timeout=300, headers=headers)
        r.raise_for_status()
        return r.text

    def _extract(self, tika):
        """Return (text, ocr_layout) of the attachment."""
        self.ensure_one()
        raw = self.raw
        raw = getattr(raw, "content", raw)  # Odoo 20: BinaryValue -> bytes
        if not raw:
            return "", None
        icp = self.env["ir.config_parameter"].sudo()
        langs = icp.get_str("doc_search.ocr_languages", "pol+eng+dan")
        max_pages = int(icp.get_str("doc_search.ocr_max_pages", "50") or 50)
        ftype = _file_type(self.mimetype)
        if ftype == "image" and (self.mimetype or "").lower() in ocr.OCR_IMAGE_MIMETYPES:
            try:
                text, layout = ocr.ocr_image(raw, langs)
                if layout:
                    return text, layout
            except Exception:
                _logger.warning("doc_search: OCR failed for image %s, falling back to Tika", self.id, exc_info=True)
        elif ftype == "pdf":
            try:
                ocr_text, layout = ocr.ocr_pdf(raw, langs, max_pages)
                text = self._tika(tika, raw, ocr_pdf=False)
                return "\n\n".join(t for t in (text, ocr_text) if t), layout
            except Exception:
                _logger.warning("doc_search: PDF OCR failed for %s, falling back to Tika", self.id, exc_info=True)
        return self._tika(tika, raw), None

    def _solr_index(self):
        solr, tika = self._solr_cfg()
        docs, done, failed = [], self.browse(), self.browse()
        for att in self:
            try:
                project, record = att._solr_related()
                if not project:  # e.g. private task without project
                    done |= att
                    continue
                text, layout = att._extract(tika)
                att.write({
                    "ocr_layout": layout or False,
                    "ocr_page_count": len(layout["pages"]) if layout else 0,
                })
                docs.append(att._solr_doc(project, record, text))
                done |= att
            except Exception:
                _logger.exception("doc_search: extraction failed for attachment %s", att.id)
                failed |= att
        if docs:
            try:
                requests.post(f"{solr}/update?commitWithin=3000", json=docs, timeout=120).raise_for_status()
            except Exception:
                _logger.exception("doc_search: Solr update failed")
                failed |= done
                done = self.browse()
        done.write({"solr_state": "done"})
        failed.write({"solr_state": "error"})

    @api.model
    def _cron_solr_index(self, limit=100):
        # OCR can be slow: index one attachment at a time and commit, so a long
        # run never loses work and stops cleanly before the cron time limit
        atts = self.sudo().search([
            ("solr_state", "=", "pending"),
            ("type", "=", "binary"),
            ("res_model", "in", PROJECT_MODELS),
        ], limit=limit, order="id desc")
        cron = self.env["ir.cron"]
        for i, att in enumerate(atts):
            att._solr_index()
            if cron._commit_progress(1, remaining=len(atts) - i - 1) < 120:
                self._solr_trigger()  # continue in a fresh run
                break

    @api.model
    def _solr_reindex_all(self):
        self.sudo().search([
            ("type", "=", "binary"), ("res_model", "in", PROJECT_MODELS),
        ]).write({"solr_state": "pending"})

    @api.model_create_multi
    def create(self, vals_list):
        atts = super().create(vals_list)
        if any(a.res_model in PROJECT_MODELS and a.type == "binary" for a in atts):
            self._solr_trigger()
        return atts

    @api.model
    def _solr_trigger(self):
        """Run the indexing cron as soon as possible (async, after commit)."""
        cron = self.env.ref("ltech_doc_search.cron_solr_index", raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    def write(self, vals):
        if vals.keys() & {"datas", "raw", "name", "res_model", "res_id"}:
            vals = dict(vals, solr_state="pending")
        return super().write(vals)

    def unlink(self):
        solr, _tika = self._solr_cfg()
        ids = [str(i) for i in self.filtered(lambda a: a.solr_state == "done").ids]
        res = super().unlink()
        if ids:
            try:
                requests.post(f"{solr}/update?commitWithin=10000", json={"delete": ids}, timeout=30)
            except Exception:
                _logger.warning("doc_search: could not delete %s from Solr", ids)
        return res
