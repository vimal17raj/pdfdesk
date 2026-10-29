import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import audit, auth, config, jobs, pdfops, store, users

app = FastAPI(title="PDFDesk")

U = Depends(auth.current_user)


async def _read(f: UploadFile) -> bytes:
    data = await f.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File larger than {config.MAX_UPLOAD_MB} MB")
    return data


def _doc(doc_id: str, user: dict) -> tuple[Path, dict]:
    """Path of the current version (pdfops opens it lazily from disk) + document info. Nothing is read into memory."""
    store.check_access(doc_id, user)
    return store.path(doc_id), store.info(doc_id)


def _commit(doc_id: str, user: dict, new: bytes, action: str, detail: str = "") -> dict:
    store.push(doc_id, new)
    audit.log(user["user"], action, doc_id, detail, user["ip"])
    return store.info(doc_id)


def _edit(doc_id: str, user: dict, fn, action: str, detail: str = "") -> dict:
    """Fast path for small edits: copy the version, apply fn(doc) to it, append only the changes."""
    store.check_access(doc_id, user)
    info = store.edit(doc_id, fn)
    audit.log(user["user"], action, doc_id, detail, user["ip"])
    return info


def _new_doc(user: dict, name: str, data: bytes, action: str, detail: str = "") -> dict:
    doc_id = store.create(user["user"], name, data)
    audit.log(user["user"], action, doc_id, detail, user["ip"])
    return {**store.info(doc_id), "pages": pdfops.validate(data), "size": len(data)}


def _file(data: bytes, name: str, media: str) -> Response:
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _pdf(data: bytes, name: str) -> Response:
    return _file(data, name if name.lower().endswith(".pdf") else name + ".pdf", "application/pdf")


def _stem(name: str) -> str:
    return Path(name).stem or "document"


# ---------- auth ----------
class Login(BaseModel):
    username: str
    password: str


users.ensure_admin()


@app.post("/api/auth/login")
def login(body: Login, request: Request):
    ip = auth.client_ip(request)
    try:
        res = auth.authenticate(body.username, body.password, ip)
    except HTTPException:
        audit.log(body.username[:64], "login_locked", ip=ip)
        raise
    if not res:
        audit.log(body.username[:64], "login_failed", ip=ip)
        raise HTTPException(401, "Invalid username or password")
    audit.log(res["username"], "login", ip=ip)
    return {"token": auth.make_token(res["username"], res["role"]), "user": res["username"], "role": res["role"]}


@app.get("/api/me")
def me(u: dict = U):
    return {"user": u["user"], "role": u["role"], "must_change": u["must_change"], "desktop": config.DESKTOP,
            "ocr": pdfops.ocr_available(),
            "ocr_langs": config.OCR_LANGS, "ai_summary": bool(config.ANTHROPIC_API_KEY), "stamps": pdfops.STAMPS}


class ChangePw(BaseModel):
    old: str
    new: str


@app.post("/api/me/password")
def change_password(b: ChangePw, u: dict = U):
    users.change_own_password(u["user"], b.old, b.new)
    audit.log(u["user"], "password_changed", ip=u["ip"])
    return {"ok": True}


# ---------- user management (admin) ----------
@app.get("/api/users/names")
def user_names(u: dict = U):
    return [x["username"] for x in users.list_all() if x["active"] and x["username"] != u["user"]]


@app.get("/api/users")
def list_users(u: dict = Depends(auth.admin_only)):
    return users.list_all()


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "user"


@app.post("/api/users")
def add_user(b: NewUser, u: dict = Depends(auth.admin_only)):
    rec = users.create(b.username, b.password, b.role)
    audit.log(u["user"], "user_created", detail=f"{rec['username']} ({rec['role']})", ip=u["ip"])
    return rec


