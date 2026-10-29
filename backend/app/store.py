"""Document store.

Two layouts, chosen when a document is created:

* "copies" (files up to PDFDESK_HUGE_MB): numbered versions v1.pdf, v2.pdf ... Each edit copies the current
  version and appends the change to the copy; undo/redo just switch which file is current.

* "append" (bigger files, up to many GB): ONE file, work.pdf. An edit appends its change to the end of that
  file (a PDF "incremental update"), so it costs time proportional to the change, not to the file size.
  Undo cuts the appended bytes off (keeping them aside so Redo can put them back). Only the size after each
  version is remembered. A file opened from the user's disk (desktop app) is used in place, read-only,
  until the first edit, when a private working copy is made once.

Read requests share one open document per (doc, revision) instead of re-parsing the file every time.
"""
import collections
import contextlib
import json
import os
import shutil
import threading
import uuid
from pathlib import Path

import pymupdf
from fastapi import HTTPException

from . import config, pdfops
from .config import DOCS_DIR

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()
_cache: "collections.OrderedDict[tuple, pymupdf.Document]" = collections.OrderedDict()
_cache_guard = threading.Lock()
_sizes: "collections.OrderedDict[tuple, list]" = collections.OrderedDict()
CACHE_MAX = int(os.getenv("PDFDESK_OPEN_CACHE", "4"))


def _lock(doc_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(doc_id, threading.RLock())


def _dir(doc_id: str) -> Path:
    if not doc_id.isalnum():
        raise HTTPException(404, "Document not found")
    d = DOCS_DIR / doc_id
    if not d.exists():
        raise HTTPException(404, "Document not found")
    return d


def _meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text("utf-8"))


def _save_meta(d: Path, m: dict):
    m["rev"] = m.get("rev", 0) + 1  # changes on every edit / undo / redo, so caches (server and browser) never go stale
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(m), "utf-8")
    os.replace(tmp, d / "meta.json")


def _is_huge(size: int) -> bool:
    return size > config.HUGE_MB * 1024 * 1024


def _new_dir(owner: str, name: str) -> tuple[str, Path]:
    doc_id = uuid.uuid4().hex[:16]
    d = DOCS_DIR / doc_id
    d.mkdir(parents=True)
    return doc_id, d


def create(owner: str, name: str, data: bytes) -> str:
    doc_id, d = _new_dir(owner, name)
    tmp = d / "incoming.pdf"
    tmp.write_bytes(data)
    return _adopt(doc_id, d, owner, name, tmp)


def create_from_file(owner: str, name: str, src: Path) -> str:
    """Take over a finished file (it is moved, not copied)."""
    doc_id, d = _new_dir(owner, name)
    shutil.move(str(src), str(d / "incoming.pdf"))
    return _adopt(doc_id, d, owner, name, d / "incoming.pdf")


def _adopt(doc_id: str, d: Path, owner: str, name: str, f: Path) -> str:
    size = f.stat().st_size
    if _is_huge(size):
        os.replace(f, d / "work.pdf")
        m = {"owner": owner, "name": name, "mode": "append", "version": 1, "top": 1, "marks": [size]}
    else:
        os.replace(f, d / "v1.pdf")
        m = {"owner": owner, "name": name, "mode": "copies", "version": 1, "top": 1}
    _save_meta(d, m)
    return doc_id


def create_linked(owner: str, name: str, src: Path) -> str:
    """Desktop app: use a file where it is (no copy). Read-only until the first edit."""
    doc_id, d = _new_dir(owner, name)
    size = src.stat().st_size
    _save_meta(d, {"owner": owner, "name": name, "mode": "append", "version": 1, "top": 1, "marks": [size],
                   "linked": str(src)})
    return doc_id


