import base64
import difflib
import io
import re
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import pymupdf as fitz
from fastapi import HTTPException

from . import config


# ---------------------------------------------------------------- basics
def _open(data: bytes) -> fitz.Document:
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise HTTPException(400, "Not a valid PDF file")
    if doc.needs_pass:
        raise HTTPException(400, "PASSWORD_REQUIRED")
    return doc


def _out(doc: fitz.Document, **kw) -> bytes:
    # garbage=1 drops unused objects but keeps object numbers stable, so a selected annotation keeps its id across edits
    return doc.tobytes(garbage=1, deflate=True, **kw)


def _page(doc: fitz.Document, n: int) -> fitz.Page:
    if not 0 <= n < doc.page_count:
        raise HTTPException(404, "No such page")
    return doc[n]


# Pages can carry a /Rotate flag. The browser works in the rotated ("visual") space,
# PDF content lives in the unrotated space; these two helpers convert between them.
def _to_page(pg, r) -> fitz.Rect:
    return (fitz.Rect(r) * pg.derotation_matrix).normalize()


def _pt_to_page(pg, p) -> fitz.Point:
    return fitz.Point(p) * pg.derotation_matrix


def _to_view(pg, r) -> list:
    return list((fitz.Rect(r) * pg.rotation_matrix).normalize())


