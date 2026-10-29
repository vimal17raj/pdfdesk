"""Machine-readable OCRmyPDF progress for PDFDesk's background job UI."""
import json
import os
import re
import subprocess
from pathlib import Path

from ocrmypdf import hookimpl
from ocrmypdf.builtin_plugins.tesseract_ocr import TesseractOcrEngine


class PdfDeskProgress:
    def __init__(self, *, total=None, desc=None, unit=None, **_kwargs):
        self.path = os.environ.get("PDFDESK_OCR_PROGRESS_FILE")
        self.total = total
        self.desc = desc or "OCR"
        self.unit = unit or "item"
        self.completed = 0

    def __enter__(self):
        self._save()
        return self

    def __exit__(self, *_exc):
        return False

    def update(self, n=1, *, completed=None):
        self.completed = completed if completed is not None else self.completed + n
        self._save()

    def _save(self):
        if not self.path:
            return
        path = Path(self.path)
        data = {"stage": self.desc, "completed": self.completed, "total": self.total, "unit": self.unit}
        self._write_page_state(data)

    @classmethod
    def _write_page_state(cls, data):
        progress_file = os.environ.get("PDFDESK_OCR_PROGRESS_FILE")
        if not progress_file:
            return
        path = Path(progress_file)
        temp = path.with_name(path.name + f".{os.getpid()}.tmp")
        try:
            temp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            temp.unlink(missing_ok=True)


class PdfDeskTesseractEngine(TesseractOcrEngine):
    """Report the active PDF page while Tesseract processes it in a worker."""

    def generate_hocr(self, input_file, output_hocr, output_text, options):
        match = re.match(r"(\d{6})_", Path(output_hocr).name)
        page = int(match.group(1)) if match else None
        total = int(os.environ.get("PDFDESK_OCR_TOTAL_PAGES", "0")) or None
        if page is not None:
            self._write_page_state({"stage": "OCR", "active_page": page, "total": total})
        try:
            return super().generate_hocr(input_file, output_hocr, output_text, options)
        finally:
            if page is not None:
                self._write_page_state({"stage": "OCR", "last_page": page, "total": total})


@hookimpl
def get_ocr_engine():
    # Keep this hook argument-free for compatibility with the older OCRmyPDF
    # hook specification used by the Linux production image. This app only
    # offers Tesseract, so no engine-selection option is needed here.
    return PdfDeskTesseractEngine()


@hookimpl
def get_progressbar_class():
    return PdfDeskProgress


@hookimpl
def initialize(plugin_manager):
    """Hide console windows from OCRmyPDF's Tesseract/Ghostscript children on Windows."""
    if os.name != "nt":
        return
    from ocrmypdf.subprocess import _run

    original_popen = _run.Popen
    if getattr(original_popen, "_pdfdesk_no_window", False):
        return

    def hidden_console_popen(*args, **kwargs):
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        return original_popen(*args, **kwargs)

    hidden_console_popen._pdfdesk_no_window = True
    _run.Popen = hidden_console_popen
