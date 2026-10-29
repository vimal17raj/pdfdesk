import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["PDFDESK_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)


def make_pdf(pages=3):
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 100), f"Hello page {i + 1}", fontsize=14)
    return d.tobytes()


def auth():
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


def wait_job(h, job, timeout=120):
    import time
    end = time.time() + timeout
    while time.time() < end:
        j = c.get(f"/api/jobs/{job['id']}", headers=h).json()
        if j["status"] != "running":
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_big_document():
    """500+ pages: edits must append (not rewrite), reads must reuse the open document, history must stay bounded."""
    import time
    h = auth()
    d = pymupdf.open()
    for k in range(600):
        d.new_page().insert_text((72, 100), f"Page number {k + 1} unique{k}", fontsize=12)
    data = d.tobytes(deflate=True)
    i = c.post("/api/docs", files={"file": ("big.pdf", data, "application/pdf")}, headers=h).json()["id"]

    t = time.time()
    doc = c.get(f"/api/docs/{i}", headers=h).json()
    assert len(doc["pages"]) == 600 and time.time() - t < 5
    assert c.get(f"/api/docs/{i}/page/599.png?zoom=1", headers=h).headers["content-type"] == "image/jpeg"

    hits = c.get(f"/api/docs/{i}/search?q=unique59", headers=h).json()
    assert {x["page"] for x in hits} >= {59, 590}
    assert all("unique59" in x["snippet"] for x in hits if x["page"] == 59), hits[:2]

    # an edit appends to a copy: the new version is only slightly larger than the old one, and is much faster than a rewrite
    t = time.time()
    r = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 300, "type": "rect", "rect": [50, 50, 200, 120]})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert time.time() - t < 5
    v1, v2 = (Path(os.environ["PDFDESK_DATA"]) / "docs" / i / f"v{n}.pdf" for n in (1, 2))
    assert v1.stat().st_size < v2.stat().st_size < v1.stat().st_size + 20_000
    assert [a["type"] for a in c.get(f"/api/docs/{i}/page/300/annots", headers=h).json()] == ["Square"]
    assert c.get(f"/api/docs/{i}/page/301/annots", headers=h).json() == []

    # undo/redo switch the shared open document too
    assert c.post(f"/api/docs/{i}/undo", headers=h).json()["version"] == 1
    assert c.get(f"/api/docs/{i}/page/300/annots", headers=h).json() == []
    assert c.post(f"/api/docs/{i}/redo", headers=h).json()["version"] == 2
    assert len(c.get(f"/api/docs/{i}/page/300/annots", headers=h).json()) == 1

    # page operations that restructure the file still work (full rewrite fallback)
    assert c.post(f"/api/docs/{i}/pages", headers=h, json={"op": "delete", "pages": [0, 1]}).status_code == 200
    assert len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 598

    # history is capped at 30 undo steps
    for k in range(35):
        assert c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 5, "type": "rect", "rect": [10, 10 + k, 60, 40 + k]}).status_code == 200
    files = sorted((Path(os.environ["PDFDESK_DATA"]) / "docs" / i).glob("v*.pdf"))
    assert len(files) <= 31

    # download streams the current version
    assert c.get(f"/api/docs/{i}/download", headers=h).content[:5] == b"%PDF-"


def _docdir(i):
    return Path(os.environ["PDFDESK_DATA"]) / "docs" / i


def test_streamed_upload_has_no_size_limit():
    import app.config as cfg
    h = auth()
    data = make_pdf(3)
    assert cfg.MAX_UPLOAD_MB == 0
    r = c.post("/api/docs/raw?name=raw.pdf", headers=h, content=data)  # the file itself is the request body
    assert r.status_code == 200 and r.json()["pages"] == 3 and r.json()["name"] == "raw.pdf"
    # multipart still works, and a configured limit is enforced while streaming
    assert c.post("/api/docs", headers=h, files={"file": ("m.pdf", data)}).status_code == 200
    cfg.MAX_UPLOAD_MB = 0.0001
    try:
        assert c.post("/api/docs/raw?name=x.pdf", headers=h, content=data).status_code == 413
    finally:
        cfg.MAX_UPLOAD_MB = 0
    assert not list((Path(os.environ["PDFDESK_DATA"]) / "tmp").glob("up_*.part"))  # no leftovers
    assert c.post("/api/docs/raw?name=bad.pdf", headers=h, content=b"%PDF-1.4 not really").status_code == 400
    # password-protected upload
    d = pymupdf.open(); d.new_page()
    enc = d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw1", owner_pw="own")
    assert c.post("/api/docs/raw?name=e.pdf", headers=h, content=enc).json()["detail"] == "PASSWORD_REQUIRED"
    assert c.post("/api/docs/raw?name=e.pdf", headers={**h, "X-Pdf-Password": "wrong"}, content=enc).status_code == 400
    assert c.post("/api/docs/raw?name=e.pdf", headers={**h, "X-Pdf-Password": "pw1"}, content=enc).status_code == 200