class UserPatch(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@app.post("/api/users/{name}/update")
def update_user(name: str, b: UserPatch, u: dict = Depends(auth.admin_only)):
    rec = users.update(name.lower(), u["user"], b.role, b.active, b.password)
    what = [k for k, v in b.model_dump().items() if v is not None]
    audit.log(u["user"], "user_updated", detail=f"{name}: {', '.join(what)}", ip=u["ip"])
    return rec


@app.post("/api/users/{name}/delete")
def delete_user(name: str, u: dict = Depends(auth.admin_only)):
    users.delete(name.lower(), u["user"])
    audit.log(u["user"], "user_deleted", detail=name, ip=u["ip"])
    return {"ok": True}


# ---------- documents ----------
@app.post("/api/docs")
async def upload(file: UploadFile = File(...), password: str = Form(""), u: dict = U):
    data = await _read(file)
    if data.lstrip()[:5] != b"%PDF-":
        data = pdfops.images_to_pdf([data])
    else:
        data = pdfops.unlock(data, password)
    return _new_doc(u, file.filename or "document.pdf", data, "upload",
                    f"{file.filename} ({pdfops.validate(data)}p, {len(data)//1024} KB)")


@app.get("/api/docs")
def recent(u: dict = U):
    return store.list_for(u)


class ShareWith(BaseModel):
    user: str


@app.post("/api/docs/{doc_id}/share")
def share(doc_id: str, b: ShareWith, u: dict = U):
    if not users.get(b.user.strip().lower()):
        raise HTTPException(404, f"No user named '{b.user.strip()}'")
    shared = store.share(doc_id, u, b.user)
    audit.log(u["user"], "share", doc_id, f"with {b.user[:60]}", u["ip"])
    return {"shared": shared}


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str, u: dict = U):
    path, info = _doc(doc_id, u)
    return {**info, "size": path.stat().st_size, "pages": store.page_sizes(doc_id)}


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: str, u: dict = U):
    store.check_access(doc_id, u)
    if store.info(doc_id)["owner"] != u["user"] and u["role"] != "admin":
        raise HTTPException(403, "Only the owner can remove this document")
    store.delete(doc_id)
    audit.log(u["user"], "delete", doc_id, ip=u["ip"])
    return {"ok": True}