def info(doc_id: str) -> dict:
    d = _dir(doc_id)
    m = _meta(d)
    return {"id": doc_id, "name": m["name"], "version": m["version"], "top": m["top"], "owner": m["owner"],
            "rev": m.get("rev", 0), "huge": m.get("mode") == "append", "linked": bool(m.get("linked") and not (d / "work.pdf").exists())}


def check_access(doc_id: str, user: dict):
    m = _meta(_dir(doc_id))
    if m["owner"] != user["user"] and user["role"] != "admin" and user["user"] not in m.get("shared", []):
        raise HTTPException(403, "Not your document")


def share(doc_id: str, owner: dict, with_user: str):
    d = _dir(doc_id)
    m = _meta(d)
    if m["owner"] != owner["user"] and owner["role"] != "admin":
        raise HTTPException(403, "Only the owner can share this document")
    with_user = with_user.strip().lower()
    if not with_user or with_user == m["owner"]:
        raise HTTPException(400, "Enter another user's username")
    if with_user not in m.setdefault("shared", []):
        m["shared"].append(with_user)
    _save_meta(d, m)
    return m["shared"]


def _file_of(d: Path, m: dict) -> Path:
    """The file that currently *is* the document."""
    if m.get("mode") == "append":
        w = d / "work.pdf"
        return w if w.exists() else Path(m["linked"])
    return d / f"v{m['version']}.pdf"


def list_for(user: dict, limit: int = 100) -> list[dict]:
    """The user's own and shared documents, newest first."""
    out = []
    for d in DOCS_DIR.iterdir():
        try:
            m = _meta(d)
            if m["owner"] != user["user"] and user["user"] not in m.get("shared", []):
                continue
            st = _file_of(d, m).stat()
            out.append({"id": d.name, "name": m["name"], "owner": m["owner"], "size": st.st_size,
                        "updated": int(st.st_mtime), "shared": m["owner"] != user["user"]})
        except Exception:
            continue
    return sorted(out, key=lambda x: -x["updated"])[:limit]


def path(doc_id: str) -> Path:
    d = _dir(doc_id)
    return _file_of(d, _meta(d))


# ---------------------------------------------------------------- reading (shared open document)
@contextlib.contextmanager
def reading(doc_id: str):
    """Yields the current version as an open PyMuPDF document, shared between requests (it is not thread-safe,
    so it is held under the document's lock). Opening happens lazily from disk, so only the pages used are parsed."""
    d = _dir(doc_id)
    m = _meta(d)
    key = (doc_id, m.get("rev", 0))
    with _lock(doc_id):
        with _cache_guard:
            doc = _cache.get(key)
            if doc is not None:
                _cache.move_to_end(key)
        if doc is None:
            doc = pymupdf.open(str(_file_of(d, m)), filetype="pdf")
            if doc.needs_pass:
                doc.close()
                raise HTTPException(400, "PASSWORD_REQUIRED")
            doc._pd_shared = True
            with _cache_guard:
                for k in [k for k in _cache if k[0] == doc_id]:  # older revisions of this document
                    _cache.pop(k, None)
                _cache[key] = doc
                while len(_cache) > CACHE_MAX:
                    _cache.popitem(last=False)  # dropped, not closed: a request still using it keeps its reference
        yield doc


def page_sizes(doc_id: str) -> list[dict]:
    d = _dir(doc_id)
    key = (doc_id, _meta(d).get("rev", 0))
    hit = _sizes.get(key)
    if hit is not None:
        return hit
    with reading(doc_id) as doc:
        out = pdfops.page_sizes(doc)
    _sizes[key] = out
    while len(_sizes) > 20:
        _sizes.popitem(last=False)
    return out


def page_count(doc_id: str) -> int:
    with reading(doc_id) as doc:
        return doc.page_count


