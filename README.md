# PDFDesk

Self-hosted PDF tool (Adobe Acrobat alternative): FastAPI + PyMuPDF + OCRmyPDF backend, React single-page front end.

## Layout
- **Menu bar** (every screen): **File** (Open, Recent files, Save, Save as, Print, Share, Properties, Close), **Edit** (Undo/Redo, copy text, snapshot, paste image/text, rotate/insert/delete page, Find), **Convert** (PDF → Word / text / HTML / PNG / JPG, images → PDF, OCR, compress, protect).
- **All tools** menu (first on the menu bar): Export a PDF · Edit a PDF · Create a PDF · Combine files · Organize pages · Generative summary · Fill & e-signatures · Scan & OCR · Protect a PDF · Compress a PDF · Prepare a form · Add comments · Add a stamp · Compare files.
- **Right-hand rail** on the viewer: Generative summary, **Search** (Ctrl+F: ▲ ▼ step through matches, and a result list showing page, position on the page and the words around each match — click one to jump), Comments, Bookmarks, Page thumbnails, Fill & sign; page number box with previous/next, rotate page, fit width / fit page / 100%, zoom in/out.
- Shortcuts: Ctrl+O open, Ctrl+S save, Ctrl+Shift+S save as, Ctrl+P print, Ctrl+F find, Ctrl+Z / Ctrl+Y undo/redo, Ctrl+V paste.
- **Accounts (local only, no LDAP):** two roles — **admin** (manage users, audit log, open any document) and **user** (own + shared documents). Admins add/disable/delete users, change roles and reset passwords on the **Users** screen; new and reset accounts must choose their own password at first sign-in. Passwords are stored as salted scrypt hashes; 5 wrong attempts lock that name+address for 5 minutes; a disabled user is locked out immediately. The first start creates `admin` with `PDFDESK_ADMIN_PASSWORD`. Everyone can change their password under File → Change my password.
- Admins also get an **Audit log** (logins, failed/locked sign-ins, user changes, uploads, edits, OCR, downloads, shares…).

**Objects are editable like in Acrobat:** stamps, signatures, pasted images, text boxes, boxes/ellipses, notes, highlights, drawings and form fields can be clicked to select. Drag to move, drag handles to resize (pictures keep their proportions), double-click to edit text, use the floating bar for colour / font size / line thickness, and press Delete (or 🗑) to remove. Stamps placed with an older version are flattened into the page — Undo removes those.

**Open first, decide later:** a PDF opens in **View & search** mode (read-only, with Find, thumbnails, bookmarks, zoom). Nothing can be changed by accident; pick Edit, OCR, Convert, etc. from **All tools** when you want them.

**Big files (500–900 pages):** the server keeps the open document in memory between clicks, edits copy the file and append only the change (≈0.5 s on a 190 MB file), pages render on demand as JPEG, and the browser draws only the pages near the one on screen. OCR and Compress run as background jobs with elapsed time, progress, cancel, and an optional page range (e.g. `1-50`); if the document is edited while a job runs, the result is saved as a separate document instead of overwriting the edits. Find stops at 2000 matches. Undo history is capped at 30 steps and `PDFDESK_HISTORY_MB` (default 1500) per document. Use 3+ CPU cores and 4+ GB RAM for 900-page scans; set `PDFDESK_WORKERS` (default 3), `PDFDESK_OCR_JOBS` (cores per OCR run).

## Windows desktop app (single user, no server)
`D:\PDFDesk-Setup.exe` installs PDFDesk on one PC: Start Menu / desktop shortcut, its own window (Edge app mode), **no login**, everything stays in `%LOCALAPPDATA%\PDFDesk` (uninstall keeps your documents). The installer also installs the OCR engine (Tesseract, English) if the PC doesn't have it. Closing the window quits the program. Needs 64-bit Windows 10/11 with Microsoft Edge or Chrome.
Rebuild it with `packaging\build.ps1` (see the header of that file for the one-time prerequisites). The installer is not code-signed, so Windows SmartScreen shows "Unknown publisher" the first time (More info → Run anyway). Multi-user features (Users, Audit log, Share, sign-in) exist only in the server version.

## Run (production, Docker)
```
docker compose up -d --build
```
Edit `docker-compose.yml` first (JWT secret, first-start admin password). Put nginx with HTTPS in front — see `deploy/nginx-pdfdesk.conf`.

## Run (development, Windows)
```
cd backend && python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\uvicorn app.main:app --reload          # API (and built UI) on :8000
cd frontend && npm install && npm run dev            # UI with hot reload on :5173
cd frontend && npm run build                         # rebuild UI served by the API
```
OCR needs `ocrmypdf` + `tesseract` on PATH (the Docker image includes them). Default dev login: `admin` / `admin123` (you are asked to change it at first sign-in; to reset everything, stop the server and delete `backend/data/users.db`).
Tests: `cd backend && .venv\Scripts\python -m pytest tests`.

## Notes
- **Save / Save as:** in Chrome and Edge, files opened from disk are written back in place (Save) or to a new file (Save as). Other browsers download a copy. PDFDesk also keeps every file you open in its own library (File → Recent files); remove old ones there.
- **Share:** gives another PDFDesk user access to the same document (they can edit it). Use Save as to send a separate copy.
- **Generative summary:** offline by default (picks key sentences, nothing leaves the server). Set `ANTHROPIC_API_KEY` to get AI-written summaries — document text is then sent to Anthropic.
- **E-signatures** are visual (drawn/typed/image), not certificate-based digital signatures.
- **Text editing** rewrites a whole line in Helvetica; original embedded fonts aren't reused. Rotated pages: edits are positioned correctly but newly inserted text isn't rotated.
- Redaction is permanent. Highlights/notes/boxes are real PDF annotations that open in other readers.
- Protect makes an AES-256 password-protected *copy* (download); the working copy stays editable.
- Users see their own and shared documents; admins can open any. Uploaded files live in `data/docs` — add a cleanup job per your retention policy.
