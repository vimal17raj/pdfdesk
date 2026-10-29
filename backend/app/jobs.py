"""Background jobs for long operations (OCR / compress of big files).

State lives in small JSON files under data/jobs, so any worker process can answer a status request even though
only one of them runs the job. A running job refreshes `updated` every few seconds; a job whose heartbeat stops
(server restarted) is reported as failed instead of spinning forever.
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import HTTPException

from . import config, pdfops

MAX_RUNNING = int(os.getenv("PDFDESK_MAX_JOBS", "2"))
STALE_AFTER = 90  # seconds without a heartbeat


def _file(job_id: str) -> Path:
    if not job_id.isalnum():
        raise HTTPException(404, "No such job")
    return config.JOBS_DIR / f"{job_id}.json"


def _write(job: dict):
    f = _file(job["id"])
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(job), "utf-8")
    os.replace(tmp, f)


def _read(job_id: str) -> dict:
    try:
        return json.loads(_file(job_id).read_text("utf-8"))
    except FileNotFoundError:
        raise HTTPException(404, "No such job")


def _public(job: dict) -> dict:
    out = {k: v for k, v in job.items() if k not in ("log", "owner")}
    out["elapsed"] = int((job["finished"] if job.get("finished") else time.time()) - job["started"])
    return out


def get(job_id: str, user: dict) -> dict:
    job = _read(job_id)
    if job["owner"] != user["user"] and user["role"] != "admin":
        raise HTTPException(403, "Not your job")
    if job["status"] == "running":
        if time.time() - job["updated"] > STALE_AFTER:
            job.update(status="error", error="The task stopped unexpectedly (server restarted?). Please try again.",
                       finished=time.time())
            _write(job)
        elif job["kind"] == "ocr":
            job["progress"] = pdfops.ocr_progress(job["log"])
    return _public(job)


def running_count() -> int:
    n = 0
    for f in config.JOBS_DIR.glob("*.json"):
        try:
            j = json.loads(f.read_text("utf-8"))
            if j["status"] == "running" and time.time() - j["updated"] <= STALE_AFTER:
                n += 1
        except Exception:
            pass
    return n


def cancel(job_id: str, user: dict):
    job = _read(job_id)
    if job["owner"] != user["user"] and user["role"] != "admin":
        raise HTTPException(403, "Not your job")
    _file(job_id).with_suffix(".cancel").write_text("1")


def start(kind: str, doc_id: str, owner: str, worker) -> dict:
    """worker(job, cancelled) -> dict with the result; raises pdfops.Cancelled / HTTPException / anything."""
    if running_count() >= MAX_RUNNING:
        raise HTTPException(429, "Other long tasks are already running on the server. Try again in a few minutes.")
    jid = uuid.uuid4().hex[:12]
    now = time.time()
    job = {"id": jid, "kind": kind, "doc": doc_id, "owner": owner, "status": "running", "started": now, "updated": now,
           "progress": None, "log": str(config.TMP_DIR / f"{jid}.log")}
    _write(job)
    cflag = _file(jid).with_suffix(".cancel")
    done = threading.Event()

    def cancelled() -> bool:
        return cflag.exists()

    def beat():
        while not done.wait(5):
            job["updated"] = time.time()
            if job["status"] == "running":
                _write(job)

    def run():
        try:
            job.update(worker(job, cancelled) or {})
            job["status"] = "done"
        except pdfops.Cancelled:
            job["status"] = "cancelled"
        except HTTPException as e:
            job.update(status="error", error=str(e.detail))
        except Exception as e:  # noqa: BLE001 - report anything to the user rather than hang
            job.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
        finally:
            done.set()
            job["finished"] = time.time()
            job["updated"] = job["finished"]
            _write(job)
            cflag.unlink(missing_ok=True)

    threading.Thread(target=beat, daemon=True).start()
    threading.Thread(target=run, daemon=True).start()
    return _public(job)
