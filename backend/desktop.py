"""PDFDesk for Windows: starts the local server and opens it in an app-style window.

Single user, no login. Everything stays on this PC under %LOCALAPPDATA%\\PDFDesk.
Quits when the window is closed (and, as a safety net, when the page stops answering its heartbeat).
"""
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))   # bundled files
EXE_DIR = Path(sys.executable).parent if FROZEN else HERE                 # folder with PDFDesk.exe / ocrmypdf.exe
APPDATA = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "PDFDesk"
HEARTBEAT_TIMEOUT = 180   # seconds without a ping from the open page before the server shuts itself down


def _quiet_streams():
    """A windowed exe has no console; make print/logging harmless and keep a log file for support."""
    APPDATA.mkdir(parents=True, exist_ok=True)
    log = open(APPDATA / "pdfdesk.log", "a", buffering=1, encoding="utf-8", errors="replace")
    if sys.stdout is None or FROZEN:
        sys.stdout = log
    if sys.stderr is None or FROZEN:
        sys.stderr = log
    return log


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _tesseract_dirs() -> list[str]:
    cands = [
        EXE_DIR / "tesseract",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR",
    ]
    return [str(p) for p in cands if (p / "tesseract.exe").exists()]


def _find_browser() -> str | None:
    pf = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", "")]
    for base in pf:
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            p = Path(base) / rel
            if base and p.exists():
                return str(p)
    return None


def _open_window(url: str) -> subprocess.Popen | None:
    exe = _find_browser()
    if not exe:
        webbrowser.open(url)  # no Edge/Chrome: a normal browser tab; the heartbeat ends the server when it's closed
        return None
    return subprocess.Popen([
        exe, f"--app={url}", f"--user-data-dir={APPDATA / 'browser'}", "--no-first-run",
        "--no-default-browser-check", "--window-size=1400,900", "--disable-features=Translate",
    ])


def main():
    multiprocessing.freeze_support()
    _quiet_streams()
    port_file = APPDATA / "port"
    # already running? just open another window on the same server
    try:
        old = int(port_file.read_text())
        if _alive(old):
            _open_window(f"http://127.0.0.1:{old}/")
            return
    except Exception:
        pass

    data = APPDATA / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update(PDFDESK_DESKTOP="1", PDFDESK_DATA=str(data), PDFDESK_STATIC=str(HERE / "web"),
                      PDFDESK_JWT_SECRET="desktop-only-" + str(os.getpid()))
    os.environ["PATH"] = os.pathsep.join([str(EXE_DIR), *_tesseract_dirs(), os.environ.get("PATH", "")])

    import uvicorn
    from app import config
    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if _alive(port):
            break
        time.sleep(0.1)
    else:
        print("server did not start", file=sys.stderr)
        return
    port_file.write_text(str(port))

    started = time.time()
    config.LAST_PING = 0.0
    proc = _open_window(f"http://127.0.0.1:{port}/")

    def watchdog():
        while True:
            time.sleep(5)
            last = config.LAST_PING
            if last and time.time() - last > HEARTBEAT_TIMEOUT:
                break
            if not last and time.time() - started > 120:  # window never opened
                break
            if proc is not None and proc.poll() is not None and time.time() - started > 8 and last and time.time() - last > 20:
                break  # the app window is gone and the page stopped pinging
        os._exit(0)

    threading.Thread(target=watchdog, daemon=True).start()
    if proc is not None:
        proc.wait()
        if time.time() - started > 8:  # a real close, not a hand-off to an already-running browser
            time.sleep(1)
            os._exit(0)
    while True:  # hand-off case / no Edge: the watchdog ends the process
        time.sleep(60)


if __name__ == "__main__":
    main()