def test_append_mode_edits_in_place_with_cheap_undo():
    """Big files: one file, edits are appended to it, undo cuts them off again (no whole-file copies)."""
    import app.config as cfg
    h = auth()
    cfg.HUGE_MB = 0  # treat every file as "big" for this test
    try:
        i = c.post("/api/docs/raw?name=huge.pdf", headers=h, content=make_pdf(40)).json()["id"]
        d = _docdir(i)
        assert (d / "work.pdf").exists() and not (d / "v1.pdf").exists()
        size1 = (d / "work.pdf").stat().st_size
        assert c.get(f"/api/docs/{i}", headers=h).json()["huge"] is True

        r1 = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 3, "type": "rect", "rect": [50, 50, 200, 120]}).json()
        size2 = (d / "work.pdf").stat().st_size
        assert r1["version"] == 2 and size1 < size2 < size1 + 6000  # appended, not rewritten
        r2 = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 3, "type": "text", "rect": [50, 200, 220, 220], "text": "hello"}).json()
        size3 = (d / "work.pdf").stat().st_size
        assert r2["version"] == 3 and size3 > size2
        annots = lambda: [a["type"] for a in c.get(f"/api/docs/{i}/page/3/annots", headers=h).json()]
        assert annots() == ["Square", "FreeText"]
        assert len(list(d.glob("v*.pdf"))) == 0  # still just one file

        # undo cuts the last change off the file; the page shows the earlier state at once
        rev_before = r2["rev"]
        u1 = c.post(f"/api/docs/{i}/undo", headers=h).json()
        assert u1["version"] == 2 and (d / "work.pdf").stat().st_size == size2 and u1["rev"] != rev_before
        assert annots() == ["Square"]
        assert c.post(f"/api/docs/{i}/undo", headers=h).json()["version"] == 1
        assert (d / "work.pdf").stat().st_size == size1 and annots() == []
        assert c.post(f"/api/docs/{i}/undo", headers=h).status_code == 409
        assert c.get(f"/api/docs/{i}/download", headers=h).content == (d / "work.pdf").read_bytes()
        # redo puts the bytes back
        assert c.post(f"/api/docs/{i}/redo", headers=h).json()["version"] == 2
        assert c.post(f"/api/docs/{i}/redo", headers=h).json()["version"] == 3
        assert (d / "work.pdf").stat().st_size == size3 and annots() == ["Square", "FreeText"]
        assert c.post(f"/api/docs/{i}/redo", headers=h).status_code == 409

        # undo, then a *different* edit: the old redo is gone, and the page must not show stale cached content
        c.post(f"/api/docs/{i}/undo", headers=h)
        r3 = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 3, "type": "ellipse", "rect": [60, 300, 160, 360]}).json()
        assert r3["version"] == 3 and r3["rev"] not in (rev_before, u1["rev"])
        assert annots() == ["Square", "Circle"]
        assert c.post(f"/api/docs/{i}/redo", headers=h).status_code == 409
        assert not list(d.glob("tail_*.bin")) or all(f.name != "tail_3.bin" for f in d.glob("tail_*.bin"))

        # deleting pages is an appendable change too, and can be undone
        size_before = (d / "work.pdf").stat().st_size
        r4 = c.post(f"/api/docs/{i}/pages", headers=h, json={"op": "delete", "pages": [0, 1]}).json()
        assert r4["version"] == 4 and len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 38
        assert (d / "work.pdf").stat().st_size < size_before + 20_000
        assert c.get(f"/api/docs/{i}/search?q=Hello%20page%205", headers=h).json()
        assert c.post(f"/api/docs/{i}/undo", headers=h).json()["version"] == 3
        assert len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 40
        assert c.post(f"/api/docs/{i}/redo", headers=h).json()["version"] == 4
        assert len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 38
    finally:
        cfg.HUGE_MB = 500