# ---------------------------------------------------------------- writing
def _evict(doc_id: str):
    """Forget cached open handles of this document (Windows can't delete or cut a file that's still open)."""
    with _cache_guard:
        for k in [k for k in _cache if k[0] == doc_id]:
            _cache.pop(k, None)
        for k in [k for k in _sizes if k[0] == doc_id]:
            _sizes.pop(k, None)
    if os.name == "nt":
        import gc
        gc.collect()  # let go of file handles PyMuPDF may still hold, or Windows refuses to replace/cut the file


def _rm(f: Path):
    try:
        f.unlink(missing_ok=True)
    except PermissionError:
        pass  # still held open by a request in flight; harmless leftover, removed with the document


def _prune(d: Path, m: dict):
    """copies mode: keep at most 30 undo steps and HISTORY_MB of old versions; never touch current or redo versions."""
    old = sorted(v for v in range(1, m["version"]) if (d / f"v{v}.pdf").exists())
    for v in old[:-30]:
        _rm(d / f"v{v}.pdf")
    old = sorted(v for v in range(1, m["version"]) if (d / f"v{v}.pdf").exists())
    total = sum((d / f"v{v}.pdf").stat().st_size for v in old)
    while old and total > config.HISTORY_MB * 1024 * 1024:
        f = d / f"v{old[0]}.pdf"
        total -= f.stat().st_size
        _rm(f)
        old.pop(0)


def _next_slot(d: Path, m: dict) -> tuple[int, Path]:
    _evict(d.name)
    for v in range(m["version"] + 1, m["top"] + 1):  # a new edit discards any redo history
        _rm(d / f"v{v}.pdf")
    nv = m["version"] + 1
    return nv, d / f"v{nv}.pdf"


def _finish(d: Path, m: dict, nv: int):
    m["version"] = nv
    m["top"] = nv
    _save_meta(d, m)
    _prune(d, m)


def _make_working_copy(d: Path, m: dict):
    """append mode: first change to a file that lives in the user's folder -> take a private copy (once)."""
    w = d / "work.pdf"
    if not w.exists():
        tmp = d / "work.tmp"
        shutil.copyfile(m["linked"], tmp)
        os.replace(tmp, w)
    return w


def _drop_redo_tails(d: Path, m: dict):
    for v in range(m["version"] + 1, m["top"] + 2):
        _rm(d / f"tail_{v}.bin")
    m["marks"] = m["marks"][: m["version"]]


def _reset_history(d: Path, m: dict):
    for f in d.glob("tail_*.bin"):
        _rm(f)
    size = (d / "work.pdf").stat().st_size
    m.update(version=1, top=1, marks=[size])


def push(doc_id: str, data: bytes):
    """Write a whole new version from bytes (operations that rebuild the file)."""
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        _evict(doc_id)
        if m.get("mode") == "append":
            tmp = d / "work.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, d / "work.pdf")
            _reset_history(d, m)
            _save_meta(d, m)
            return
        nv, dst = _next_slot(d, m)
        dst.write_bytes(data)
        _finish(d, m, nv)


def commit_file(doc_id: str, src: Path, base_rev: int) -> bool:
    """Make `src` the document's next state if nobody changed it since `base_rev`. Returns False if they did."""
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        if m.get("rev", 0) != base_rev:
            return False
        _evict(doc_id)
        if m.get("mode") == "append":
            try:
                os.replace(src, d / "work.pdf")
            except OSError:  # different disk
                shutil.move(str(src), str(d / "work.pdf"))
            _reset_history(d, m)
            _save_meta(d, m)
            return True
        nv, dst = _next_slot(d, m)
        shutil.move(str(src), str(dst))
        _finish(d, m, nv)
        return True