def unlock(data: bytes, password: str | None) -> bytes:
    """Return the PDF unchanged, or decrypted when it needs (and is given) a password."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise HTTPException(400, "Not a valid PDF file")
    if not doc.needs_pass:
        doc.close()
        return data
    if not password:
        raise HTTPException(400, "PASSWORD_REQUIRED")
    if not doc.authenticate(password):
        raise HTTPException(400, "Wrong password")
    out = doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE, garbage=3, deflate=True)
    doc.close()
    return out


def validate(data: bytes) -> int:
    d = _open(data)
    n = d.page_count
    d.close()
    return n


def render(data: bytes, page: int, zoom: float) -> bytes:
    doc = _open(data)
    pix = _page(doc, page).get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    out = pix.tobytes("png")
    doc.close()
    return out


def page_sizes(data: bytes) -> list[dict]:
    doc = _open(data)
    out = [{"w": p.rect.width, "h": p.rect.height, "rotation": p.rotation} for p in doc]
    doc.close()
    return out


def _rgb(c) -> tuple:
    if isinstance(c, (list, tuple)) and len(c) == 3:
        return tuple(max(0, min(1, float(x))) for x in c)
    if isinstance(c, int):
        return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)
    return (0, 0, 0)


# ---------------------------------------------------------------- text editing
def text_lines(data: bytes, page: int) -> list[dict]:
    """Editable text lines, bbox in visual PDF points (top-left origin)."""
    doc = _open(data)
    pg = _page(doc, page)
    lines = []
    for b in pg.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            lines.append(
                {
                    "bbox": _to_view(pg, ln["bbox"]),
                    "text": "".join(s["text"] for s in ln["spans"]),
                    "size": round(spans[0]["size"], 1),
                    "color": spans[0]["color"],
                    "font": spans[0]["font"],
                }
            )
    doc.close()
    return lines


def edit_text(data: bytes, page: int, bbox, new_text: str, size: float, color) -> bytes:
    """Replace the text inside bbox: blank the old line (images kept), write the new text in its place."""
    doc = _open(data)
    pg = _page(doc, page)
    r = _to_page(pg, bbox)
    pg.add_redact_annot(r, fill=(1, 1, 1))
    pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    wide = fitz.Rect(r.x0, r.y0 - 1, max(r.x1, pg.rect.x1 - 20), r.y1 + size * 0.4)
    rc = pg.insert_textbox(wide, new_text, fontsize=size, fontname="helv", color=_rgb(color))
    if rc < 0:  # didn't fit: shrink until it does
        for s in (size * 0.9, size * 0.8, size * 0.7, size * 0.6):
            if pg.insert_textbox(wide, new_text, fontsize=s, fontname="helv", color=_rgb(color)) >= 0:
                break
    return _out(doc)


# ---------------------------------------------------------------- annotations & comments
_HIDDEN_ANNOTS = {"Link", "Widget", "Popup"}


def annotate(data: bytes, page: int, a: dict, author: str = "") -> bytes:
    doc = _open(data)
    pg = _page(doc, page)
    kind = a["type"]
    color = _rgb(a.get("color", [1, 0.9, 0]))
    r = _to_page(pg, a["rect"]) if a.get("rect") else None
    ann = None
    if kind in ("text", "highlight", "underline", "strike", "rect", "ellipse", "redact", "note") and r is None:
        raise HTTPException(400, "rect required")
    if kind == "text":
        ann = pg.add_freetext_annot(
            r, a.get("text", ""), fontsize=a.get("size", 12), text_color=_rgb(a.get("color", [0, 0, 0])), fontname="helv"
        )
        ann.update()
    elif kind == "highlight":
        ann = pg.add_highlight_annot(r)
        ann.set_colors(stroke=color)
    elif kind == "underline":
        ann = pg.add_underline_annot(r)
    elif kind == "strike":
        ann = pg.add_strikeout_annot(r)
    elif kind in ("rect", "ellipse"):
        ann = pg.add_rect_annot(r) if kind == "rect" else pg.add_circle_annot(r)
        ann.set_colors(stroke=color)
        ann.set_border(width=a.get("width", 2))
        ann.update()
    elif kind == "ink":
        strokes = [[tuple(_pt_to_page(pg, (x, y))) for x, y in s] for s in a["strokes"]]
        ann = pg.add_ink_annot(strokes)
        ann.set_colors(stroke=color)
        ann.set_border(width=a.get("width", 2))
        ann.update()
    elif kind == "note":
        ann = pg.add_text_annot(r.tl, a.get("text", ""))
    elif kind == "redact":  # permanent: removes the text/pixels underneath
        pg.add_redact_annot(r, fill=(0, 0, 0))
        pg.apply_redactions()
    else:
        raise HTTPException(400, f"Unknown annotation type {kind}")
    if ann is not None and kind != "text":
        ann.set_info(title=author[:60], content=a.get("text", ""))
        ann.update()
    elif ann is not None:
        ann.set_info(title=author[:60])
    return _out(doc)


def _hex(c) -> str:
    if not c or len(c) != 3:
        return "#000000"
    return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in c)


def list_annots(data: bytes, page: int) -> list[dict]:
    doc = _open(data)
    pg = _page(doc, page)
    out = [
        {"xref": an.xref, "type": an.type[1], "rect": _to_view(pg, an.rect), "text": an.info.get("content", ""),
         "author": an.info.get("title", ""), "color": _hex(an.colors.get("stroke"))}
        for an in pg.annots() or []
        if an.type[1] not in _HIDDEN_ANNOTS
    ]
    doc.close()
    return out


def all_comments(data: bytes) -> list[dict]:
    doc = _open(data)
    out = []
    for i, pg in enumerate(doc):
        for an in pg.annots() or []:
            t = an.type[1]
            if t in _HIDDEN_ANNOTS:
                continue
            out.append({"page": i, "xref": an.xref, "type": t, "text": an.info.get("content", ""),
                        "author": an.info.get("title", ""), "rect": _to_view(pg, an.rect)})
    doc.close()
    return out


def delete_annot(data: bytes, page: int, xref: int) -> bytes:
    doc = _open(data)
    pg = _page(doc, page)
    for an in pg.annots() or []:
        if an.xref == xref:
            pg.delete_annot(an)
            return _out(doc)
    raise HTTPException(404, "Annotation not found")


# ---------------------------------------------------------------- page organisation
def pages_op(data: bytes, op: str, pages: list[int], angle: int = 90, order: list[int] | None = None) -> bytes:
    doc = _open(data)
    n = doc.page_count
    if any(not 0 <= p < n for p in pages) or (order and sorted(order) != list(range(n))):
        raise HTTPException(400, "Invalid page numbers")
    if op == "rotate":
        for p in pages:
            doc[p].set_rotation((doc[p].rotation + angle) % 360)
    elif op == "delete":
        if len(set(pages)) >= n:
            raise HTTPException(400, "Cannot delete all pages")
        doc.delete_pages(sorted(set(pages)))
    elif op == "reorder":
        doc.select(order)
    elif op == "reverse":
        doc.select(list(range(n))[::-1])
    elif op == "duplicate":
        for p in sorted(set(pages), reverse=True):
            doc.fullcopy_page(p, p + 1)
    elif op == "blank":
        for p in sorted(set(pages), reverse=True):
            ref = doc[p].rect
            doc.new_page(p + 1, width=ref.width, height=ref.height)
    else:
        raise HTTPException(400, "Unknown page operation")
    return _out(doc)


def extract(data: bytes, pages: list[int]) -> bytes:
    doc = _open(data)
    if not pages or any(not 0 <= p < doc.page_count for p in pages):
        raise HTTPException(400, "Invalid page numbers")
    doc.select(sorted(set(pages)))
    return _out(doc)


def merge(files: list[bytes]) -> bytes:
    out = fitz.open()
    for f in files:
        d = _open(f)
        out.insert_pdf(d)
        d.close()
    return _out(out)


def parse_ranges(spec: str, n: int) -> list[list[int]]:
    """'1-3,5,7-9' -> [[0,1,2],[4],[6,7,8]] (zero-based)."""
    groups = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = int(a), int(b) if b else n
            else:
                a = b = int(part)
        except ValueError:
            raise HTTPException(400, f"Bad range '{part}'")
        if not (1 <= a <= b <= n):
            raise HTTPException(400, f"Range '{part}' is outside 1-{n}")
        groups.append(list(range(a - 1, b)))
    if not groups:
        raise HTTPException(400, "No ranges given")
    return groups


def split(data: bytes, groups: list[list[int]], stem: str) -> bytes:
    """Returns a ZIP with one PDF per group."""
    src = _open(data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for i, g in enumerate(groups, 1):
            d = fitz.open()
            for p in g:
                d.insert_pdf(src, from_page=p, to_page=p)
            label = f"{g[0] + 1}" if len(g) == 1 else f"{g[0] + 1}-{g[-1] + 1}"
            z.writestr(f"{stem}_part{i}_p{label}.pdf", _out(d))
    return buf.getvalue()


# ---------------------------------------------------------------- compress / OCR
_LEVELS = {"low": (200, 150, 85), "medium": (150, 110, 70), "high": (120, 80, 50)}


def compress(data: bytes, level: str) -> bytes:
    if level not in _LEVELS:
        raise HTTPException(400, "level must be low, medium or high")
    doc = _open(data)
    thr, tgt, q = _LEVELS[level]
    try:
        doc.rewrite_images(dpi_threshold=thr, dpi_target=tgt, quality=q)
    except Exception:
        pass  # older PyMuPDF / odd images: structural compression below still applies
    out = doc.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True)
    doc.close()
    return out if len(out) < len(data) else data


def ocr_available() -> bool:
    return shutil.which("ocrmypdf") is not None and shutil.which("tesseract") is not None


def ocr(data: bytes, langs: str | None = None, force: bool = False, deskew: bool = True) -> bytes:
    """Scanned PDF -> searchable PDF using OCRmyPDF (Tesseract). Pages that already have text are skipped."""
    if not ocr_available():
        raise HTTPException(501, "OCR engine (ocrmypdf + tesseract) is not installed on the server")
    langs = (langs or config.OCR_LANGS).strip()
    if not all(c.isalnum() or c in "+_" for c in langs):
        raise HTTPException(400, "Bad language code")
    with tempfile.TemporaryDirectory() as t:
        src, dst = Path(t, "in.pdf"), Path(t, "out.pdf")
        src.write_bytes(data)
        cmd = ["ocrmypdf", "-l", langs, "--output-type", "pdf", "--optimize", "1"]
        cmd += ["--force-ocr"] if force else ["--skip-text"]
        if deskew:
            cmd += ["--deskew"]
        cmd += [str(src), str(dst)]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if p.returncode != 0 or not dst.exists():
            raise HTTPException(500, "OCR failed: " + (p.stderr or p.stdout)[-400:])
        return dst.read_bytes()


def outline(data: bytes) -> list[dict]:
    doc = _open(data)
    out = [{"level": lvl, "title": t, "page": max(0, p - 1)} for lvl, t, p in doc.get_toc(simple=True) if p > 0]
    doc.close()
    return out


# ---------------------------------------------------------------- search / export
def search_text(data: bytes, query: str) -> list[dict]:
    doc = _open(data)
    hits = []
    for i, pg in enumerate(doc):
        for r in pg.search_for(query):
            hits.append({"page": i, "rect": _to_view(pg, r)})
    doc.close()
    return hits


def to_text(data: bytes) -> str:
    doc = _open(data)
    t = "\n\f".join(p.get_text() for p in doc)
    doc.close()
    return t


def export_images(data: bytes, fmt: str, dpi: int, stem: str) -> bytes:
    if fmt not in ("png", "jpg"):
        raise HTTPException(400, "fmt must be png or jpg")
    doc = _open(data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for i, pg in enumerate(doc, 1):
            pix = pg.get_pixmap(dpi=max(50, min(dpi, 300)), alpha=False)
            z.writestr(f"{stem}_page{i:03d}.{fmt}", pix.tobytes("jpeg" if fmt == "jpg" else "png"))
    return buf.getvalue()


def export_html(data: bytes) -> str:
    doc = _open(data)
    body = "\n".join(f'<div class="page" id="p{i + 1}">{p.get_text("html")}</div>' for i, p in enumerate(doc))
    doc.close()
    return f'<!doctype html><meta charset="utf-8"><title>Exported PDF</title>{body}'


def export_docx(data: bytes) -> bytes:
    """Text and images per page into a Word file (layout is simplified, not pixel-exact)."""
    from docx import Document
    from docx.shared import Inches

    doc = _open(data)
    out = Document()
    for i, pg in enumerate(doc):
        if i:
            out.add_page_break()
        for b in pg.get_text("dict", sort=True)["blocks"]:
            if b.get("type") == 0:
                text = "\n".join("".join(s["text"] for s in ln["spans"]) for ln in b["lines"]).strip()
                if text:
                    out.add_paragraph(text)
            elif b.get("type") == 1 and b.get("image"):
                try:
                    out.add_picture(io.BytesIO(b["image"]), width=Inches(min(6, (b["bbox"][2] - b["bbox"][0]) / 72)))
                except Exception:
                    pass
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- create
_SIZES = {"a4": (595, 842), "letter": (612, 792), "legal": (612, 1008), "a3": (842, 1191)}


def create_blank(count: int, size: str, landscape: bool = False) -> bytes:
    w, h = _SIZES.get(size.lower(), _SIZES["a4"])
    if landscape:
        w, h = h, w
    d = fitz.open()
    for _ in range(max(1, min(count, 500))):
        d.new_page(width=w, height=h)
    return _out(d)


def create_from_text(text: str, size: str, fontsize: float) -> bytes:
    w, h = _SIZES.get(size.lower(), _SIZES["a4"])
    fontsize = max(6, min(fontsize, 48))
    margin, lead = 54, fontsize * 1.35
    maxw = w - 2 * margin
    lines = []
    for para in text.replace("\r", "").split("\n"):
        words, cur = para.split(" "), ""
        for wd in words:
            t = (cur + " " + wd).strip() if cur else wd
            if fitz.get_text_length(t, "helv", fontsize) <= maxw:
                cur = t
            else:
                lines.append(cur)
                cur = wd
        lines.append(cur)
    per = max(1, int((h - 2 * margin) // lead))
    d = fitz.open()
    for s in range(0, max(len(lines), 1), per):
        pg = d.new_page(width=w, height=h)
        y = margin + fontsize
        for ln in lines[s:s + per]:
            pg.insert_text((margin, y), ln, fontsize=fontsize, fontname="helv")
            y += lead
    return _out(d)


def images_to_pdf(images: list[bytes]) -> bytes:
    out = fitz.open()
    for im in images:
        try:
            pix = fitz.open(stream=im)
            pdf = fitz.open("pdf", pix.convert_to_pdf())
        except Exception:
            raise HTTPException(400, "Unsupported image file")
        out.insert_pdf(pdf)
    return _out(out)


# ---------------------------------------------------------------- forms
_WTYPES = {
    "text": fitz.PDF_WIDGET_TYPE_TEXT,
    "checkbox": fitz.PDF_WIDGET_TYPE_CHECKBOX,
    "dropdown": fitz.PDF_WIDGET_TYPE_COMBOBOX,
    "signature": fitz.PDF_WIDGET_TYPE_SIGNATURE,
}


def list_fields(data: bytes) -> list[dict]:
    doc = _open(data)
    out = []
    for i, pg in enumerate(doc):
        for w in pg.widgets() or []:
            kind = w.field_type_string
            val = w.field_value
            if kind == "CheckBox":
                val = val not in (False, "Off", "", None, 0)
            out.append({"page": i, "name": w.field_name, "type": kind, "rect": _to_view(pg, w.rect),
                        "value": val, "choices": list(w.choice_values or []), "readonly": bool(w.field_flags & 1)})
    doc.close()
    return out


def fill_fields(data: bytes, values: dict) -> bytes:
    doc = _open(data)
    done = 0
    for pg in doc:
        for w in pg.widgets() or []:
            if w.field_name in values and w.field_type_string != "Signature":
                v = values[w.field_name]
                w.field_value = bool(v) if w.field_type_string == "CheckBox" else str(v)
                w.update()
                done += 1
    if not done:
        raise HTTPException(400, "No matching form fields")
    return _out(doc)


def add_field(data: bytes, page: int, rect, kind: str, name: str, options: list[str] | None = None) -> bytes:
    if kind not in _WTYPES:
        raise HTTPException(400, "Unknown field type")
    doc = _open(data)
    pg = _page(doc, page)
    existing = {w.field_name for p in doc for w in (p.widgets() or [])}
    name = (name or kind).strip()[:60] or kind
    base, k = name, 2
    while name in existing:
        name, k = f"{base}_{k}", k + 1
    w = fitz.Widget()
    w.field_type = _WTYPES[kind]
    w.field_name = name
    w.rect = _to_page(pg, rect)
    w.border_color = (0.2, 0.3, 0.9)
    w.border_width = 1
    w.fill_color = (0.93, 0.95, 1)
    if kind == "text":
        w.text_fontsize = 0  # auto
        w.field_value = ""
    elif kind == "dropdown":
        w.choice_values = [o for o in (options or []) if o.strip()] or ["Option 1", "Option 2"]
        w.field_value = w.choice_values[0]
    pg.add_widget(w)
    return _out(doc)


# ---------------------------------------------------------------- e-signature / stamps
def _data_url(url: str) -> bytes:
    try:
        return base64.b64decode(url.split(",", 1)[1])
    except Exception:
        raise HTTPException(400, "Bad image data")


def place_image(data: bytes, page: int, rect, image_data_url: str, label: str = "Image", author: str = "") -> bytes:
    """Signatures / pasted pictures become Stamp annotations, so they can be selected, moved, resized and deleted."""
    doc = _open(data)
    pg = _page(doc, page)
    ann = pg.add_stamp_annot(_to_page(pg, rect), stamp=_data_url(image_data_url))
    ann.set_info(title=author[:60], content=label[:60])
    ann.update()
    return _out(doc)


STAMPS = {
    "APPROVED": (0, 0.5, 0), "REJECTED": (0.75, 0, 0), "DRAFT": (0.4, 0.4, 0.4), "CONFIDENTIAL": (0.75, 0, 0),
    "FOR REVIEW": (0, 0.3, 0.8), "PAID": (0, 0.5, 0), "RECEIVED": (0, 0.3, 0.8), "VOID": (0.75, 0, 0),
    "COPY": (0.4, 0.4, 0.4), "COMPLETED": (0, 0.5, 0),
}


def stamp(data: bytes, pages: list[int], center, text: str, color, size: float, with_date: bool, by: str) -> bytes:
    """Stamps are real Stamp annotations (an image of the stamp), so they can be moved, resized and deleted."""
    text = text.strip()[:40].upper() or "APPROVED"
    size = max(10, min(size, 60))
    col = _rgb(color)
    sub = f"{date.today().isoformat()}" + (f"  {by}" if by else "") if with_date else ""
    w = max(fitz.get_text_length(text, "hebo", size), fitz.get_text_length(sub, "helv", size * 0.45)) + size
    h = size * 1.5 + (size * 0.7 if sub else 0)
    tmp = fitz.open()
    tp = tmp.new_page(width=w, height=h)
    r = fitz.Rect(2, 2, w - 2, h - 2)
    tp.draw_rect(r, color=col, width=2.2, radius=0.12)
    tp.insert_textbox(fitz.Rect(r.x0, r.y0 + size * 0.15, r.x1, r.y0 + size * 1.5), text, fontsize=size,
                      fontname="hebo", color=col, align=1)
    if sub:
        tp.insert_textbox(fitz.Rect(r.x0, r.y0 + size * 1.45, r.x1, r.y1), sub, fontsize=size * 0.45,
                          fontname="helv", color=col, align=1)
    png = tp.get_pixmap(matrix=fitz.Matrix(4, 4), alpha=True).tobytes("png")
    doc = _open(data)
    for p in pages:
        pg = _page(doc, p)
        c = _pt_to_page(pg, center)
        ann = pg.add_stamp_annot(fitz.Rect(c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2), stamp=png)
        ann.set_info(title=by[:60], content=text)
        ann.update()
    return _out(doc)


# ---------------------------------------------------------------- edit existing objects
def _find_annot(pg, xref: int):
    for an in pg.annots() or []:
        if an.xref == xref:
            return an
    raise HTTPException(404, "Object not found")


def update_annot(data: bytes, page: int, xref: int, rect=None, text=None, color=None, width=None, size=None) -> bytes:
    doc = _open(data)
    pg = _page(doc, page)
    an = _find_annot(pg, xref)
    kind = an.type[1]
    if rect is not None:
        if kind not in ("Stamp", "FreeText", "Square", "Circle", "Text"):
            raise HTTPException(400, f"A {kind} object can't be moved or resized — delete it and add it again")
        an.set_rect(_to_page(pg, rect))
    if text is not None:
        an.set_info(content=text)
    if color is not None:
        c = _rgb(color)
        if kind == "FreeText":
            an.update(text_color=c)
        elif kind in ("Square", "Circle", "Ink", "Highlight", "Underline", "StrikeOut"):
            an.set_colors(stroke=c)
    if width is not None and kind in ("Square", "Circle", "Ink"):
        an.set_border(width=max(0.5, min(float(width), 20)))
    if kind == "FreeText" and size is not None:
        an.update(fontsize=max(5, min(float(size), 96)))
    else:
        an.update()
    return _out(doc)


def delete_field(data: bytes, name: str) -> bytes:
    doc = _open(data)
    for pg in doc:
        for w in pg.widgets() or []:
            if w.field_name == name:
                pg.delete_widget(w)
                return _out(doc)
    raise HTTPException(404, "Field not found")


def update_field(data: bytes, name: str, rect=None, new_name: str | None = None) -> bytes:
    doc = _open(data)
    for pg in doc:
        for w in pg.widgets() or []:
            if w.field_name == name:
                if rect is not None:
                    w.rect = _to_page(pg, rect)
                if new_name and new_name != name:
                    if any(x.field_name == new_name for p in doc for x in (p.widgets() or [])):
                        raise HTTPException(400, "A field with that name already exists")
                    w.update()
                    safe = new_name[:60].replace("\\", "").replace("(", "").replace(")", "")
                    parent = doc.xref_get_key(w.xref, "Parent")
                    target = int(parent[1].split()[0]) if parent[0] == "xref" else w.xref
                    doc.xref_set_key(target, "T", f"({safe})")
                    return _out(doc)
                w.update()
                return _out(doc)
    raise HTTPException(404, "Field not found")


def watermark(data: bytes, text: str) -> bytes:
    doc = _open(data)
    for pg in doc:
        r = pg.rect
        pg.insert_text(
            fitz.Point(r.width * 0.15, r.height * 0.55), text, fontsize=r.width / max(len(text), 6) * 1.6,
            fontname="helv", color=(0.8, 0.8, 0.8), overlay=True, fill_opacity=0.5,
        )
    return _out(doc)


# ---------------------------------------------------------------- protect
def protect(data: bytes, user_pw: str, owner_pw: str, allow_print: bool, allow_copy: bool, allow_edit: bool) -> bytes:
    if not user_pw and not owner_pw:
        raise HTTPException(400, "Enter an open password or an owner (permissions) password")
    perm = fitz.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perm |= fitz.PDF_PERM_PRINT | fitz.PDF_PERM_PRINT_HQ
    if allow_copy:
        perm |= fitz.PDF_PERM_COPY
    if allow_edit:
        perm |= fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ASSEMBLE
    doc = _open(data)
    out = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=user_pw or "",
                      owner_pw=owner_pw or secrets.token_hex(16), permissions=perm, garbage=3, deflate=True)
    doc.close()
    return out


# ---------------------------------------------------------------- compare
def _page_lines(doc: fitz.Document, i: int) -> list[str]:
    if i >= doc.page_count:
        return []
    return [re.sub(r"\s+", " ", ln).strip() for ln in doc[i].get_text().splitlines() if ln.strip()]


def compare(a: bytes, b: bytes) -> dict:
    da, db = _open(a), _open(b)
    changes, added, removed = [], 0, 0
    for i in range(max(da.page_count, db.page_count)):
        la, lb = _page_lines(da, i), _page_lines(db, i)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            changes.append({"page": i + 1, "removed": la[i1:i2], "added": lb[j1:j2]})
            removed += i2 - i1
            added += j2 - j1
    res = {"pages_a": da.page_count, "pages_b": db.page_count, "changes": changes[:2000],
           "added": added, "removed": removed, "identical": not changes,
           "no_text": not any(_page_lines(da, i) for i in range(da.page_count)) or
                      not any(_page_lines(db, i) for i in range(db.page_count))}
    da.close()
    db.close()
    return res


# ---------------------------------------------------------------- summary
_STOP = set("""a an the and or but if then else of to in on at by for with from as is are was were be been being it its this
that these those i you he she we they them his her our your their not no yes do does did have has had will would can could
should may might must also than so such into over under about after before between which who whom whose what when where why how
all any each more most other some very just only""".split())


def extractive_summary(text: str, sentences: int) -> list[str]:
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)  # PDF hard line breaks are not sentence breaks
    seen, sents = set(), []
    for s in re.split(r"(?<=[.!?])\s+|\n{2,}", re.sub(r"[ \t]+", " ", text)):
        s = s.strip()
        if len(s.split()) >= 5 and s.lower() not in seen:
            seen.add(s.lower())
            sents.append(s)
    if len(sents) <= sentences:
        return sents
    freq = Counter(w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.lower()) if w not in _STOP)
    top = max(freq.values(), default=1)
    scored = []
    for i, s in enumerate(sents):
        ws = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", s.lower()) if w not in _STOP]
        score = sum(freq[w] / top for w in ws) / (len(ws) ** 0.6 or 1)
        scored.append((score + (0.15 if i < 3 else 0), i))
    keep = sorted(i for _, i in sorted(scored, reverse=True)[:sentences])
    return [sents[i] for i in keep]


def llm_summary(text: str, length: str) -> str:
    import httpx

    words = {"short": "about 80 words", "medium": "about 200 words", "long": "about 450 words"}[length]
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
        json={"model": config.LLM_MODEL, "max_tokens": 1500,
              "messages": [{"role": "user", "content":
                            f"Summarise the following document in {words}. Start with one sentence on what the document is, "
                            f"then give the key points as short bullets (facts, dates, amounts, decisions, action items). "
                            f"Do not invent anything that is not in the text.\n\n<document>\n{text[:150000]}\n</document>"}]},
        timeout=120,
    )
    if r.status_code != 200:
        raise HTTPException(502, f"Summary service error ({r.status_code})")
    return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()


def summarize(data: bytes, length: str) -> dict:
    if length not in ("short", "medium", "long"):
        raise HTTPException(400, "length must be short, medium or long")
    text = to_text(data)
    if len(text.split()) < 20:
        raise HTTPException(400, "This PDF has no readable text. Run Scan & OCR first.")
    base = {"words": len(text.split())}
    if config.ANTHROPIC_API_KEY:
        try:
            return {**base, "engine": "claude", "summary": llm_summary(text, length)}
        except HTTPException:
            raise
        except Exception:
            pass  # network problem: fall back to the offline summary
    n = {"short": 3, "medium": 6, "long": 12}[length]
    return {**base, "engine": "extractive", "summary": "\n".join("• " + s for s in extractive_summary(text, n))}