def test_append_mode_rebuilds_a_file_that_cannot_be_appended_to():
    import app.config as cfg
    h = auth()
    cfg.HUGE_MB = 0
    try:
        broken = make_pdf(6).replace(b"startxref", b"startxxxx")  # damaged: MuPDF repairs it on open, so no appending
        i = c.post("/api/docs/raw?name=broken.pdf", headers=h, content=broken).json()["id"]
        r = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 1, "type": "rect", "rect": [10, 10, 60, 60]})
        assert r.status_code == 200 and r.json()["version"] == 1       # rebuilt: history starts over
        assert [a["type"] for a in c.get(f"/api/docs/{i}/page/1/annots", headers=h).json()] == ["Square"]
        r = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 2, "type": "rect", "rect": [10, 10, 60, 60]})
        assert r.json()["version"] == 2                                  # now clean, so edits append again
        assert c.post(f"/api/docs/{i}/undo", headers=h).json()["version"] == 1
    finally:
        cfg.HUGE_MB = 500


def test_bad_edit_leaves_big_file_untouched():
    import app.config as cfg
    h = auth()
    cfg.HUGE_MB = 0
    try:
        i = c.post("/api/docs/raw?name=h.pdf", headers=h, content=make_pdf(5)).json()["id"]
        f = _docdir(i) / "work.pdf"
        before = f.read_bytes()
        assert c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 99, "type": "rect", "rect": [1, 1, 5, 5]}).status_code == 404
        assert c.post(f"/api/docs/{i}/fill", headers=h, json={"values": {"nope": "x"}}).status_code == 400
        assert f.read_bytes() == before
        assert c.get(f"/api/docs/{i}", headers=h).json()["version"] == 1
    finally:
        cfg.HUGE_MB = 500


