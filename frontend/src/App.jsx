import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, download, fetchBlob, getToken, setToken } from "./api.js";
import { TOOLS, ToolView, Audit, Users } from "./Tools.jsx";
import { goPage } from "./nav.js";

export default function App() {
  const [me, setMe] = useState(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    // Always ask: the Windows desktop app needs no login and answers even without a token
    api.me().then(setMe).catch(() => setToken("")).finally(() => setChecked(true));
  }, []);

  if (!checked) return null;
  if (!me) return <Login onLogin={() => api.me().then(setMe)} />;
  return <Shell me={me} reloadMe={() => api.me().then(setMe)} onLogout={() => { setToken(""); setMe(null); }} />;
}

function Login({ onLogin }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try {
      const r = await api.login(u, p);
      setToken(r.token);
      onLogin();
    } catch (x) {
      setErr(x.message);
    }
  };
  return (
    <div className="login">
      <form onSubmit={submit} className="card">
        <h1>PDFDesk</h1>
        <p className="muted">Sign in with your company account</p>
        <input placeholder="Username" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
        <input placeholder="Password" type="password" value={p} onChange={(e) => setP(e.target.value)} />
        {err && <div className="error">{err}</div>}
        <button className="primary">Sign in</button>
      </form>
    </div>
  );
}

const kb = (b) => (b >= 1073741824 ? (b / 1073741824).toFixed(2) + " GB" : b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
const ago = (t) => new Date(t * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
const blobToDataUrl = (blob) => new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(blob); });
const imgSize = (url) => new Promise((res) => { const i = new Image(); i.onload = () => res([i.naturalWidth, i.naturalHeight]); i.src = url; });

