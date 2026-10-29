"""Document store: each document is a folder with numbered versions (v1.pdf, v2.pdf, ...) for undo/redo.

Big files (500-900 pages) drive the design:
  * read requests share one open document per (doc, version) instead of re-parsing the file every time;
  * an edit copies the current version and appends only the changes (incremental save) instead of rewriting everything;
  * undo history is capped by disk size, not just by count.
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
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(m), "utf-8")
    os.replace(tmp, d / "meta.json")


def create(owner: str, name: str, data: bytes) -> str:
    doc_id = uuid.uuid4().hex[:16]
    d = DOCS_DIR / doc_id
    d.mkdir(parents=True)
    (d / "v1.pdf").write_bytes(data)
    _save_meta(d, {"owner": owner, "name": name, "version": 1, "top": 1})
    return doc_id


def create_from_file(owner: str, name: str, src: Path) -> str:
    doc_id = uuid.uuid4().hex[:16]
    d = DOCS_DIR / doc_id
    d.mkdir(parents=True)
    shutil.move(str(src), str(d / "v1.pdf"))
    _save_meta(d, {"owner": owner, "name": name, "version": 1, "top": 1})
    return doc_id


def info(doc_id: str) -> dict:
    d = _dir(doc_id)
    m = _meta(d)
    return {"id": doc_id, "name": m["name"], "version": m["version"], "top": m["top"], "owner": m["owner"]}


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


def list_for(user: dict, limit: int = 100) -> list[dict]:
    """The user's own and shared documents, newest first."""
    out = []
    for d in DOCS_DIR.iterdir():
        try:
            m = _meta(d)
            if m["owner"] != user["user"] and user["user"] not in m.get("shared", []):
                continue
            st = (d / f"v{m['version']}.pdf").stat()
            out.append({"id": d.name, "name": m["name"], "owner": m["owner"], "size": st.st_size,
                        "updated": int(st.st_mtime), "shared": m["owner"] != user["user"]})
        except Exception:
            continue
    return sorted(out, key=lambda x: -x["updated"])[:limit]


def path(doc_id: str) -> Path:
    d = _dir(doc_id)
    return d / f"v{_meta(d)['version']}.pdf"


# ---------------------------------------------------------------- reading (shared open document)
@contextlib.contextmanager
def reading(doc_id: str):
    """Yields the current version as an open PyMuPDF document, shared between requests (it is not thread-safe,
    so it is held under the document's lock). Opening happens lazily from disk, so only the pages used are parsed."""
    d = _dir(doc_id)
    m = _meta(d)
    key = (doc_id, m["version"])
    with _lock(doc_id):
        with _cache_guard:
            doc = _cache.get(key)
            if doc is not None:
                _cache.move_to_end(key)
        if doc is None:
            doc = pymupdf.open(str(d / f"v{m['version']}.pdf"))
            if doc.needs_pass:
                doc.close()
                raise HTTPException(400, "PASSWORD_REQUIRED")
            doc._pd_shared = True
            with _cache_guard:
                for k in [k for k in _cache if k[0] == doc_id]:  # older versions of this document
                    _cache.pop(k, None)
                _cache[key] = doc
                while len(_cache) > CACHE_MAX:
                    _cache.popitem(last=False)  # dropped, not closed: a request still using it keeps its reference
        yield doc


_sizes: "collections.OrderedDict[tuple, list]" = collections.OrderedDict()


def page_sizes(doc_id: str) -> list[dict]:
    d = _dir(doc_id)
    key = (doc_id, _meta(d)["version"])
    hit = _sizes.get(key)
    if hit is not None:
        return hit
    with reading(doc_id) as doc:
        out = pdfops.page_sizes(doc)
    _sizes[key] = out
    while len(_sizes) > 20:
        _sizes.popitem(last=False)
    return out


# ---------------------------------------------------------------- writing
def _evict(doc_id: str):
    """Forget cached open handles of this document (Windows can't delete a file that's still open)."""
    with _cache_guard:
        for k in [k for k in _cache if k[0] == doc_id]:
            _cache.pop(k, None)


def _rm(f: Path):
    try:
        f.unlink(missing_ok=True)
    except PermissionError:
        pass  # still held open by a request in flight; harmless leftover, removed with the document


def _prune(d: Path, m: dict):
    """Keep at most 30 undo steps and HISTORY_MB of old versions; never touch the current or redo versions."""
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


def push(doc_id: str, data: bytes):
    """Write a whole new version from bytes."""
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        nv, dst = _next_slot(d, m)
        dst.write_bytes(data)
        _finish(d, m, nv)


def commit_file(doc_id: str, src: Path, base_version: int) -> bool:
    """Make `src` the next version if nobody edited the document since `base_version`. Returns False if they did."""
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        if m["version"] != base_version:
            return False
        nv, dst = _next_slot(d, m)
        shutil.move(str(src), str(dst))
        _finish(d, m, nv)
        return True


def edit(doc_id: str, fn) -> dict:
    """Apply `fn(doc)` (a pdfops edit function) to a copy of the current version and save only what changed.
    A 900-page file is copied (fast) and appended to, instead of being parsed and rewritten end to end."""
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        src = d / f"v{m['version']}.pdf"
        nv, dst = _next_slot(d, m)
        shutil.copyfile(src, dst)
        doc = None
        try:
            doc = pymupdf.open(str(dst))
            tok = pdfops.INPLACE.set(True)
            try:
                fn(doc)
            finally:
                pdfops.INPLACE.reset(tok)
            try:
                doc.saveIncr()
            except Exception:  # file can't be appended to (e.g. it needed repair): do a full rewrite instead
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


def step(doc_id: str, delta: int) -> dict:
    with _lock(doc_id):
        d = _dir(doc_id)
        m = _meta(d)
        nv = m["version"] + delta
        if nv < 1 or nv > m["top"] or not (d / f"v{nv}.pdf").exists():
            raise HTTPException(409, "Nothing to undo" if delta < 0 else "Nothing to redo")
        m["version"] = nv
        _save_meta(d, m)
        return info(doc_id)


def delete(doc_id: str):
    with _lock(doc_id):
        d = _dir(doc_id)
        _evict(doc_id)
        shutil.rmtree(d, ignore_errors=True)
