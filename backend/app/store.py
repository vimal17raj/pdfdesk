"""Document store: each document is a folder with numbered versions (v1.pdf, v2.pdf, ...) for undo/redo."""
import json
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException

from .config import DOCS_DIR


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
    (d / "meta.json").write_text(json.dumps(m), "utf-8")


def create(owner: str, name: str, data: bytes) -> str:
    doc_id = uuid.uuid4().hex[:16]
    d = DOCS_DIR / doc_id
    d.mkdir(parents=True)
    (d / "v1.pdf").write_bytes(data)
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
            f = d / f"v{m['version']}.pdf"
            st = f.stat()
            out.append({"id": d.name, "name": m["name"], "owner": m["owner"], "size": st.st_size,
                        "updated": int(st.st_mtime), "shared": m["owner"] != user["user"]})
        except Exception:
            continue
    return sorted(out, key=lambda x: -x["updated"])[:limit]


def path(doc_id: str) -> Path:
    d = _dir(doc_id)
    return d / f"v{_meta(d)['version']}.pdf"


def push(doc_id: str, data: bytes):
    """Write a new version, discarding any redo history."""
    d = _dir(doc_id)
    m = _meta(d)
    for v in range(m["version"] + 1, m["top"] + 1):
        (d / f"v{v}.pdf").unlink(missing_ok=True)
    m["version"] += 1
    m["top"] = m["version"]
    (d / f"v{m['version']}.pdf").write_bytes(data)
    # keep disk bounded
    old = m["version"] - 30
    if old > 0:
        (d / f"v{old}.pdf").unlink(missing_ok=True)
    _save_meta(d, m)


def step(doc_id: str, delta: int) -> dict:
    d = _dir(doc_id)
    m = _meta(d)
    nv = m["version"] + delta
    if nv < 1 or nv > m["top"] or not (d / f"v{nv}.pdf").exists():
        raise HTTPException(409, "Nothing to undo" if delta < 0 else "Nothing to redo")
    m["version"] = nv
    _save_meta(d, m)
    return info(doc_id)


def delete(doc_id: str):
    shutil.rmtree(_dir(doc_id), ignore_errors=True)