function Shell({ me, reloadMe, onLogout }) {
  // Windows desktop app: tell the program this window is still open (it quits when the pings stop)
  useEffect(() => {
    if (!me.desktop) return;
    const ping = () => fetch("/api/ping").catch(() => {});
    ping();
    const t = setInterval(ping, 5000);
    return () => clearInterval(t);
  }, [me.desktop]);
  const [doc, setDoc] = useState(null);
  const [tool, setTool] = useState("view");
  const [zoom, setZoom] = useState(1.2);
  const [busy, setBusy] = useState("");
  const [up, setUp] = useState(null); // upload in progress: {name, loaded, total, ctl}
  const [toast, setToast] = useState(null);
  const [dialog, setDialog] = useState(null); // recent | share | props
  const [recent, setRecent] = useState([]);
  const [sq, setSq] = useState(""); // search box text
  const [searched, setSearched] = useState(""); // the text the current results are for
  const [searching, setSearching] = useState(false);
  const [findTick, setFindTick] = useState(0); // bumped to open the Search panel on the right
  const [hits, setHits] = useState([]);
  const [hitIdx, setHitIdx] = useState(0);
  const [pending, setPending] = useState(null); // pasted content waiting to be placed
  const handle = useRef(null); // File System Access handle of the file opened from disk
  const picker = useRef();

  const say = (msg, bad) => {
    setToast({ msg, bad });
    setTimeout(() => setToast(null), bad ? 6000 : 3000);
  };
  const run = async (label, fn) => {
    setBusy(label);
    try {
      return await fn();
    } catch (e) {
      say(e.message, true);
    } finally {
      setBusy("");
    }
  };
  const load = useCallback(async (id) => {
    const d = await api.doc(id);
    setDoc(d);
    return d;
  }, []);
  const act = (label, fn, okMsg) =>
    run(label, async () => {
      const info = await fn();
      if (info?.id) await load(info.id);
      if (okMsg) say(typeof okMsg === "function" ? okMsg(info) : okMsg);
      return info;
    });
  const openDoc = async (id, toolId) => {
    await load(id);
    handle.current = null;
    setHits([]); setSq(""); setSearched("");
    if (toolId) setTool(toolId);
    else if (!TOOLS.find((t) => t.id === tool)?.needsDoc) setTool("view"); // open for viewing first; pick a tool later
  };

  const refreshRecent = () => api.recent().then(setRecent).catch(() => {});
  useEffect(() => { refreshRecent(); }, [doc?.id, doc?.rev]);
  useEffect(() => {
    const m = location.hash.match(/doc=([a-z0-9]+)/);
    if (m) run("Opening…", () => openDoc(m[1]));
  }, []);

  // ---------- File menu actions ----------
  // Files of any size: the browser streams the file from disk straight to the server (with a progress bar)
  const openFile = async (file, h = null) => {
    const ctl = {};
    const send = async (pw) => {
      setUp({ name: file.name, loaded: 0, total: file.size, ctl });
      try {
        return await api.upload(file, pw, (loaded, total) => setUp((s) => s && { ...s, loaded, total }), ctl);
      } finally {
        setUp(null);
      }
    };
    try {
      let info;
      try {
        info = await send("");
      } catch (e) {
        if (e.message !== "PASSWORD_REQUIRED") throw e;
        const pw = prompt(`"${file.name}" is password protected. Enter the password:`);
        if (pw === null) return;
        info = await send(pw);
      }
      setBusy("Opening…");
      await openDoc(info.id);
      handle.current = h;
    } catch (e) {
      if (e.message !== "Upload cancelled") say(e.message, true);
    } finally {
      setBusy("");
    }
  };

  // Windows desktop app: the standard Open dialog; big files are used where they are, nothing is copied
  const openLocal = () =>
    run("Opening…", async () => {
      let r = await api.desktopOpen(null);
      if (r.cancelled) return;
      if (r.password_required) {
        const pw = prompt(`"${r.name}" is password protected. Enter the password:`);
        if (pw === null) return;
        r = await api.desktopOpen(r.path, pw);
      }
      await openDoc(r.id);
    });

  const openPicker = async () => {
    if (me.desktop) return openLocal();
    if (window.showOpenFilePicker) {
      try {
        const [h] = await window.showOpenFilePicker({ types: [{ description: "PDF or image", accept: { "application/pdf": [".pdf"], "image/*": [".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"] } }] });
        return openFile(await h.getFile(), h);
      } catch (e) { if (e.name === "AbortError") return; }
    }
    picker.current.click();
  };

  const currentBlob = () => fetchBlob(`/api/docs/${doc.id}/download`);
  const BIG = 300 * 1024 * 1024; // above this, never build the whole file in browser memory

  // streams the document from the server straight into a file on disk (works for any size)
  const streamTo = async (writable) => {
    const r = await fetch(`/api/docs/${doc.id}/download`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!r.ok) throw new Error("Download failed");
    await r.body.pipeTo(writable);
  };

  const saveAs = () =>
    run("Saving…", async () => {
      if (window.showSaveFilePicker) {
        try {
          const h = await window.showSaveFilePicker({ suggestedName: doc.name.replace(/\.[^.]+$/, "") + ".pdf", types: [{ description: "PDF", accept: { "application/pdf": [".pdf"] } }] });
          await streamTo(await h.createWritable());
          handle.current = h;
          return say("Saved " + h.name);
        } catch (e) { if (e.name === "AbortError") return; throw e; }
      }
      if (doc.size > BIG) { // plain link: the browser saves it to disk itself, piece by piece
        const a = document.createElement("a");
        a.href = `/api/docs/${doc.id}/download?token=${getToken()}`;
        a.download = doc.name;
        a.click();
        return say("Download started — see your browser's downloads");
      }
      await download(`/api/docs/${doc.id}/download`, "GET", null, doc.name);
    });

  const save = () => {
    if (!handle.current) return saveAs();
    return run("Saving…", async () => {
      await streamTo(await handle.current.createWritable());
      say("Saved to " + handle.current.name);
    });
  };

  const print = () =>
    run("Preparing to print…", async () => {
      if (doc.size > BIG) return say("This file is too large to print in one go. Use Organize pages → Extract to make a smaller PDF of the pages you need, then print that.", true);
      const url = URL.createObjectURL(await currentBlob());
      const f = document.createElement("iframe");
      f.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
      f.src = url;
      f.onload = () => { f.contentWindow.focus(); f.contentWindow.print(); setTimeout(() => { f.remove(); URL.revokeObjectURL(url); }, 60000); };
      document.body.appendChild(f);
    });

  const closeDoc = () => { setDoc(null); setHits([]); setSq(""); setSearched(""); handle.current = null; history.replaceState(null, "", location.pathname); };

  // ---------- Edit menu actions ----------
  const undo = () => doc && doc.version > 1 && act("Undo…", () => api.undo(doc.id));
  const redo = () => doc && doc.version < doc.top && act("Redo…", () => api.redo(doc.id));
  const curPage = () => {
    const el = document.querySelector(".pages");
    if (!el) return 0;
    const mid = el.scrollTop + el.clientHeight / 3;
    const idx = [...el.children].findIndex((k) => k.offsetTop - el.offsetTop + k.offsetHeight > mid);
    return Math.max(0, idx);
  };
  const copyText = (all) =>
    run("Copying…", async () => {
      if (all) {
        const t = await (await fetch(`/api/docs/${doc.id}/export?fmt=txt`, { headers: { Authorization: `Bearer ${getToken()}` } })).text();
        await navigator.clipboard.writeText(t);
      } else {
        const l = await api.lines(doc.id, curPage());
        await navigator.clipboard.writeText(l.map((x) => x.text).join("\n"));
      }
      say("Text copied to clipboard");
    });
  const snapshot = () =>
    run("Copying page image…", async () => {
      const img = await (await fetch(api.imgUrl(doc.id, curPage(), 2, doc.rev))).blob();
      await navigator.clipboard.write([new ClipboardItem({ "image/png": img })]);
      say("Page image copied — paste it into Word, Teams, e-mail…");
    });
  const beginPaste = async (items) => {
    if (!doc) return say("Open a PDF first", true);
    if (items.image) {
      const url = await blobToDataUrl(items.image);
      const [w, h] = await imgSize(url);
      const k = Math.min(1, 300 / w);
      setPending({ kind: "image", img: url, w: w * k, h: h * k });
    } else if (items.text?.trim()) {
      setPending({ kind: "text", text: items.text.trim().slice(0, 500), w: Math.min(260, 12 + items.text.length * 6), h: 20 });
    } else return say("Nothing to paste", true);
    if (!["edit", "sign", "comment", "stamp", "form", "summary", "ocr", "protect", "compress", "export"].includes(tool) || tool === "organize") setTool("edit");
    say("Click on the page where you want to place it (Esc to cancel)");
  };
  const paste = async () => {
    try {
      for (const it of await navigator.clipboard.read()) {
        const t = it.types.find((x) => x.startsWith("image/"));
        if (t) return beginPaste({ image: await it.getType(t) });
      }
      beginPaste({ text: await navigator.clipboard.readText() });
    } catch {
      say("Browser blocked clipboard access — press Ctrl+V on the page instead", true);
    }
  };
  const placePending = (i, x, y) => {
    const p = pending;
    setPending(null);
    if (!p) return;
    if (p.kind === "image")
      act("Pasting…", () => api.sign(doc.id, { page: i, rect: [x - p.w / 2, y - p.h / 2, x + p.w / 2, y + p.h / 2], image: p.img }));
    else
      act("Pasting…", () => api.annotate(doc.id, { page: i, type: "text", rect: [x, y, x + p.w + 20, y + 22], text: p.text, size: 12 }));
  };
  const pageOp = (op, extra = {}) => act("Updating pages…", () => api.pages(doc.id, { op, pages: [curPage()], ...extra }));
  const startFind = () => {
    if (!doc) return;
    if (["organize", "create", "combine", "compare", "audit", "users"].includes(tool)) setTool("view"); // screens without a page viewer
    setFindTick((t) => t + 1);
  };

  useEffect(() => {
    const k = (e) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName);
      if (e.key === "Escape") { setPending(null); }
      if (!e.ctrlKey && !e.metaKey) return;
      const key = e.key.toLowerCase();
      if (key === "o") { e.preventDefault(); openPicker(); }
      else if (key === "s" && doc) { e.preventDefault(); e.shiftKey ? saveAs() : save(); }
      else if (key === "p" && doc) { e.preventDefault(); print(); }
      else if (key === "f" && doc) { e.preventDefault(); startFind(); }
      else if (key === "z" && !typing) { e.preventDefault(); undo(); }
      else if (key === "y" && !typing) { e.preventDefault(); redo(); }
    };
    const onPaste = (e) => {
      if (["INPUT", "TEXTAREA"].includes(e.target.tagName) || !doc) return;
      const img = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
      e.preventDefault();
      beginPaste(img ? { image: img.getAsFile() } : { text: e.clipboardData.getData("text") });
    };
    window.addEventListener("keydown", k);
    window.addEventListener("paste", onPaste);
    return () => { window.removeEventListener("keydown", k); window.removeEventListener("paste", onPaste); };
  });

  const showHit = (i, list = hits) => {
    setHitIdx(i);
    // Pages outside the current window are created after the viewer handles this event.
    // Wait for the match marker itself instead of guessing how long rendering will take.
    const hit = list[i];
    if (!hit) return;
    goPage(hit.page);
    let frames = 0;
    const reveal = () => {
      const target = document.getElementById("hit" + i);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
      else if (frames++ < 120) requestAnimationFrame(reveal);
    };
    requestAnimationFrame(reveal);
  };
  const doFind = async (q) => {
    if (!q.trim()) return clearFind();
    setSearching(true);
    try {
      const h = (await api.search(doc.id, q)).map((x, i) => ({ ...x, gi: i }));
      setHits(h);
      setSearched(q);
      setHitIdx(0);
      if (h.length) showHit(0, h);
    } catch (e) {
      say(e.message, true);
    } finally {
      setSearching(false);
    }
  };
  const clearFind = () => { setHits([]); setSq(""); setSearched(""); setHitIdx(0); };
  const stepHit = (d) => {
    if (!hits.length) return;
    showHit((hitIdx + d + hits.length) % hits.length);
  };

  const dl = (path, name) => download(path, "GET", null, name).catch((e) => say(e.message, true));
  const stem = doc ? doc.name.replace(/\.[^.]+$/, "") : "";

  // ---------- menus ----------
  const has = !!doc;
  const MENUS = {
    "All tools": TOOLS.map((t) => ({ label: `${t.icon}   ${t.label}`, hint: tool === t.id ? "✓" : "", on: () => setTool(t.id) })),
    File: [
      { label: "Open…", key: "Ctrl+O", on: openPicker },
      { label: "Recent files…", on: () => { refreshRecent(); setDialog("recent"); } },
      ...recent.slice(0, 5).map((r) => ({ label: "   " + r.name, hint: kb(r.size), on: () => run("Opening…", () => openDoc(r.id)) })),
      { sep: true },
      { label: "Save", key: "Ctrl+S", on: save, off: !has },
      { label: "Save as…", key: "Ctrl+Shift+S", on: saveAs, off: !has },
      { label: "Print…", key: "Ctrl+P", on: print, off: !has },
      { sep: true },
      { label: "Undo", key: "Ctrl+Z", on: undo, off: !has || doc.version <= 1 },
      { label: "Redo", key: "Ctrl+Y", on: redo, off: !has || doc.version >= doc.top },
      { sep: true },
      { label: "Share…", on: () => setDialog("share"), off: !has, hide: me.desktop },
      { label: "Copy link to this document", hide: me.desktop, on: () => navigator.clipboard.writeText(`${location.origin}/#doc=${doc.id}`).then(() => say("Link copied (colleagues need access — use Share)")), off: !has },
      { label: "Document properties…", on: () => setDialog("props"), off: !has },
      { sep: true },
      { label: "Close file", on: closeDoc, off: !has },
      { sep: true },
      { label: "Change my password…", on: () => setDialog("password"), hide: me.desktop },
      { label: `Sign out (${me.user})`, on: onLogout, hide: me.desktop },
    ],
    Edit: [
      { label: "Undo", key: "Ctrl+Z", on: undo, off: !has || doc.version <= 1 },
      { label: "Redo", key: "Ctrl+Y", on: redo, off: !has || doc.version >= doc.top },
      { sep: true },
      { label: "Copy text of this page", on: () => copyText(false), off: !has },
      { label: "Copy all text", on: () => copyText(true), off: !has },
      { label: "Take a snapshot (copy page as image)", on: snapshot, off: !has },
      { label: "Paste image / text onto page", key: "Ctrl+V", on: paste, off: !has },
      { sep: true },
      { label: "Edit text & images", on: () => has && setTool("edit"), off: !has },
      { label: "Rotate page clockwise", on: () => pageOp("rotate", { angle: 90 }), off: !has },
      { label: "Rotate page counter-clockwise", on: () => pageOp("rotate", { angle: 270 }), off: !has },
      { label: "Insert blank page after this one", on: () => pageOp("blank"), off: !has },
      { label: "Delete this page", on: () => confirm("Delete this page?") && pageOp("delete"), off: !has || doc.pages.length < 2 },
      { sep: true },
      { label: "Find…", key: "Ctrl+F", on: startFind, off: !has },
      { label: "Organize pages…", on: () => setTool("organize"), off: !has },
    ],
    Convert: [
      { label: "PDF → Word (.docx)", on: () => dl(`/api/docs/${doc.id}/export?fmt=docx`, stem + ".docx"), off: !has },
      { label: "PDF → Excel (.xlsx)", on: () => dl(`/api/docs/${doc.id}/export?fmt=xlsx`, stem + ".xlsx"), off: !has },
      { label: "PDF → Text (.txt)", on: () => dl(`/api/docs/${doc.id}/export?fmt=txt`, stem + ".txt"), off: !has },
      { label: "PDF → Web page (.html)", on: () => dl(`/api/docs/${doc.id}/export?fmt=html`, stem + ".html"), off: !has },
      { label: "PDF → Images (PNG, zip)", on: () => dl(`/api/docs/${doc.id}/export?fmt=png&dpi=150`, stem + "_png.zip"), off: !has },
      { label: "PDF → Images (JPG, zip)", on: () => dl(`/api/docs/${doc.id}/export?fmt=jpg&dpi=150`, stem + "_jpg.zip"), off: !has },
      { sep: true },
      { label: "Images / text / blank → PDF…", on: () => setTool("create") },
      { label: "Scanned PDF → searchable (OCR)…", on: () => has ? setTool("ocr") : say("Open a PDF first", true) },
      { label: "Compress…", on: () => has ? setTool("compress") : say("Open a PDF first", true) },
      { label: "Password-protect…", on: () => has ? setTool("protect") : say("Open a PDF first", true) },
    ],
  };

  const search = { q: sq, setQ: setSq, searched, searching, hits, hitIdx, doFind, stepHit, showHit, clear: clearFind, tick: findTick };
  const ctx = { me, doc, act, run, say, load, openDoc, zoom, setZoom, hits, hitIdx, setHits, setTool, openFile, openPicker, pending, placePending, tool, search };
  const cur = TOOLS.find((t) => t.id === tool);

  return (
    <div className="shell">
      <header>
        <b className="logo" onClick={() => setTool("view")}>PDFDesk</b>
        <MenuBar menus={MENUS} />
        <div className="qa">
          <button title="Undo (Ctrl+Z)" disabled={!has || doc.version <= 1} onClick={undo}>↶</button>
          <button title="Redo (Ctrl+Y)" disabled={!has || doc.version >= doc.top} onClick={redo}>↷</button>
          <button title="Save (Ctrl+S)" disabled={!has} onClick={save}>💾</button>
          <button title="Paste image / text onto page (Ctrl+V)" disabled={!has} onClick={paste}>📋</button>
          <button title="Print (Ctrl+P)" disabled={!has} onClick={print}>🖨</button>
          <button title="Find (Ctrl+F)" disabled={!has} onClick={startFind}>🔍</button>
        </div>
        <span className="grow" />
        {doc && <span className="muted docname" title={doc.name}>{doc.name}</span>}
        {doc && <button className="primary" onClick={save}>Save</button>}
        {me.role === "admin" && !me.desktop && <button className={tool === "users" ? "active" : ""} onClick={() => setTool("users")}>Users</button>}
        {me.role === "admin" && !me.desktop && <button className={tool === "audit" ? "active" : ""} onClick={() => setTool("audit")}>Audit log</button>}
        {!me.desktop && <span className="muted" title={`Signed in as ${me.user} (${me.role})`}>{me.user} · {me.role}</span>}
      </header>
      {pending && <div className="findbar pastebar">Click on the page to place the pasted {pending.kind}. <button onClick={() => setPending(null)}>Cancel</button></div>}
      <div className="main">
        <section className="stage">
          {tool === "audit" && me.role === "admin" ? <Audit />
            : tool === "users" && me.role === "admin" ? <Users me={me} say={say} run={run} />
            : <ToolView tool={cur} ctx={ctx} />}
        </section>
      </div>
      <input ref={picker} type="file" hidden accept=".pdf,image/*" onChange={(e) => { const f = e.target.files[0]; e.target.value = ""; f && openFile(f); }} />
      {dialog === "recent" && <RecentDialog list={recent} close={() => setDialog(null)} refresh={refreshRecent}
        open={(id) => { setDialog(null); run("Opening…", () => openDoc(id)); }} run={run} say={say} />}
      {(dialog === "password" || me.must_change) && (
        <PasswordDialog forced={me.must_change} close={() => setDialog(null)} say={say}
          done={() => { setDialog(null); reloadMe(); }} onLogout={onLogout} />
      )}
      {dialog === "share" && doc && <ShareDialog doc={doc} close={() => setDialog(null)} say={say} />}
      {dialog === "props" && doc && (
        <Modal title="Document properties" close={() => setDialog(null)}>
          <table className="props"><tbody>
            <tr><td>File name</td><td>{doc.name}</td></tr>
            <tr><td>Pages</td><td>{doc.pages.length}</td></tr>
            <tr><td>Size</td><td>{kb(doc.size)}</td></tr>
            <tr><td>Page size</td><td>{Math.round(doc.pages[0].w)} × {Math.round(doc.pages[0].h)} pt</td></tr>
            <tr><td>Owner</td><td>{doc.owner}</td></tr>
            <tr><td>Version</td><td>{doc.version} of {doc.top} (undo history)</td></tr>
          </tbody></table>
        </Modal>
      )}
      {up && (
        <div className="busy">
          <div className="uploadbox">
            <b>{up.loaded >= up.total ? "Checking the file…" : "Uploading"} {up.name}</b>
            <div className="bar"><div style={{ width: (up.total ? (up.loaded / up.total) * 100 : 0) + "%" }} /></div>
            <span>{kb(up.loaded)} of {kb(up.total)} · {up.total ? Math.round((up.loaded / up.total) * 100) : 0}%</span>
            <button onClick={() => up.ctl.abort?.()}>Cancel</button>
          </div>
        </div>
      )}
      {busy && !up && <div className="busy"><div className="spin" />{busy}</div>}
      {toast && <div className={"toast" + (toast.bad ? " bad" : "")}>{toast.msg}</div>}
    </div>
  );
}

