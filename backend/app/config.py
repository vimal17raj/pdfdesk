import os
from pathlib import Path

DATA_DIR = Path(os.getenv("PDFDESK_DATA", Path(__file__).resolve().parents[1] / "data"))
DOCS_DIR = DATA_DIR / "docs"
DB_PATH = DATA_DIR / "audit.db"
# Windows desktop app: one person on their own PC. No login; the server only listens on 127.0.0.1.
DESKTOP = os.getenv("PDFDESK_DESKTOP") == "1"
LAST_PING = 0.0  # desktop app: time of the last heartbeat from the open window
STATIC_DIR = Path(os.getenv("PDFDESK_STATIC") or Path(__file__).resolve().parents[2] / "frontend" / "dist")

JOBS_DIR = DATA_DIR / "jobs"
TMP_DIR = DATA_DIR / "tmp"
for _d in (DOCS_DIR, JOBS_DIR, TMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

JWT_SECRET = os.getenv("PDFDESK_JWT_SECRET", "change-me-in-production")
JWT_HOURS = int(os.getenv("PDFDESK_JWT_HOURS", "10"))

# Accounts live in the app (admin creates users in the "Users" screen). On the very first start an
# 'admin' account is created with this password and must be changed at first sign-in.
ADMIN_PASSWORD = os.getenv("PDFDESK_ADMIN_PASSWORD", "admin123")

# Optional: set to use Claude for "Generative summary". Document text is sent to Anthropic when set.
# Left empty, summaries are produced offline on this server (extractive, no AI).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("PDFDESK_LLM_MODEL", "claude-sonnet-5-5")

# 0 = no limit (uploads are streamed straight to disk, never held in memory)
MAX_UPLOAD_MB = int(os.getenv("PDFDESK_MAX_UPLOAD_MB", "0"))
# Files bigger than this are edited in place by appending (no whole-file copy per edit) and undone by truncating
HUGE_MB = int(os.getenv("PDFDESK_HUGE_MB", "500"))
OCR_LANGS = os.getenv("PDFDESK_OCR_LANGS", "eng")
# CPU cores used by one OCR run (each core also needs ~300-500 MB of RAM on big scans)
OCR_JOBS = int(os.getenv("PDFDESK_OCR_JOBS", str(max(1, min(os.cpu_count() or 1, 3)))))
# Disk space used by undo history per document (oldest versions are dropped beyond this)
HISTORY_MB = int(os.getenv("PDFDESK_HISTORY_MB", "1500"))
