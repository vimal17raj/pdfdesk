# PyInstaller spec: builds dist/PDFDesk/ containing PDFDesk.exe (the app) and ocrmypdf.exe (the OCR command).
# Run from the repository root:   backend\.venv\Scripts\pyinstaller packaging\pdfdesk.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"

datas, binaries, hidden = [], [], []
for pkg in ("ocrmypdf", "pikepdf", "pymupdf", "pluggy", "img2pdf", "docx", "multipart", "pdfminer"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hidden += h
    except Exception:
        pass
hidden += collect_submodules("uvicorn") + collect_submodules("app") + ["jwt", "httpx", "h11", "anyio", "email_validator"]
datas += [(str(ROOT / "frontend" / "dist"), "web")]

EXCLUDES = ["tkinter", "matplotlib", "numpy.tests", "pytest", "IPython"]

app = Analysis([str(BACKEND / "desktop.py")], pathex=[str(BACKEND)], binaries=binaries, datas=datas,
               hiddenimports=hidden, excludes=EXCLUDES, noarchive=False)
ocr = Analysis([str(ROOT / "packaging" / "ocr_entry.py")], pathex=[str(BACKEND)], binaries=binaries, datas=datas,
               hiddenimports=hidden, excludes=EXCLUDES, noarchive=False)
MERGE((app, "desktop", "PDFDesk"), (ocr, "ocr_entry", "ocrmypdf"))

app_exe = EXE(PYZ(app.pure), app.scripts, [], exclude_binaries=True, name="PDFDesk", console=False,
              icon=str(ROOT / "packaging" / "pdfdesk.ico") if (ROOT / "packaging" / "pdfdesk.ico").exists() else None)
ocr_exe = EXE(PYZ(ocr.pure), ocr.scripts, [], exclude_binaries=True, name="ocrmypdf", console=True)

COLLECT(app_exe, app.binaries, app.datas, ocr_exe, ocr.binaries, ocr.datas, name="PDFDesk")