def edit(doc_id: str, fn) -> dict:
    """Apply `fn(doc)` (a pdfops edit function) and save only what changed."""
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        if m.get("mode") == "append":
            return _edit_append(doc_id, d, m, fn)
        src = d / f"v{m['version']}.pdf"
        nv, dst = _next_slot(d, m)
        shutil.copyfile(src, dst)
        doc = None
        try:
            doc = pymupdf.open(str(dst), filetype="pdf")
            tok = pdfops.INPLACE.set(True)
            try:
                fn(doc)
            finally:
                pdfops.INPLACE.reset(tok)
            append_failed = False
            try:
                doc.saveIncr()
            except Exception:  # file can't be appended to (e.g. it needed repair): do a full rewrite instead
                append_failed = True
            if append_failed:
                tmp = d / f"v{nv}.tmp"
                doc.save(str(tmp), garbage=1, deflate=True)
                doc.close()
                doc = None
                os.replace(tmp, dst)
        except BaseException:
            if doc is not None:
                doc.close()
                doc = None
            _rm(dst)
            raise
        finally:
            if doc is not None:
                doc.close()
        _finish(d, m, nv)
        return info(doc_id)


def _edit_append(doc_id: str, d: Path, m: dict, fn) -> dict:
    work = _make_working_copy(d, m)
    _evict(doc_id)
    before = work.stat().st_size
    doc = None
    rewritten = False
    try:
        doc = pymupdf.open(str(work), filetype="pdf")
        tok = pdfops.INPLACE.set(True)
        try:
            fn(doc)
        finally:
            pdfops.INPLACE.reset(tok)
        append_failed = False
        try:
            doc.saveIncr()  # appends the change to the end of the file; nothing else is read or rewritten
        except Exception:
            if work.stat().st_size != before:  # a half-written append: cut it off before anything else
                raise
            append_failed = True
        if append_failed:
            # e.g. a damaged file MuPDF had to repair can't be appended to: rebuild the whole file instead.
            # (Done outside the except block: the error object would keep the file open, and Windows can't replace it.)
            tmp = d / "work.tmp"
            doc.save(str(tmp), garbage=1, deflate=True)
            doc.close()
            doc = None
            os.replace(tmp, work)
            rewritten = True
    except BaseException:
        if doc is not None:
            doc.close()
            doc = None
        with open(work, "r+b") as f:  # undo any partial append
            if f.seek(0, 2) != before:
                f.truncate(before)
        raise
    finally:
        if doc is not None:
            doc.close()
    if rewritten:
        _reset_history(d, m)  # a rebuilt file has no cheap way back: Undo history starts over
    else:
        _drop_redo_tails(d, m)
        m["marks"].append(work.stat().st_size)
        m["version"] += 1
        m["top"] = m["version"]
    _save_meta(d, m)
    return info(doc_id)


def step(doc_id: str, delta: int) -> dict:
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        nv = m["version"] + delta
        if nv < 1 or nv > m["top"]:
            raise HTTPException(409, "Nothing to undo" if delta < 0 else "Nothing to redo")
        _evict(doc_id)
        if m.get("mode") == "append":
            work = d / "work.pdf"
            if not work.exists():
                raise HTTPException(409, "Nothing to undo")
            if delta < 0:  # keep the bytes of the undone change, then cut them off
                cut, end = m["marks"][nv - 1], m["marks"][nv]
                with open(work, "r+b") as f, open(d / f"tail_{nv + 1}.bin", "wb") as t:
                    f.seek(cut)
                    shutil.copyfileobj(f, t, 8 * 1024 * 1024)
                    f.truncate(cut)
            else:
                tail = d / f"tail_{nv}.bin"
                if not tail.exists():
                    raise HTTPException(409, "Nothing to redo")
                with open(work, "ab") as f, open(tail, "rb") as t:
                    shutil.copyfileobj(t, f, 8 * 1024 * 1024)
        elif not (d / f"v{nv}.pdf").exists():
            raise HTTPException(409, "Nothing to undo" if delta < 0 else "Nothing to redo")
        m["version"] = nv
        _save_meta(d, m)
        return info(doc_id)


def delete(doc_id: str):
    with _lock(doc_id):
        d = _dir(doc_id)
        _evict(doc_id)
        shutil.rmtree(d, ignore_errors=True)
