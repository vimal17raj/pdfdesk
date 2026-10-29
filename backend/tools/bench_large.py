"""Make a big scanned-style PDF of any size and time what PDFDesk does with it.

  python tools/bench_large.py --gb 20 --url http://SERVER:8000 --user alice --password ...   # needs ~2x the size free
  python tools/bench_large.py --gb 1 --url http://localhost:8000 --user admin --password ... --keep

It writes the PDF straight to disk (never held in memory), uploads it the same way the browser does, then times:
opening, rendering first/middle/last page, searching, an edit, undo/redo and a streamed download.
The invisible text layer on every page ("Archive page N reference REFnnnnn status ok") gives Find something to match.
Use a throw-away account; the document is left in that account's library (delete it from File -> Recent files).
"""
import argparse
import io
import os
import sys
import time

import httpx


def make_pdf(path: str, gb: float, jpeg_kb: int = 950):
    from PIL import Image
    im = Image.frombytes("RGB", (900, 1200), os.urandom(900 * 1200 * 3))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=90)
    base = b.getvalue()
    n = max(2, int(gb * 2**30 / (len(base) + 400)))
    offs, t = {}, time.time()
    with open(path, "wb") as f:
        def obj(k, body):
            offs[k] = f.tell()
            f.write(b"%d 0 obj\n" % k + body + b"\nendobj\n")
        f.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
        obj(1, b"<</Type/Catalog/Pages 2 0 R>>")
        obj(3, b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
        for k in range(n):
            page, cont, img = 4 + 3 * k, 5 + 3 * k, 6 + 3 * k
            data = base + os.urandom(16)  # every page's image differs, like a real scan
            offs[img] = f.tell()
            f.write(b"%d 0 obj\n<</Type/XObject/Subtype/Image/Width 900/Height 1200/ColorSpace/DeviceRGB/BitsPerComponent 8"
                    b"/Filter/DCTDecode/Length %d>>\nstream\n" % (img, len(data)) + data + b"\nendstream\nendobj\n")
            text = b"q 595 0 0 842 0 0 cm /Im0 Do Q BT 3 Tr /F1 12 Tf 40 780 Td (Archive page %d reference REF%05d status ok) Tj ET" % (k + 1, k)
            obj(cont, b"<</Length %d>>\nstream\n" % len(text) + text + b"\nendstream")
            obj(page, b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]/Resources<</XObject<</Im0 %d 0 R>>/Font<</F1 3 0 R>>>>/Contents %d 0 R>>" % (img, cont))
            if k % 500 == 0:
                print(f"  writing page {k}/{n}  {time.time() - t:.0f}s", flush=True)
        obj(2, b"<</Type/Pages/Kids[%s]/Count %d>>" % (b" ".join(b"%d 0 R" % (4 + 3 * k) for k in range(n)), n))
        size, x = 3 * n + 4, f.tell()
        f.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
        for k in range(1, size):
            f.write(b"%010d 00000 n \n" % offs[k])
        f.write(b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (size, x))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gb", type=float, default=1.0)
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="")
    ap.add_argument("--file", default=os.path.join(os.path.expanduser("~"), "pdfdesk_bench.pdf"))
    ap.add_argument("--keep", action="store_true", help="keep the generated file")
    a = ap.parse_args()
    c = httpx.Client(timeout=None, base_url=a.url)

    print(f"making a {a.gb} GB PDF at {a.file}")
    n = make_pdf(a.file, a.gb)
    size = os.path.getsize(a.file)
    print(f"  {n} pages, {size / 2**30:.2f} GB")
    tok = c.post("/api/auth/login", json={"username": a.user, "password": a.password}).json().get("token")
    if not tok:
        sys.exit("login failed")
    H = {"Authorization": "Bearer " + tok}

    def tm(label, fn):
        t = time.time()
        r = fn()
        print(f"{label:42s} {time.time() - t:8.2f}s  {getattr(r, 'status_code', '')}", flush=True)
        return r

    def up():
        with open(a.file, "rb") as fh:
            return c.post("/api/docs/raw?name=bench.pdf", headers=H, content=iter(lambda: fh.read(1 << 20), b""))
    r = tm("upload (streamed)", up)
    d = r.json()
    i = d["id"]
    print(f"  pages={d['pages']}  big-file mode={d.get('huge')}")
    tm("open (page list)", lambda: c.get(f"/api/docs/{i}", headers=H))
    for p in (0, n // 2, n - 1):
        tm(f"render page {p + 1}", lambda p=p: c.get(f"/api/docs/{i}/page/{p}.png?zoom=1.5", headers=H))
    tm(f"search REF{n // 2:05d} (whole file)", lambda: c.get(f"/api/docs/{i}/search?q=REF{n // 2:05d}", headers=H))
    tm("edit: annotation", lambda: c.post(f"/api/docs/{i}/annotate", headers=H, json={"page": n // 3, "type": "rect", "rect": [50, 50, 200, 120]}))
    tm("undo", lambda: c.post(f"/api/docs/{i}/undo", headers=H))
    tm("redo", lambda: c.post(f"/api/docs/{i}/redo", headers=H))

    def dl():
        got = 0
        with c.stream("GET", f"/api/docs/{i}/download", headers=H) as resp:
            for chunk in resp.iter_bytes(1 << 20):
                got += len(chunk)
        return got
    t = time.time()
    got = dl()
    print(f"{'download (streamed)':42s} {time.time() - t:8.2f}s  {'OK' if got >= size else 'SIZE MISMATCH'}")
    c.delete(f"/api/docs/{i}", headers=H)
    if not a.keep:
        os.remove(a.file)


if __name__ == "__main__":
    main()