@app.get("/api/docs/{doc_id}/page/{n}.png")
def page_png(doc_id: str, n: int, zoom: float = 1.5, v: int = 0, u: dict = U):
    store.check_access(doc_id, u)
    with store.reading(doc_id) as doc:
        img = pdfops.render(doc, n, max(0.3, min(zoom, 4)))
    return Response(img, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/docs/{doc_id}/download")
def download(doc_id: str, u: dict = U):
    path, info = _doc(doc_id, u)
    audit.log(u["user"], "download", doc_id, info["name"], u["ip"])
    name = info["name"] if info["name"].lower().endswith(".pdf") else info["name"] + ".pdf"
    return FileResponse(path, media_type="application/pdf", filename=name)  # streamed from disk, not loaded into memory


@app.get("/api/docs/{doc_id}/page/{n}/lines")
def lines(doc_id: str, n: int, u: dict = U):
    store.check_access(doc_id, u)
    with store.reading(doc_id) as doc:
        return pdfops.text_lines(doc, n)


@app.get("/api/docs/{doc_id}/page/{n}/annots")
def annots(doc_id: str, n: int, u: dict = U):
    store.check_access(doc_id, u)
    with store.reading(doc_id) as doc:
        return pdfops.list_annots(doc, n)


@app.get("/api/docs/{doc_id}/comments")
def comments(doc_id: str, u: dict = U):
    store.check_access(doc_id, u)
    with store.reading(doc_id) as doc:
        return pdfops.all_comments(doc)


@app.get("/api/docs/{doc_id}/outline")
def outline(doc_id: str, u: dict = U):
    store.check_access(doc_id, u)
    with store.reading(doc_id) as doc:
        return pdfops.outline(doc)


@app.get("/api/docs/{doc_id}/search")
def search(doc_id: str, q: str, u: dict = U):
    store.check_access(doc_id, u)
    if not q.strip():
        return []
    with store.reading(doc_id) as doc:
        return pdfops.search_text(doc, q)


@app.post("/api/docs/{doc_id}/undo")
def undo(doc_id: str, u: dict = U):
    store.check_access(doc_id, u)
    return store.step(doc_id, -1)


@app.post("/api/docs/{doc_id}/redo")
def redo(doc_id: str, u: dict = U):
    store.check_access(doc_id, u)
    return store.step(doc_id, 1)


# ---------- export ----------
@app.get("/api/docs/{doc_id}/export")
def export(doc_id: str, fmt: str = "txt", dpi: int = 150, u: dict = U):
    data, info = _doc(doc_id, u)
    stem = _stem(info["name"])
    audit.log(u["user"], "export", doc_id, fmt, u["ip"])
    if fmt == "txt":
        return _file(pdfops.to_text(data).encode("utf-8"), f"{stem}.txt", "text/plain; charset=utf-8")
    if fmt == "html":
        return _file(pdfops.export_html(data).encode("utf-8"), f"{stem}.html", "text/html; charset=utf-8")
    if fmt == "docx":
        return _file(pdfops.export_docx(data), f"{stem}.docx",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    if fmt in ("png", "jpg"):
        return _file(pdfops.export_images(data, fmt, dpi, stem), f"{stem}_{fmt}.zip", "application/zip")
    raise HTTPException(400, "Unknown export format")


# ---------- create ----------
class CreateBlank(BaseModel):
    count: int = 1
    size: str = "a4"
    landscape: bool = False


@app.post("/api/create/blank")
def create_blank(b: CreateBlank, u: dict = U):
    return _new_doc(u, "blank.pdf", pdfops.create_blank(b.count, b.size, b.landscape), "create", "blank")


class CreateText(BaseModel):
    text: str
    size: str = "a4"
    fontsize: float = 12


@app.post("/api/create/text")
def create_text(b: CreateText, u: dict = U):
    if not b.text.strip():
        raise HTTPException(400, "Type some text first")
    return _new_doc(u, "document.pdf", pdfops.create_from_text(b.text, b.size, b.fontsize), "create", "from text")


@app.post("/api/create/images")
async def create_images(files: list[UploadFile] = File(...), u: dict = U):
    out = pdfops.images_to_pdf([await _read(f) for f in files])
    return _new_doc(u, "images.pdf", out, "create", f"{len(files)} image(s)")


# ---------- edit text / annotations ----------
class EditText(BaseModel):
    page: int
    bbox: list[float]
    text: str
    size: float = 11
    color: Any = 0


@app.post("/api/docs/{doc_id}/edit-text")
def edit_text(doc_id: str, b: EditText, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.edit_text(d, b.page, b.bbox, b.text, b.size, b.color),
                 "edit_text", f"page {b.page + 1}")


class Annot(BaseModel):
    page: int
    type: str
    rect: list[float] | None = None
    text: str = ""
    size: float = 12
    color: list[float] | None = None
    width: float = 2
    strokes: list[list[list[float]]] | None = None


@app.post("/api/docs/{doc_id}/annotate")
def annotate(doc_id: str, b: Annot, u: dict = U):
    spec = b.model_dump(exclude_none=True)
    return _edit(doc_id, u, lambda d: pdfops.annotate(d, b.page, spec, u["user"]),
                 "redact" if b.type == "redact" else "annotate", f"{b.type} page {b.page + 1}")


class DelAnnot(BaseModel):
    page: int
    xref: int


@app.post("/api/docs/{doc_id}/delete-annot")
def delete_annot(doc_id: str, b: DelAnnot, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.delete_annot(d, b.page, b.xref), "delete_annot", f"page {b.page + 1}")


# ---------- organize ----------
class PagesOp(BaseModel):
    op: str
    pages: list[int] = []
    angle: int = 90
    order: list[int] | None = None


@app.post("/api/docs/{doc_id}/pages")
def pages(doc_id: str, b: PagesOp, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.pages_op(d, b.op, b.pages, b.angle, b.order), f"pages_{b.op}",
                 ",".join(str(p + 1) for p in b.pages))


class Extract(BaseModel):
    pages: list[int]


@app.post("/api/docs/{doc_id}/extract")
def extract(doc_id: str, b: Extract, u: dict = U):
    path, info = _doc(doc_id, u)
    audit.log(u["user"], "extract", doc_id, ",".join(str(p + 1) for p in b.pages), u["ip"])
    return _pdf(pdfops.extract(path, b.pages), f"{_stem(info['name'])}_extract.pdf")


class Split(BaseModel):
    ranges: str


@app.post("/api/docs/{doc_id}/split")
def split(doc_id: str, b: Split, u: dict = U):
    path, info = _doc(doc_id, u)
    groups = pdfops.parse_ranges(b.ranges, len(store.page_sizes(doc_id)))
    zipped = pdfops.split(path, groups, _stem(info["name"]))
    audit.log(u["user"], "split", doc_id, b.ranges, u["ip"])
    return _file(zipped, f"{_stem(info['name'])}_split.zip", "application/zip")


# ---------- combine ----------
@app.post("/api/merge")
async def merge(files: list[UploadFile] = File(...), u: dict = U):
    if len(files) < 2:
        raise HTTPException(400, "Select at least two PDFs")
    out = pdfops.merge([await _read(f) for f in files])
    return _new_doc(u, "merged.pdf", out, "merge", ", ".join(f.filename or "?" for f in files)[:300])


@app.post("/api/docs/{doc_id}/append")
async def append(doc_id: str, files: list[UploadFile] = File(...), u: dict = U):
    path, _ = _doc(doc_id, u)
    blobs = [path.read_bytes()] + [await _read(f) for f in files]
    return _commit(doc_id, u, pdfops.merge(blobs), "append", ", ".join(f.filename or "?" for f in files)[:300])


# ---------- OCR / compress / watermark ----------
def _job_result(doc_id: str, u: dict, tmp: Path, base_version: int, name: str, suffix: str, action: str, detail: str) -> dict:
    """Store a finished job's output. If the document was edited while the job ran, saving it over the top would
    lose those edits, so it becomes a separate document instead."""
    if store.commit_file(doc_id, tmp, base_version):
        audit.log(u["user"], action, doc_id, detail, u["ip"])
        return {"doc_id": doc_id, "new": False, "message": "Done"}
    stem = _stem(name)
    new_id = store.create_from_file(u["user"], f"{stem} ({suffix}).pdf", tmp)
    audit.log(u["user"], action, new_id, detail + " (saved as a separate document: original changed meanwhile)", u["ip"])
    return {"doc_id": new_id, "new": True,
            "message": f"The document was changed while this ran, so the result was saved as a new document: {stem} ({suffix}).pdf"}


class Ocr(BaseModel):
    langs: str | None = None
    force: bool = False
    deskew: bool = True
    pages: str | None = None  # e.g. "1-50" or "1,3,7-9"; empty = whole document


@app.post("/api/docs/{doc_id}/ocr")
def run_ocr(doc_id: str, b: Ocr, u: dict = U):
    """Starts OCR in the background and returns a job; poll /api/jobs/{id}."""
    path, info = _doc(doc_id, u)
    langs, pages = pdfops.check_ocr_args(b.langs, b.pages)
    base = info["version"]

    def worker(job, cancelled):
        tmp = config.TMP_DIR / f"{job['id']}.pdf"
        try:
            pdfops.ocr_file(path, tmp, langs, b.force, b.deskew, pages, cancelled, job["log"])
            return _job_result(doc_id, u, tmp, base, info["name"], "OCR", "ocr", f"langs={langs} pages={pages or 'all'}")
        finally:
            for f in (tmp, Path(job["log"])):
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass  # a lingering handle (Windows); harmless leftover in data/tmp

    return jobs.start("ocr", doc_id, u["user"], worker)


class Compress(BaseModel):
    level: str = "medium"


@app.post("/api/docs/{doc_id}/compress")
def compress(doc_id: str, b: Compress, u: dict = U):
    path, info = _doc(doc_id, u)
    pdfops.check_level(b.level)
    base = info["version"]

    def worker(job, cancelled):
        tmp = config.TMP_DIR / f"{job['id']}.pdf"
        try:
            before = path.stat().st_size
            pdfops.compress_file(path, tmp, b.level)
            after = tmp.stat().st_size
            if after >= before:  # nothing to gain: keep the original
                return {"doc_id": doc_id, "new": False, "before": before, "after": before, "message": "Already optimal"}
            res = _job_result(doc_id, u, tmp, base, info["name"], "compressed", "compress",
                              f"{b.level}: {before // 1024} KB -> {after // 1024} KB")
            return {**res, "before": before, "after": after}
        finally:
            tmp.unlink(missing_ok=True)

    return jobs.start("compress", doc_id, u["user"], worker)


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str, u: dict = U):
    return jobs.get(job_id, u)


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str, u: dict = U):
    jobs.cancel(job_id, u)
    return {"ok": True}


