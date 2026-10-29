"""Stand-alone ocrmypdf.exe shipped next to PDFDesk.exe (the app runs it as a command, like on the server)."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from ocrmypdf.__main__ import run

    sys.exit(run())
