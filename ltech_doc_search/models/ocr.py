"""OCR helpers: word bounding boxes for images and PDFs (Tesseract + Poppler).

Layout format stored on ir.attachment.ocr_layout::

    {"kind": "image" | "pdf",
     "pages": [{"w": <width>, "h": <height>,
                "words": [[x0, y0, x1, y1, "text"], ...]}, ...]}

Coordinates are fractions (0..1) of the page size, so the browser can place
highlight boxes in % regardless of the zoom / rendering resolution.
"""
import html
import io
import logging
import os
import re
import subprocess
import tempfile
import unicodedata

_logger = logging.getLogger(__name__)

OCR_IMAGE_MIMETYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/pjpeg", "image/tiff",
    "image/bmp", "image/gif", "image/webp",
}
OCR_DPI = 200            # rendering resolution of scanned PDF pages for Tesseract
PREVIEW_DPI = 130        # resolution of page images sent to the browser
MIN_TEXT_WORDS = 5       # PDF page with fewer text-layer words is treated as a scan
MAX_IMAGE_SIDE = 5000    # downscale huge photos before OCR
_ENV = dict(os.environ, OMP_THREAD_LIMIT="1")  # don't let Tesseract eat all cores


def _run(args, timeout):
    return subprocess.run(args, check=True, capture_output=True, timeout=timeout, env=_ENV).stdout


def _tesseract_tsv(png_path, langs):
    """Return (words, lines_text, width, height) for one image file."""
    out = _run(["tesseract", png_path, "-", "-l", langs, "tsv"], timeout=180).decode("utf-8", "replace")
    words, lines, width, height = [], {}, 1, 1
    for row in out.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12:
            continue
        level, left, top, w, h, conf, text = int(cols[0]), *map(int, cols[6:10]), float(cols[10]), cols[11]
        if level == 1:
            width, height = max(w, 1), max(h, 1)
        elif level == 5 and text.strip() and conf >= 0:
            words.append((left, top, left + w, top + h, text.strip()))
            lines.setdefault(tuple(cols[1:5]), []).append(text.strip())
    text = "\n".join(" ".join(ws) for ws in lines.values())
    return words, text, width, height


def _norm_words(words, width, height):
    return [[round(x0 / width, 5), round(y0 / height, 5), round(x1 / width, 5), round(y1 / height, 5), t]
            for x0, y0, x1, y1, t in words]


def ocr_image(raw, langs):
    """OCR an image. Returns (text, layout) or (None, None) when not applicable."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # browsers honour EXIF orientation, Tesseract doesn't
    if min(img.size) < 48:
        return None, None
    width, height = img.size
    if max(img.size) > MAX_IMAGE_SIDE:
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")
    with tempfile.TemporaryDirectory(prefix="ds_ocr_") as tmp:
        path = os.path.join(tmp, "img.png")
        img.save(path)
        words, text, w, h = _tesseract_tsv(path, langs)
    page = {"w": width, "h": height, "words": _norm_words(words, w, h)}
    return text, {"kind": "image", "pages": [page]}


_PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
_WORD_RE = re.compile(r'<word xMin="([\d.-]+)" yMin="([\d.-]+)" xMax="([\d.-]+)" yMax="([\d.-]+)">(.*?)</word>', re.S)


def ocr_pdf(raw, langs, max_pages):
    """Find PDF pages without a text layer and OCR them.

    Returns (ocr_text, layout). layout is None when every page already has text
    (the pdf.js viewer then highlights matches by itself).
    """
    with tempfile.TemporaryDirectory(prefix="ds_ocr_") as tmp:
        pdf = os.path.join(tmp, "doc.pdf")
        with open(pdf, "wb") as f:
            f.write(raw)
        bbox = _run(["pdftotext", "-bbox", pdf, "-"], timeout=300).decode("utf-8", "replace")
        pages = []
        for pw, ph, body in _PAGE_RE.findall(bbox):
            pw, ph = float(pw), float(ph)
            words = [(float(a), float(b), float(c), float(d), html.unescape(t).strip())
                     for a, b, c, d, t in _WORD_RE.findall(body)]
            words = [w for w in words if w[4]]
            pages.append({"w": pw, "h": ph, "words": _norm_words(words, pw, ph), "scan": len(words) < MIN_TEXT_WORDS})
        scanned = [i for i, p in enumerate(pages) if p["scan"]]
        if not scanned:
            return "", None
        texts = []
        for i in scanned[:max_pages]:
            base = os.path.join(tmp, f"p{i + 1}")
            _run(["pdftoppm", "-f", str(i + 1), "-l", str(i + 1), "-r", str(OCR_DPI),
                  "-gray", "-png", "-singlefile", pdf, base], timeout=120)
            words, text, w, h = _tesseract_tsv(base + ".png", langs)
            pages[i]["words"] = _norm_words(words, w, h)
            texts.append(text)
        if len(scanned) > max_pages:
            _logger.info("doc_search: OCR limited to %s of %s scanned pages", max_pages, len(scanned))
    for p in pages:
        p.pop("scan")
    return "\n\n".join(texts), {"kind": "pdf", "pages": pages}


def render_pdf_page(raw, page, dpi=PREVIEW_DPI):
    with tempfile.TemporaryDirectory(prefix="ds_pg_") as tmp:
        pdf = os.path.join(tmp, "doc.pdf")
        with open(pdf, "wb") as f:
            f.write(raw)
        base = os.path.join(tmp, "page")
        _run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi),
              "-jpeg", "-jpegopt", "quality=82", "-singlefile", pdf, base], timeout=120)
        with open(base + ".jpg", "rb") as f:
            return f.read()


# ------------------------------------------------------------------ matching

_FOLD = str.maketrans({"ł": "l", "ø": "o", "æ": "ae", "đ": "d", "ß": "ss", "œ": "oe"})
_TOKEN_RE = re.compile(r"\w+(?:[./,:-]\w+)*")


def fold(s):
    s = unicodedata.normalize("NFKD", s.lower().translate(_FOLD))
    return "".join(c for c in s if not unicodedata.combining(c))


def query_terms(q):
    """Positive search terms of a user query (edismax syntax is tolerated)."""
    q = re.sub(r'(^|\s)-("[^"]*"|\S+)', " ", q or "")       # drop negations
    q = re.sub(r"\b(AND|OR|NOT)\b", " ", q)                   # boolean operators
    q = re.sub(r"\b\w+:(?=\S)", " ", q)                        # field:value -> value
    terms = {fold(t) for t in _TOKEN_RE.findall(q)}
    return [t for t in terms if len(t) >= 2]


def _compact(s):
    return re.sub(r"\W+", "", s)


def word_matches(word, terms):
    w = fold(word).strip("\"'„”“«»()[]{}<>.,;:!?*")
    if not w:
        return False
    wc = _compact(w)
    for t in terms:
        if w.startswith(t) or (wc and wc.startswith(_compact(t)) and len(_compact(t)) >= 2):
            return True
        # light inflection tolerance: faktura / faktury, protokół / protokołu
        if len(t) >= 5 and len(w) >= len(t) - 2:
            n = 0
            for a, b in zip(w, t):
                if a != b:
                    break
                n += 1
            if n >= len(t) - 2:
                return True
    return False


def match_boxes(layout, q):
    terms = query_terms(q)
    pages, count = [], 0
    for p in (layout or {}).get("pages", []):
        boxes = [w[:4] for w in p.get("words", []) if terms and word_matches(w[4], terms)]
        count += len(boxes)
        pages.append({"w": p["w"], "h": p["h"], "boxes": boxes})
    return {"kind": (layout or {}).get("kind"), "pages": pages, "count": count}
