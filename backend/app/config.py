import os
from pathlib import Path

DATA_DIR = Path(os.getenv("PDFDESK_DATA", Path(__file__).resolve().parents[1] / "data"))
DOCS_DIR = DATA_DIR / "docs"
DB_PATH = DATA_DIR / "audit.db"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

JWT_SECRET = os.getenv("PDFDESK_JWT_SECRET", "change-me-in-production")
JWT_HOURS = int(os.getenv("PDFDESK_JWT_HOURS", "10"))

# Accounts live in the app (admin creates users in the "Users" screen). On the very first start an
# 'admin' account is created with this password and must be changed at first sign-in.
ADMIN_PASSWORD = os.getenv("PDFDESK_ADMIN_PASSWORD", "admin123")

# Optional: set to use Claude for "Generative summary". Document text is sent to Anthropic when set.
# Left empty, summaries are produced offline on this server (extractive, no AI).
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("PDFDESK_LLM_MODEL", "claude-sonnet-5-5")

MAX_UPLOAD_MB =int(os.getenv("PDFDESK_MAX_UPLOAD_MB", "200"))
OCR_LANGS = os.getenv("PDFDESK_OCR_LANGS", "eng")