def test_desktop_opens_big_file_where_it_is(tmp_path):
    """Desktop app: a big PDF chosen in the Open dialog is used in place; the original is never modified."""
    import app.config as cfg
    from app import native
    src = tmp_path / "My Archive.pdf"
    src.write_bytes(make_pdf(30))
    original = src.read_bytes()
    cfg.DESKTOP, cfg.HUGE_MB = True, 0
    native.pick_pdf = lambda: str(src)
    try:
        r = c.post("/api/desktop/open", json={}).json()   # no Authorization header: desktop mode
        i = r["id"]
        assert r["pages"] == 30 and r["name"] == "My Archive.pdf" and r["linked"] is True
        assert not (_docdir(i) / "work.pdf").exists()      # nothing copied yet
        assert c.get(f"/api/docs/{i}/page/10.png?zoom=1").status_code == 200
        assert c.get(f"/api/docs/{i}/search?q=Hello%20page%2029").json()
        # the first edit makes a private working copy; the user's file stays byte-for-byte the same
        e = c.post(f"/api/docs/{i}/annotate", json={"page": 1, "type": "rect", "rect": [20, 20, 80, 80]}).json()
        assert e["version"] == 2 and e["linked"] is False and (_docdir(i) / "work.pdf").exists()
        assert src.read_bytes() == original
        assert c.post(f"/api/docs/{i}/undo").json()["version"] == 1
        assert src.read_bytes() == original
        # only paths the user picked can be opened by path
        assert c.post("/api/desktop/open", json={"path": str(tmp_path / "other.pdf")}).status_code == 403
        # a password-protected big file asks for the password, then opens a normal copy
        d = pymupdf.open(); d.new_page()
        enc = tmp_path / "enc.pdf"; enc.write_bytes(d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw1", owner_pw="own"))
        native.pick_pdf = lambda: str(enc)
        q = c.post("/api/desktop/open", json={}).json()
        assert q["password_required"] is True
        assert c.post("/api/desktop/open", json={"path": q["path"], "password": "pw1"}).json()["pages"] == 1
    finally:
        cfg.DESKTOP, cfg.HUGE_MB = False, 500
    assert c.post("/api/desktop/open", json={}).status_code == 401  # not available outside the desktop app


def test_job_cancel_and_stale():
    import time
    from app import jobs, pdfops
    h = auth()
    i = c.post("/api/docs", files={"file": ("t.pdf", make_pdf(2))}, headers=h).json()["id"]

    def slow(job, cancelled):
        while not cancelled():
            time.sleep(0.1)
        raise pdfops.Cancelled()

    job = jobs.start("test", i, "admin", slow)
    assert c.get(f"/api/jobs/{job['id']}", headers=h).json()["status"] == "running"
    assert c.post(f"/api/jobs/{job['id']}/cancel", headers=h).status_code == 200
    assert wait_job(h, job)["status"] == "cancelled"

    boom = jobs.start("test", i, "admin", lambda j, cancelled: (_ for _ in ()).throw(RuntimeError("bad")))
    assert wait_job(h, boom)["status"] == "error"
    assert c.get("/api/jobs/doesnotexist", headers=h).status_code == 404

    # a job whose heartbeat stopped (server restarted) is reported as failed, not "running" forever
    import json as _j
    f = Path(os.environ["PDFDESK_DATA"]) / "jobs" / f"{job['id']}.json"
    j = _j.loads(f.read_text()); j.update(status="running", updated=time.time() - 500); f.write_text(_j.dumps(j))
    assert c.get(f"/api/jobs/{job['id']}", headers=h).json()["status"] == "error"


def test_flow():
    assert c.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert c.get("/api/me").status_code == 401
    h = auth()
    r = c.post("/api/docs", files={"file": ("t.pdf", make_pdf(), "application/pdf")}, headers=h)
    assert r.status_code == 200 and r.json()["pages"] == 3
    i = r.json()["id"]

    ls = c.get(f"/api/docs/{i}/page/0/lines", headers=h).json()
    assert ls[0]["text"].startswith("Hello page 1")
    r = c.post(f"/api/docs/{i}/edit-text", headers=h,
               json={"page": 0, "bbox": ls[0]["bbox"], "text": "Changed text", "size": ls[0]["size"], "color": ls[0]["color"]})
    assert r.status_code == 200
    txt = c.get(f"/api/docs/{i}/export?fmt=txt", headers=h).text
    assert "Changed text" in txt and "Hello page 1" not in txt

    for t in ("highlight", "rect", "text", "redact"):
        r = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 1, "type": t, "rect": [60, 80, 200, 110], "text": "hi"})
        assert r.status_code == 200, (t, r.text)
    r = c.post(f"/api/docs/{i}/annotate", headers=h, json={"page": 1, "type": "ink", "strokes": [[[10, 10], [50, 50], [90, 20]]]})
    assert r.status_code == 200
    an = c.get(f"/api/docs/{i}/page/1/annots", headers=h).json()
    assert an
    assert c.post(f"/api/docs/{i}/delete-annot", headers=h, json={"page": 1, "xref": an[0]["xref"]}).status_code == 200

    assert c.get(f"/api/docs/{i}/search?q=Changed", headers=h).json()
    assert c.get(f"/api/docs/{i}/page/0.png?zoom=1", headers=h).status_code == 200

    pg = lambda **k: c.post(f"/api/docs/{i}/pages", headers=h, json=k)
    assert pg(op="rotate", pages=[0]).status_code == 200
    assert pg(op="duplicate", pages=[0]).status_code == 200
    assert len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 4
    assert pg(op="reorder", order=[3, 2, 1, 0]).status_code == 200
    assert pg(op="delete", pages=[0]).status_code == 200
    assert pg(op="delete", pages=[0, 1, 2]).status_code == 400
    assert len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 3

    assert c.post(f"/api/docs/{i}/undo", headers=h).json()["version"] > 0
    assert c.post(f"/api/docs/{i}/redo", headers=h).status_code == 200
    assert c.post(f"/api/docs/{i}/watermark", headers=h, json={"text": "DRAFT"}).status_code == 200
    r = wait_job(h, c.post(f"/api/docs/{i}/compress", headers=h, json={"level": "high"}).json())
    assert r["status"] == "done" and r["after"] <= r["before"], r
    assert c.post(f"/api/docs/{i}/compress", headers=h, json={"level": "nope"}).status_code == 400

    r = c.post(f"/api/docs/{i}/split", headers=h, json={"ranges": "1-2,3"})
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert len(z.namelist()) == 2
    assert c.post(f"/api/docs/{i}/split", headers=h, json={"ranges": "9"}).status_code == 400

    r = c.post("/api/merge", headers=h, files=[("files", ("a.pdf", make_pdf(2))), ("files", ("b.pdf", make_pdf(3)))])
    assert r.json()["pages"] == 5
    r = c.post(f"/api/docs/{i}/append", headers=h, files=[("files", ("a.pdf", make_pdf(2)))])
    assert r.status_code == 200
    assert len(c.get(f"/api/docs/{i}", headers=h).json()["pages"]) == 5

    # image -> pdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.set_rect(pix.irect, (200, 0, 0))
    r = c.post("/api/docs", headers=h, files={"file": ("scan.png", pix.tobytes("png"), "image/png")})
    assert r.status_code == 200 and r.json()["pages"] == 1

    assert c.post("/api/docs", headers=h, files={"file": ("x.pdf", b"garbage")}).status_code == 400
    r = c.post(f"/api/docs/{i}/ocr", headers=h, json={})
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        wait_job(h, r.json())
    assert c.post(f"/api/docs/{i}/ocr", headers=h, json={"pages": "1-x"}).status_code in (400, 501)

    actions = {a["action"] for a in c.get("/api/audit", headers=h).json()}
    assert {"upload", "edit_text", "annotate", "redact", "compress", "split", "merge", "login", "login_failed"} <= actions