// drop hidden items, then any separator that ended up first, last or doubled
function tidy(items) {
  const out = [];
  for (const it of items.filter((i) => !i.hide)) {
    if (it.sep && (!out.length || out[out.length - 1].sep)) continue;
    out.push(it);
  }
  if (out.length && out[out.length - 1].sep) out.pop();
  return out;
}

function MenuBar({ menus }) {
  const [open, setOpen] = useState(null);
  const ref = useRef();
  useEffect(() => {
    const away = (e) => { if (!ref.current?.contains(e.target)) setOpen(null); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, []);
  return (
    <div className="menubar" ref={ref}>
      {Object.entries(menus).map(([name, items]) => (
        <div key={name} className="menu">
          <button className={open === name ? "active" : ""} onClick={() => setOpen(open === name ? null : name)}
            onMouseEnter={() => open && setOpen(name)}>{name}</button>
          {open === name && (
            <div className="dropdown">
              {tidy(items).map((it, k) => it.sep ? <hr key={k} /> : (
                <button key={k} disabled={it.off} onClick={() => { setOpen(null); it.on(); }}>
                  <span>{it.label}</span><span className="muted">{it.key || it.hint || ""}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function Modal({ title, close, children }) {
  return (
    <div className="modalbg" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="modal card">
        <div className="mhead"><b>{title}</b><button onClick={close}>✕</button></div>
        {children}
      </div>
    </div>
  );
}

function RecentDialog({ list, close, open, refresh, run, say }) {
  const [q, setQ] = useState("");
  const rows = list.filter((r) => r.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <Modal title="Recent files" close={close}>
      <input placeholder="Filter by name" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
      <div className="recent">
        {rows.length === 0 && <p className="muted">No files yet.</p>}
        {rows.map((r) => (
          <div key={r.id} className="rrow">
            <a onClick={() => open(r.id)}>{r.name}</a>
            <span className="muted">{r.shared ? `shared by ${r.owner} · ` : ""}{kb(r.size)} · {ago(r.updated)}</span>
            {!r.shared && <button title="Remove from your library" onClick={() => confirm(`Remove "${r.name}" from PDFDesk?`) &&
              run("Removing…", async () => { await fetch(`/api/docs/${r.id}`, { method: "DELETE", headers: { Authorization: `Bearer ${getToken()}` } }); refresh(); say("Removed"); })}>🗑</button>}
          </div>
        ))}
      </div>
    </Modal>
  );
}

function PasswordDialog({ forced, close, done, say, onLogout }) {
  const [v, setV] = useState({ old: "", nw: "", again: "" });
  const bad = v.nw.length < 8 ? "At least 8 characters" : v.nw !== v.again ? "Passwords don't match" : v.nw === v.old ? "Choose a different password" : "";
  const submit = (e) => {
    e.preventDefault();
    if (bad) return;
    api.changePassword(v.old, v.nw).then(() => { say("Password changed"); done(); }).catch((x) => say(x.message, true));
  };
  return (
    <div className="modalbg">
      <form className="modal card" onSubmit={submit}>
        <div className="mhead"><b>{forced ? "Choose a new password to continue" : "Change my password"}</b>{!forced && <button type="button" onClick={close}>✕</button>}</div>
        {forced && <p className="muted">Your password was set by an administrator. Pick your own before using PDFDesk.</p>}
        <input type="password" placeholder="Current password" value={v.old} onChange={(e) => setV({ ...v, old: e.target.value })} autoFocus autoComplete="current-password" />
        <input type="password" placeholder="New password (min 8 characters)" value={v.nw} onChange={(e) => setV({ ...v, nw: e.target.value })} autoComplete="new-password" />
        <input type="password" placeholder="New password again" value={v.again} onChange={(e) => setV({ ...v, again: e.target.value })} autoComplete="new-password" />
        {v.nw && bad && <span className="error">{bad}</span>}
        <div className="row"><button className="primary" disabled={!v.old || !!bad}>Change password</button>
          {forced && <button type="button" onClick={onLogout}>Sign out</button>}</div>
      </form>
    </div>
  );
}

function ShareDialog({ doc, close, say }) {
  const [u, setU] = useState("");
  const [names, setNames] = useState([]);
  useEffect(() => { api.userNames().then(setNames).catch(() => {}); }, []);
  const link = `${location.origin}/#doc=${doc.id}`;
  return (
    <Modal title={`Share "${doc.name}"`} close={close}>
      <p className="muted">Give a colleague access. They sign in with their own account and open it from Recent files or the link.</p>
      <div className="row">
        <input list="usernames" placeholder="Colleague's username" value={u} onChange={(e) => setU(e.target.value)} autoFocus />
        <datalist id="usernames">{names.map((n) => <option key={n} value={n} />)}</datalist>
        <button className="primary" disabled={!u.trim()} onClick={() => api.share(doc.id, u).then((r) => say(`Shared with ${r.shared.join(", ")}`)).catch((e) => say(e.message, true))}>Share</button>
      </div>
      <div className="row">
        <input readOnly value={link} onFocus={(e) => e.target.select()} />
        <button onClick={() => navigator.clipboard.writeText(link).then(() => say("Link copied"))}>Copy link</button>
      </div>
      <p className="muted">Shared people can edit the same document. Use Save as… to send someone their own copy.</p>
    </Modal>
  );
}