class Watermark(BaseModel):
    text: str


@app.post("/api/docs/{doc_id}/watermark")
def watermark(doc_id: str, b: Watermark, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.watermark(d, b.text[:60]), "watermark", b.text[:60])


# ---------- forms / signatures / stamps ----------
@app.get("/api/docs/{doc_id}/fields")
def fields(doc_id: str, u: dict = U):
    store.check_access(doc_id, u)
    with store.reading(doc_id) as doc:
        return pdfops.list_fields(doc)


class Fill(BaseModel):
    values: dict[str, Any]


@app.post("/api/docs/{doc_id}/fill")
def fill(doc_id: str, b: Fill, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.fill_fields(d, b.values), "fill_form", f"{len(b.values)} field(s)")


class AddField(BaseModel):
    page: int
    rect: list[float]
    type: str = "text"
    name: str = ""
    options: list[str] | None = None


@app.post("/api/docs/{doc_id}/add-field")
def add_field(doc_id: str, b: AddField, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.add_field(d, b.page, b.rect, b.type, b.name, b.options),
                 "add_field", f"{b.type} page {b.page + 1}")


class PlaceImage(BaseModel):
    page: int
    rect: list[float]
    image: str
    label: str = "Image"


@app.post("/api/docs/{doc_id}/sign")
def sign(doc_id: str, b: PlaceImage, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.place_image(d, b.page, b.rect, b.image, b.label, u["user"]),
                 "sign" if b.label == "Signature" else "place_image", f"{b.label} page {b.page + 1}")