LOREM = ("The quarterly report shows revenue growth in the northern region. " * 4 +
         "Operating costs were reduced by ten percent after the migration. " * 3 +
         "The board approved a new hiring plan for the support team in March. ")


def test_new_tools():
    h = auth()
    # create
    r = c.post("/api/create/text", headers=h, json={"text": LOREM * 20, "fontsize": 12})
    assert r.status_code == 200 and r.json()["pages"] >= 1
    i = r.json()["id"]
    assert c.post("/api/create/blank", headers=h, json={"count": 2, "size": "letter"}).json()["pages"] == 2

    # export
    assert c.get(f"/api/docs/{i}/export?fmt=docx", headers=h).content[:2] == b"PK"
    assert b"<div" in c.get(f"/api/docs/{i}/export?fmt=html", headers=h).content
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/docs/{i}/export?fmt=png&dpi=72", headers=h).content))
    assert z.namelist()[0].endswith(".png")

    # summary (offline engine)
    r = c.post(f"/api/docs/{i}/summary", headers=h, json={"length": "short"})
    assert r.status_code == 200 and r.json()["engine"] == "extractive" and r.json()["summary"]
    blank = c.post("/api/create/blank", headers=h, json={"count": 1}).json()["id"]
    assert c.post(f"/api/docs/{blank}/summary", headers=h, json={"length": "short"}).status_code == 400

    # forms: prepare -> list -> fill
    assert c.post(f"/api/docs/{blank}/add-field", headers=h,
                  json={"page": 0, "rect": [50, 50, 200, 70], "type": "text", "name": "Name"}).status_code == 200
    assert c.post(f"/api/docs/{blank}/add-field", headers=h,
                  json={"page": 0, "rect": [50, 90, 70, 110], "type": "checkbox", "name": "Agree"}).status_code == 200
    assert c.post(f"/api/docs/{blank}/add-field", headers=h,
                  json={"page": 0, "rect": [50, 130, 200, 150], "type": "dropdown", "name": "City",
                        "options": ["Chennai", "Delhi"]}).status_code == 200
    fl = c.get(f"/api/docs/{blank}/fields", headers=h).json()
    assert {f["name"] for f in fl} == {"Name", "Agree", "City"}
    r = c.post(f"/api/docs/{blank}/fill", headers=h, json={"values": {"Name": "Vimal", "Agree": True, "City": "Delhi"}})
    assert r.status_code == 200
    vals = {f["name"]: f["value"] for f in c.get(f"/api/docs/{blank}/fields", headers=h).json()}
    assert vals["Name"] == "Vimal" and vals["Agree"] is True and vals["City"] in ("Delhi", ["Delhi"])

    # signature image + stamp + comments
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
    pix.set_rect(pix.irect, (0, 0, 0))
    import base64
    url = "data:image/png;base64," + base64.b64encode(pix.tobytes("png")).decode()
    assert c.post(f"/api/docs/{blank}/sign", headers=h, json={"page": 0, "rect": [300, 300, 400, 350], "image": url}).status_code == 200
    assert c.post(f"/api/docs/{blank}/stamp", headers=h,
                  json={"pages": [0], "center": [300, 500], "text": "Approved", "date": True}).status_code == 200
    assert c.post(f"/api/docs/{blank}/annotate", headers=h,
                  json={"page": 0, "type": "note", "rect": [100, 400, 120, 420], "text": "check this"}).status_code == 200
    cm = c.get(f"/api/docs/{blank}/comments", headers=h).json()
    note = [x for x in cm if x["type"] == "Text"]
    assert note and note[0]["text"] == "check this" and note[0]["author"] == "admin"

    # stamps / signatures are real objects: list, move, recolor, edit text, delete
    an = {a["type"]: a for a in c.get(f"/api/docs/{blank}/page/0/annots", headers=h).json()}
    assert "Stamp" in an
    st = [a for a in c.get(f"/api/docs/{blank}/page/0/annots", headers=h).json() if a["type"] == "Stamp"]
    assert len(st) == 2 and {s["text"] for s in st} == {"APPROVED", "Image"}
    sx = st[0]["xref"]
    r = c.post(f"/api/docs/{blank}/update-annot", headers=h, json={"page": 0, "xref": sx, "rect": [10, 10, 110, 60]})
    assert r.status_code == 200
    moved = [a for a in c.get(f"/api/docs/{blank}/page/0/annots", headers=h).json() if a["xref"] == sx][0]
    assert [round(v) for v in moved["rect"]] == [10, 10, 110, 60]
    c.post(f"/api/docs/{blank}/annotate", headers=h, json={"page": 0, "type": "text", "rect": [50, 600, 200, 620], "text": "old"})
    c.post(f"/api/docs/{blank}/annotate", headers=h, json={"page": 0, "type": "rect", "rect": [50, 650, 150, 700]})
    al = c.get(f"/api/docs/{blank}/page/0/annots", headers=h).json()
    ft = [a for a in al if a["type"] == "FreeText"][0]
    assert c.post(f"/api/docs/{blank}/update-annot", headers=h, json={"page": 0, "xref": ft["xref"], "text": "new text", "color": [1, 0, 0], "size": 16}).status_code == 200
    assert [a for a in c.get(f"/api/docs/{blank}/page/0/annots", headers=h).json() if a["xref"] == ft["xref"]][0]["text"] == "new text"
    sq = [a for a in al if a["type"] == "Square"][0]
    assert c.post(f"/api/docs/{blank}/update-annot", headers=h, json={"page": 0, "xref": sq["xref"], "color": [0, 0, 1], "width": 4, "rect": [60, 660, 200, 720]}).status_code == 200
    assert c.post(f"/api/docs/{blank}/delete-annot", headers=h, json={"page": 0, "xref": sx}).status_code == 200
    assert sx not in [a["xref"] for a in c.get(f"/api/docs/{blank}/page/0/annots", headers=h).json()]
    # form fields: move / rename / delete
    assert c.post(f"/api/docs/{blank}/update-field", headers=h, json={"name": "Name", "rect": [60, 60, 260, 84], "new_name": "Full name"}).status_code == 200
    fl2 = {f["name"]: f for f in c.get(f"/api/docs/{blank}/fields", headers=h).json()}
    assert "Full name" in fl2 and [round(v) for v in fl2["Full name"]["rect"]] == [60, 60, 260, 84]
    assert c.post(f"/api/docs/{blank}/delete-field", headers=h, json={"name": "Agree"}).status_code == 200
    assert "Agree" not in {f["name"] for f in c.get(f"/api/docs/{blank}/fields", headers=h).json()}

    # rotated page: visual coordinates round-trip
    assert c.post(f"/api/docs/{i}/pages", headers=h, json={"op": "rotate", "pages": [0]}).status_code == 200
    ln = c.get(f"/api/docs/{i}/page/0/lines", headers=h).json()
    sz = c.get(f"/api/docs/{i}", headers=h).json()["pages"][0]
    assert all(0 <= l["bbox"][2] <= sz["w"] + 1 and 0 <= l["bbox"][3] <= sz["h"] + 1 for l in ln)

    # bookmarks
    d = pymupdf.open()
    d.new_page(); d.new_page()
    d.set_toc([[1, "Intro", 1], [2, "Details", 2]])
    bid = c.post("/api/docs", headers=h, files={"file": ("toc.pdf", d.tobytes())}).json()["id"]
    assert c.get(f"/api/docs/{bid}/outline", headers=h).json() == [
        {"level": 1, "title": "Intro", "page": 0}, {"level": 2, "title": "Details", "page": 1}]
    assert c.get(f"/api/docs/{i}/outline", headers=h).json() == []

    # organize: extract + reverse
    assert c.post(f"/api/docs/{i}/extract", headers=h, json={"pages": [0]}).content[:5] == b"%PDF-"
    assert c.post(f"/api/docs/{i}/pages", headers=h, json={"op": "reverse"}).status_code == 200

    # protect -> reopen with password
    r = c.post(f"/api/docs/{i}/protect", headers=h, json={"user_password": "s3cret", "allow_copy": False})
    assert r.status_code == 200
    enc = r.content
    assert c.post("/api/docs", headers=h, files={"file": ("p.pdf", enc)}).json()["detail"] == "PASSWORD_REQUIRED"
    assert c.post("/api/docs", headers=h, data={"password": "bad"}, files={"file": ("p.pdf", enc)}).status_code == 400
    r = c.post("/api/docs", headers=h, data={"password": "s3cret"}, files={"file": ("p.pdf", enc)})
    assert r.status_code == 200
    assert c.post(f"/api/docs/{i}/protect", headers=h, json={}).status_code == 400

    # compare
    a, b = make_pdf(2), None
    d = pymupdf.open()
    for k, t in enumerate(("Hello page 1", "Different words here")):
        d.new_page().insert_text((72, 100), t, fontsize=14)
    b = d.tobytes()
    r = c.post("/api/compare", headers=h, files={"a": ("a.pdf", a), "b": ("b.pdf", b)}).json()
    assert not r["identical"] and r["removed"] == 1 and r["added"] == 1 and r["changes"][0]["page"] == 2
    assert c.post("/api/compare", headers=h, files={"a": ("a.pdf", a), "b": ("b.pdf", a)}).json()["identical"]


