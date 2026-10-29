"""Windows desktop app: the standard 'Open' dialog, so a file can be used where it is (no upload, no copy)."""
import ctypes
import os
from ctypes import wintypes


class _OPENFILENAMEW(ctypes.Structure):
    _fields_ = [
        ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND), ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", ctypes.c_void_p), ("lpstrCustomFilter", ctypes.c_void_p), ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD), ("lpstrFile", ctypes.c_void_p), ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", ctypes.c_void_p), ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", ctypes.c_void_p),
        ("lpstrTitle", ctypes.c_void_p), ("Flags", wintypes.DWORD), ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD), ("lpstrDefExt", ctypes.c_void_p), ("lCustData", wintypes.LPARAM),
        ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", ctypes.c_void_p), ("pvReserved", ctypes.c_void_p),
        ("dwReserved", wintypes.DWORD), ("FlagsEx", wintypes.DWORD),
    ]


def pick_pdf() -> str | None:
    """Shows the Open dialog and returns the chosen path, or None if cancelled / not on Windows."""
    if os.name != "nt":
        return None
    keep = []  # the buffers must stay alive while the dialog is open

    def wbuf(text: str):
        b = ctypes.create_unicode_buffer(text)
        keep.append(b)
        return ctypes.cast(b, ctypes.c_void_p)

    file_buf = ctypes.create_unicode_buffer(32768)
    ofn = _OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(_OPENFILENAMEW)
    ofn.hwndOwner = ctypes.windll.user32.GetForegroundWindow()  # appear on top of the app window
    ofn.lpstrFilter = wbuf("PDF files and pictures\0*.pdf;*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.bmp\0All files\0*.*\0\0")
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(file_buf, ctypes.c_void_p)
    ofn.nMaxFile = len(file_buf)
    ofn.lpstrTitle = wbuf("Open a PDF")
    ofn.Flags = 0x00001000 | 0x00000800 | 0x00080000 | 0x00000008  # file must exist | path must exist | explorer | keep cwd
    if not ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        return None
    return file_buf.value or None