class UpdateAnnot(BaseModel):
    page: int
    xref: int
    rect: list[float] | None = None
    text: str | None = None
    color: list[float] | None = None
    width: float | None = None
    size: float | None = None


@app.post("/api/docs/{doc_id}/update-annot")
def update_annot(doc_id: str, b: UpdateAnnot, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.update_annot(d, b.page, b.xref, b.rect, b.text, b.color, b.width, b.size),
                 "update_object", f"page {b.page + 1}")


class FieldRef(BaseModel):
    name: str
    rect: list[float] | None = None
    new_name: str | None = None


@app.post("/api/docs/{doc_id}/delete-field")
def delete_field(doc_id: str, b: FieldRef, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.delete_field(d, b.name), "delete_field", b.name[:60])


@app.post("/api/docs/{doc_id}/update-field")
def update_field(doc_id: str, b: FieldRef, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.update_field(d, b.name, b.rect, b.new_name), "update_field", b.name[:60])


class Stamp(BaseModel):
    pages: list[int]
    center: list[float]
    text: str
    color: list[float] = [0, 0.5, 0]
    size: float = 26
    date: bool = True


@app.post("/api/docs/{doc_id}/stamp")
def stamp(doc_id: str, b: Stamp, u: dict = U):
    return _edit(doc_id, u, lambda d: pdfops.stamp(d, b.pages, b.center, b.text, b.color, b.size, b.date, u["user"]),
                 "stamp", f"{b.text[:30]} on {len(b.pages)} page(s)")


# ---------- protect ----------
class Protect(BaseModel):
    user_password: str = ""
    owner_password: str = ""
    allow_print: bool = True
    allow_copy: bool = False
    allow_edit: bool = False


@app.post("/api/docs/{doc_id}/protect")
def protect(doc_id: str, b: Protect, u: dict = U):
    data, info = _doc(doc_id, u)
    out = pdfops.protect(data, b.user_password, b.owner_password, b.allow_print, b.allow_copy, b.allow_edit)
    audit.log(u["user"], "protect", doc_id, "password-protected copy downloaded", u["ip"])
    return _pdf(out, f"{_stem(info['name'])}_protected.pdf")


# ---------- compare / summary ----------
@app.post("/api/compare")
async def compare(a: UploadFile = File(...), b: UploadFile = File(...), pw_a: str = Form(""), pw_b: str = Form(""),
                  u: dict = U):
    da = pdfops.unlock(await _read(a), pw_a)
    db = pdfops.unlock(await _read(b), pw_b)
    res = pdfops.compare(da, db)
    audit.log(u["user"], "compare", "", f"{a.filename} vs {b.filename}: +{res['added']} -{res['removed']}", u["ip"])
    return res


class Summary(BaseModel):
    length: str = "medium"


@app.post("/api/docs/{doc_id}/summary")
def summary(doc_id: str, b: Summary, u: dict = U):
    data, _ = _doc(doc_id, u)
    res = pdfops.summarize(data, b.length)
    audit.log(u["user"], "summary", doc_id, res["engine"], u["ip"])
    return res


# ---------- audit ----------
@app.get("/api/audit")
def get_audit(limit: int = 500, user: str = "", action: str = "", u: dict = Depends(auth.admin_only)):
    return audit.query(min(limit, 5000), user, action)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/ping")
def ping():
    """Desktop app: the open window pings every few seconds; when it stops, the program quits."""
    config.LAST_PING = time.time()
    return {"ok": True}


# ---------- frontend ----------
_dist = config.STATIC_DIR.resolve()
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = (_dist / path).resolve()
        if path and f.is_file() and _dist in f.parents:  # never serve anything outside the web folder
            return FileResponse(f)
        return FileResponse(_dist / "index.html")