def test_user_management():
    h = auth()
    me = c.get("/api/me", headers=h).json()
    assert me["role"] == "admin" and me["must_change"] is True
    assert c.post("/api/users", headers=h, json={"username": "Amy", "password": "short", "role": "user"}).status_code == 400
    assert c.post("/api/users", headers=h, json={"username": "bad name!", "password": "longenough1"}).status_code == 400
    assert c.post("/api/users", headers=h, json={"username": "amy", "password": "longenough1", "role": "root"}).status_code == 400
    assert c.post("/api/users", headers=h, json={"username": "Amy", "password": "longenough1", "role": "user"}).json()["username"] == "amy"
    assert c.post("/api/users", headers=h, json={"username": "amy", "password": "longenough1"}).status_code == 400
    ha = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "amy", "password": "longenough1"}).json()["token"]}
    assert c.get("/api/users", headers=ha).status_code == 403
    assert c.post("/api/users", headers=ha, json={"username": "x1", "password": "longenough1"}).status_code == 403
    assert "amy" not in c.get("/api/users/names", headers=ha).json() and "admin" in c.get("/api/users/names", headers=ha).json()

    # change own password
    assert c.post("/api/me/password", headers=ha, json={"old": "wrong", "new": "brandnew123"}).status_code == 400
    assert c.post("/api/me/password", headers=ha, json={"old": "longenough1", "new": "short"}).status_code == 400
    assert c.post("/api/me/password", headers=ha, json={"old": "longenough1", "new": "brandnew123"}).status_code == 200
    assert c.get("/api/me", headers=ha).json()["must_change"] is False
    assert c.post("/api/auth/login", json={"username": "amy", "password": "longenough1"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "amy", "password": "brandnew123"}).status_code == 200

    # admin resets password, promotes, disables
    assert c.post("/api/users/amy/update", headers=h, json={"password": "resetpass99"}).status_code == 200
    assert c.get("/api/me", headers=ha).json()["must_change"] is True
    assert c.post("/api/users/amy/update", headers=h, json={"role": "admin"}).json()["role"] == "admin"
    assert c.get("/api/users", headers=ha).status_code == 200  # promotion applies to the existing token
    assert c.post("/api/users/amy/update", headers=h, json={"role": "user", "active": False}).status_code == 200
    assert c.get("/api/me", headers=ha).status_code == 401  # disabled: existing token stops working
    assert c.post("/api/auth/login", json={"username": "amy", "password": "resetpass99"}).status_code == 401

    # guard rails
    assert c.post("/api/users/admin/update", headers=h, json={"active": False}).status_code == 400
    assert c.post("/api/users/admin/update", headers=h, json={"role": "user"}).status_code == 400
    assert c.post("/api/users/admin/delete", headers=h).status_code == 400
    assert c.post("/api/users/amy/delete", headers=h).status_code == 200
    assert c.post("/api/users/amy/delete", headers=h).status_code == 404
    i = c.post("/api/docs", files={"file": ("t.pdf", make_pdf(1))}, headers=h).json()["id"]
    assert c.post(f"/api/docs/{i}/share", headers=h, json={"user": "nobody"}).status_code == 404

    # lockout after repeated failures
    for _ in range(5):
        assert c.post("/api/auth/login", json={"username": "mallory", "password": "x"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "mallory", "password": "x"}).status_code == 429
    acts = {a["action"] for a in c.get("/api/audit", headers=h).json()}
    assert {"user_created", "user_updated", "user_deleted", "password_changed", "login_locked"} <= acts


def test_static_files_cannot_escape():
    for p in ("..%2f..%2fapp%2fconfig.py", "%2e%2e/%2e%2e/backend/app/config.py", "../app/config.py"):
        r = c.get("/" + p)
        assert b"JWT_SECRET" not in r.content, p


def test_desktop_mode_needs_no_login():
    import app.config as cfg
    assert c.get("/api/me").status_code == 401
    cfg.DESKTOP = True
    try:
        me = c.get("/api/me").json()
        assert me["desktop"] is True and me["role"] == "admin" and me["must_change"] is False
        i = c.post("/api/docs", files={"file": ("t.pdf", make_pdf(1))}).json()["id"]  # no Authorization header
        assert c.get(f"/api/docs/{i}/page/0.png?zoom=1").status_code == 200
    finally:
        cfg.DESKTOP = False
    assert c.get("/api/me").status_code == 401


def test_other_user_blocked():
    h = auth()
    r = c.post("/api/docs", files={"file": ("t.pdf", make_pdf(1))}, headers=h)
    i = r.json()["id"]
    assert c.post("/api/users", headers=h, json={"username": "bob", "password": "password1", "role": "user"}).status_code == 200
    hb = {"Authorization": "Bearer " + c.post("/api/auth/login", json={"username": "bob", "password": "password1"}).json()["token"]}
    assert c.get(f"/api/docs/{i}", headers=hb).status_code == 403
    assert c.get("/api/audit", headers=hb).status_code == 403
    assert i not in [d["id"] for d in c.get("/api/docs", headers=hb).json()]
    assert c.post(f"/api/docs/{i}/share", headers=hb, json={"user": "bob"}).status_code == 403
    assert c.post(f"/api/docs/{i}/share", headers=h, json={"user": "bob"}).status_code == 200
    assert c.get(f"/api/docs/{i}", headers=hb).status_code == 200
    assert i in [d["id"] for d in c.get("/api/docs", headers=hb).json()]
    assert i in [d["id"] for d in c.get("/api/docs", headers=h).json()]
