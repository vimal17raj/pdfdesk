import React, { useEffect, useRef, useState } from "react";
import { api, download } from "./api.js";
import Viewer from "./Viewer.jsx";
import { goPage } from "./nav.js";

export const TOOLS = [
  { id: "view", label: "View & search", icon: "👁", needsDoc: true },
  { id: "export", label: "Export a PDF", icon: "⤓", needsDoc: true },
  { id: "edit", label: "Edit a PDF", icon: "✎", needsDoc: true },
  { id: "create", label: "Create a PDF", icon: "＋" },
  { id: "combine", label: "Combine files", icon: "⧉" },
  { id: "organize", label: "Organize pages", icon: "▦", needsDoc: true },
  { id: "summary", label: "Generative summary", icon: "✦", needsDoc: true },
  { id: "sign", label: "Fill & e-signatures", icon: "✍", needsDoc: true },
  { id: "ocr", label: "Scan & OCR", icon: "⌕", needsDoc: true },
  { id: "protect", label: "Protect a PDF", icon: "🔒", needsDoc: true },
  { id: "compress", label: "Compress a PDF", icon: "⇲", needsDoc: true },
  { id: "form", label: "Prepare a form", icon: "☑", needsDoc: true },
  { id: "comment", label: "Add comments", icon: "💬", needsDoc: true },
  { id: "stamp", label: "Add a stamp", icon: "◉", needsDoc: true },
  { id: "compare", label: "Compare files", icon: "⇄" },
];

const kb = (b) => (b >= 1073741824 ? (b / 1073741824).toFixed(2) + " GB" : b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB");
const today = () => new Date().toISOString().slice(0, 10);
const swap = (a, i, j) => { const c = [...a]; [c[i], c[j]] = [c[j], c[i]]; return c; };

export function ToolView({ tool, ctx }) {
  if (!tool) return null;
  if (tool.id === "create") return <Create ctx={ctx} />;
  if (tool.id === "combine") return <Combine ctx={ctx} />;
  if (tool.id === "compare") return <Compare ctx={ctx} />;
  if (!ctx.doc) return <OpenPrompt ctx={ctx} tool={tool} />;
  if (tool.id === "organize") return <Organize ctx={ctx} />;
  return <Workspace tool={tool} ctx={ctx} />;
}

function Head({ tool, children }) {
  return <div className="thead"><h2><span className="ico">{tool.icon}</span> {tool.label}</h2><p className="muted">{children}</p></div>;
}

function OpenPrompt({ ctx, tool }) {
  const [drag, setDrag] = useState(false);
  return (
    <div className="center">
      <div className={"drop" + (drag ? " over" : "")}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); e.dataTransfer.files[0] && ctx.openFile(e.dataTransfer.files[0]); }}
        onClick={ctx.openPicker}>
        <h2>{tool.id === "view" ? "Open a PDF" : tool.label}</h2>
        <p>Drop a PDF (or a scan / photo) here, or click to choose a file.</p>
        <p className="muted">{tool.id === "view" ? "It opens for viewing and searching only. Choose what to do with it later from All tools — edit, OCR, convert, protect…" : "Or use File → Open / Recent files. Ctrl+O"}</p>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Workspace (viewer + side panel)
function Workspace({ tool, ctx }) {
  const { doc, act, say, zoom, setZoom, hits, pending } = ctx;
  const id = tool.id;
  const [etool, setEtool] = useState("select");
  const [ctool, setCtool] = useState("note");
  const [item, setItem] = useState("signature");
  const [sig, setSig] = useState(() => { try { return JSON.parse(localStorage.getItem("pdfdesk_sig") || "null"); } catch { return null; } });
  const [ini, setIni] = useState(() => { try { return JSON.parse(localStorage.getItem("pdfdesk_ini") || "null"); } catch { return null; } });
  const [freeText, setFreeText] = useState("");
  const [stampCfg, setStampCfg] = useState({ text: "APPROVED", color: [0, 0.5, 0], size: 26, date: true, all: false });
  const [formCfg, setFormCfg] = useState({ type: "text", name: "", options: "" });
  const [fields, setFields] = useState([]);
  const [values, setValues] = useState({});
  const [armed, setArmed] = useState(true);
  const [sideOpen, setSideOpen] = useState(true);
  useEffect(() => { setSideOpen(true); }, [id]);
  const [formMode, setFormMode] = useState("add");
  useEffect(() => { setArmed(true); }, [id, item]);

  useEffect(() => {
    if (id !== "sign" && id !== "form") return;
    api.fields(doc.id).then((f) => {
      setFields(f);
      setValues(Object.fromEntries(f.map((x) => [x.name, x.value])));
    }).catch(() => {});
  }, [id, doc.id, doc.rev]);

  const saveSig = (k, v) => { (k === "sig" ? setSig : setIni)(v); try { localStorage.setItem(k === "sig" ? "pdfdesk_sig" : "pdfdesk_ini", JSON.stringify(v)); } catch {} };

  // what a click on the page does
  // `armed`: stamp / signature tools place one object per arming, then drop back to Select so it can be moved or deleted
  let mode = "none", onPlace, onRect, ghost, askText = false, fillUI = null, fieldBoxes = null, fieldObjs = null;
  const showLines = id === "edit" && etool === "select";
  if (id === "view") mode = "view"; // read-only: nothing can be selected, clicked into or changed by accident
  if (id === "edit") mode = etool;
  if (id === "comment") { mode = ctool; askText = ["highlight", "underline", "strike"].includes(ctool); }
  if (id === "sign") {
    if (fields.length) fillUI = { fields, values, setValue: (k, v) => setValues((s) => ({ ...s, [k]: v })) };
    const img = item === "signature" ? sig : item === "initials" ? ini : null;
    if (armed && (img || ["date", "text", "x"].includes(item))) {
      mode = "place";
      const label = item === "date" ? today() : item === "x" ? "X" : freeText || "Text";
      if (img) {
        const w = item === "signature" ? 150 : 60;
        ghost = { img: img.url, w, h: w / img.ar };
        onPlace = (i, x, y) => { setArmed(false); act("Placing…", () => api.sign(doc.id, { page: i, rect: [x - w / 2, y - w / img.ar / 2, x + w / 2, y + w / img.ar / 2], image: img.url, label: item === "signature" ? "Signature" : "Initials" })); };
      } else {
        ghost = { label, w: Math.max(30, label.length * 7), h: 18 };
        onPlace = (i, x, y) => { setArmed(false); act("Placing…", () => api.annotate(doc.id, { page: i, type: "text", rect: [x, y - 9, x + ghost.w + 10, y + 12], text: label, size: 12 })); };
      }
    }
  }
  if (id === "stamp" && armed) {
    mode = "place";
    const t = stampCfg.text.toUpperCase() || "STAMP";
    ghost = { label: t, w: Math.max(80, t.length * stampCfg.size * 0.72), h: stampCfg.size * 1.6 };
    onPlace = (i, x, y) => { setArmed(false); act("Stamping…", () => api.stamp(doc.id, {
      pages: stampCfg.all ? doc.pages.map((_, k) => k) : [i], center: [x, y], text: t, color: stampCfg.color, size: stampCfg.size, date: stampCfg.date,
    }), "Stamp added — click it to move, resize or delete"); };
  }
  if (id === "form") {
    if (formMode === "add") {
      mode = "rect-cb";
      fieldBoxes = fields;
      onRect = (i, rect) => act("Adding field…", () => api.addField(doc.id, {
        page: i, rect, type: formCfg.type, name: formCfg.name, options: formCfg.options.split("\n").filter(Boolean),
      }), "Field added");
    } else fieldObjs = fields;
  }
  if (!sideOpen && id !== "view") { mode = id === "edit" ? "select" : "none"; onPlace = onRect = ghost = undefined; fieldBoxes = null; }
  if (pending) {
    mode = "place";
    ghost = pending.kind === "image" ? { img: pending.img, w: pending.w, h: pending.h } : { label: pending.text, w: pending.w, h: pending.h };
    onPlace = ctx.placePending;
  }

  const p = { ctx, doc, act, say };
  return (
    <div className="work">
      <Viewer doc={doc} mode={mode} act={act} zoom={zoom} setZoom={setZoom} hits={hits} curHit={ctx.hitIdx} showLines={showLines}
        askText={askText} fillUI={fillUI} fieldBoxes={fieldBoxes} fieldObjs={fieldObjs} onPlace={onPlace} onRect={onRect} ghost={ghost} ctx={{ setTool: ctx.setTool, tool, search: ctx.search }} />
      {!sideOpen && id !== "view" && <button className="sidetab" title="Show tool options" onClick={() => setSideOpen(true)}>◂ {tool.label}</button>}
      {sideOpen && id !== "view" && <aside className="side">
        <button className="sideclose" title="Close this panel" onClick={() => setSideOpen(false)}>✕</button>
        <Head tool={tool}>{HELP[id]}</Head>
        {id === "export" && <ExportPanel {...p} />}
        {id === "edit" && <EditPanel {...p} etool={etool} setEtool={setEtool} />}
        {id === "summary" && <SummaryPanel {...p} />}
        {id === "sign" && <SignPanel {...p} item={item} setItem={setItem} sig={sig} ini={ini} saveSig={saveSig} freeText={freeText} setFreeText={setFreeText}
          fields={fields} values={values} armed={armed} setArmed={setArmed} />}
        {id === "ocr" && <OcrPanel {...p} />}
        {id === "protect" && <ProtectPanel {...p} />}
        {id === "compress" && <CompressPanel {...p} />}
        {id === "form" && <FormPanel {...p} cfg={formCfg} setCfg={setFormCfg} fields={fields} formMode={formMode} setFormMode={setFormMode} />}
        {id === "comment" && <CommentPanel {...p} ctool={ctool} setCtool={setCtool} />}
        {id === "stamp" && <StampPanel {...p} cfg={stampCfg} setCfg={setStampCfg} armed={armed} setArmed={setArmed} />}
      </aside>}
    </div>
  );
}

const HELP = {
  export: "Save this PDF as Excel, Word, text, web page or images.",
  edit: "Click a line of text to rewrite it, or pick a tool and mark up the page.",
  summary: "Get the key points of a long document in seconds.",
  sign: "Fill form fields, type text, add the date, or place your signature.",
  ocr: "Turn scanned pages into searchable, copyable text.",
  protect: "Add a password and limit printing, copying and editing.",
  compress: "Reduce the file size for e-mail or upload.",
  form: "Drag on the page to add text boxes, check boxes and drop-downs.",
  comment: "Notes, highlights and text comments that other readers can see.",
  stamp: "Click the page to place a stamp — Approved, Draft, Confidential…",
};

// ------------------------------------------------------------------ panels
function ExportPanel({ doc, say }) {
  const [dpi, setDpi] = useState(150);
  const stem = doc.name.replace(/\.[^.]+$/, "");
  const go = (fmt, name) => download(`/api/docs/${doc.id}/export?fmt=${fmt}&dpi=${dpi}`, "GET", null, name).catch((e) => say(e.message, true));
  return (
    <div className="stack">
      <button className="wide" onClick={() => go("docx", stem + ".docx")}>Microsoft Word (.docx)</button>
      <button className="wide" onClick={() => go("xlsx", stem + ".xlsx")}>Microsoft Excel (.xlsx)</button>
      <button className="wide" onClick={() => go("txt", stem + ".txt")}>Plain text (.txt)</button>
      <button className="wide" onClick={() => go("html", stem + ".html")}>Web page (.html)</button>
      <button className="wide" onClick={() => go("png", stem + "_png.zip")}>Images — PNG (zip)</button>
      <button className="wide" onClick={() => go("jpg", stem + "_jpg.zip")}>Images — JPG (zip)</button>
      <label>Image resolution
        <select value={dpi} onChange={(e) => setDpi(+e.target.value)}>
          <option value={100}>100 dpi (small)</option><option value={150}>150 dpi</option><option value={200}>200 dpi</option><option value={300}>300 dpi (print)</option>
        </select>
      </label>
      <p className="muted">Excel export puts each text block in a row with its page number. Scanned PDFs need OCR first to export text.</p>
    </div>
  );
}

const ETOOLS = [["select", "Select / Edit text"], ["text", "Add text"], ["highlight", "Highlight"], ["underline", "Underline"], ["strike", "Strike"],
  ["rect", "Box"], ["ellipse", "Ellipse"], ["ink", "Draw"], ["note", "Note"], ["redact", "Redact"]];

function EditPanel({ doc, act, say, ctx, etool, setEtool }) {
  const [wm, setWm] = useState("CONFIDENTIAL");
  const ap = useRef();
  return (
    <div className="stack">
      <div className="chips">
        {ETOOLS.map(([k, l]) => <button key={k} className={etool === k ? "active" : ""} onClick={() => setEtool(k)}>{l}</button>)}
      </div>
      <p className="muted">{etool === "select" ? "Click page text to rewrite it. Click any added object (stamp, signature, box, note, highlight…) to select it: drag to move, handles to resize, double-click to edit its text, Delete to remove."
        : etool === "redact" ? "Redaction permanently removes what's under the box." : "Drag on the page."}</p>
      <hr />
      <label>Watermark
        <div className="row"><input value={wm} onChange={(e) => setWm(e.target.value)} />
          <button onClick={() => act("Applying…", () => api.watermark(doc.id, wm), "Watermark added")}>Apply</button></div>
      </label>
      <button onClick={() => ap.current.click()}>Append other PDFs…</button>
      <input ref={ap} type="file" hidden multiple accept=".pdf" onChange={(e) => { const f = [...e.target.files]; e.target.value = ""; f.length && act("Merging…", () => api.append(doc.id, f), "Added at the end"); }} />
      <button onClick={() => ctx.setTool("organize")}>Organize pages…</button>
    </div>
  );
}

function SummaryPanel({ doc, say, ctx }) {
  const [len, setLen] = useState("medium");
  const [res, setRes] = useState(null);
  return (
    <div className="stack">
      <label>Length
        <select value={len} onChange={(e) => setLen(e.target.value)}>
          <option value="short">Short</option><option value="medium">Medium</option><option value="long">Detailed</option>
        </select>
      </label>
      <button className="primary" onClick={() => ctx.run("Summarising…", async () => setRes(await api.summary(doc.id, len)))}>Summarise this document</button>
      {res && (
        <>
          {res.notice && <div className="note warn">{res.notice}</div>}
          <pre className="summary">{res.summary}</pre>
          <div className="row"><button onClick={() => navigator.clipboard.writeText(res.summary).then(() => say("Copied"))}>Copy</button>
            <span className="muted">{res.words} words · {res.engine === "claude" ? "AI summary" : "offline key-sentence summary"}</span></div>
        </>
      )}
      {!ctx.me.ai_summary && <p className="muted">This server runs offline: the summary picks the most important sentences. Ask your admin to set ANTHROPIC_API_KEY for AI-written summaries (document text is then sent to Anthropic).</p>}
    </div>
  );
}

function SigPad({ onSave, label }) {
  const cv = useRef();
  const [tab, setTab] = useState("draw");
  const [typed, setTyped] = useState("");
  const [font, setFont] = useState("'Segoe Script','Brush Script MT',cursive");
  const drawing = useRef(false);
  const clear = () => { const c = cv.current; c.getContext("2d").clearRect(0, 0, c.width, c.height); };
  useEffect(() => { clear(); }, [tab]);
  const pos = (e) => { const r = cv.current.getBoundingClientRect(); const t = e.touches?.[0] || e; return [(t.clientX - r.left) * (cv.current.width / r.width), (t.clientY - r.top) * (cv.current.height / r.height)]; };
  const start = (e) => { if (tab !== "draw") return; drawing.current = true; const g = cv.current.getContext("2d"); g.lineWidth = 3; g.lineCap = "round"; g.strokeStyle = "#0a1a5c"; g.beginPath(); g.moveTo(...pos(e)); };
  const move = (e) => { if (!drawing.current) return; const g = cv.current.getContext("2d"); g.lineTo(...pos(e)); g.stroke(); };
  const end = () => { drawing.current = false; };
  useEffect(() => {
    if (tab !== "type") return;
    clear();
    const g = cv.current.getContext("2d");
    g.fillStyle = "#0a1a5c"; g.font = `64px ${font}`; g.textBaseline = "middle";
    g.fillText(typed, 20, 80, 460);
  }, [typed, font, tab]);
  const upload = (f) => {
    if (!f) return;
    const img = new Image();
    img.onload = () => { clear(); const g = cv.current.getContext("2d"); const k = Math.min(500 / img.width, 160 / img.height); g.drawImage(img, 0, 0, img.width * k, img.height * k); };
    img.src = URL.createObjectURL(f);
  };
  const save = () => {
    const c = cv.current, g = c.getContext("2d"), d = g.getImageData(0, 0, c.width, c.height).data;
    let x0 = c.width, y0 = c.height, x1 = 0, y1 = 0;
    for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++) if (d[(y * c.width + x) * 4 + 3] > 8) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    if (x1 <= x0) return;
    const w = x1 - x0 + 1, h = y1 - y0 + 1, o = document.createElement("canvas");
    o.width = w; o.height = h;
    o.getContext("2d").drawImage(c, x0, y0, w, h, 0, 0, w, h);
    onSave({ url: o.toDataURL("image/png"), ar: w / h });
  };
  return (
    <div className="stack">
      <div className="chips">{[["draw", "Draw"], ["type", "Type"], ["image", "Image"]].map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      <canvas ref={cv} width={500} height={160} className="sigpad" onMouseDown={start} onMouseMove={move} onMouseUp={end} onMouseLeave={end}
        onTouchStart={start} onTouchMove={(e) => { e.preventDefault(); move(e); }} onTouchEnd={end} />
      {tab === "type" && <div className="row"><input placeholder="Type your name" value={typed} onChange={(e) => setTyped(e.target.value)} />
        <select value={font} onChange={(e) => setFont(e.target.value)}>
          <option value="'Segoe Script','Brush Script MT',cursive">Script</option><option value="'Lucida Handwriting','Comic Sans MS',cursive">Handwriting</option><option value="Georgia,serif">Serif</option>
        </select></div>}
      {tab === "image" && <input type="file" accept="image/*" onChange={(e) => upload(e.target.files[0])} />}
      <div className="row"><button onClick={clear}>Clear</button><button className="primary" onClick={save}>Save {label}</button></div>
    </div>
  );
}

function SignPanel({ doc, act, say, item, setItem, sig, ini, saveSig, freeText, setFreeText, fields, values, armed, setArmed }) {
  const [making, setMaking] = useState(null); // 'sig' | 'ini'
  const fillable = fields.filter((f) => f.type !== "Signature");
  const apply = () => act("Saving form…", () => api.fill(doc.id, Object.fromEntries(fillable.map((f) => [f.name, values[f.name]]))), "Form filled");
  return (
    <div className="stack">
      {fillable.length > 0 ? (
        <>
          <div className="note">This PDF has {fillable.length} fillable field(s). Type directly on the page, then:</div>
          <button className="primary" onClick={apply}>Save filled form</button>
        </>
      ) : <p className="muted">No fillable fields in this PDF. Use the tools below to add text, a date or a signature anywhere. (Use "Prepare a form" to create fields.)</p>}
      <hr />
      <b>Place on the page</b>
      <div className="chips">
        {[["signature", "Signature"], ["initials", "Initials"], ["date", "Date"], ["text", "Text"], ["x", "✕ mark"]].map(([k, l]) => <button key={k} className={item === k ? "active" : ""} onClick={() => setItem(k)}>{l}</button>)}
      </div>
      {item === "text" && <input placeholder="Text to place" value={freeText} onChange={(e) => setFreeText(e.target.value)} />}
      {(item === "signature" || item === "initials") && (() => {
        const cur = item === "signature" ? sig : ini, k = item === "signature" ? "sig" : "ini";
        return making === k || !cur ? (
          <SigPad label={item} onSave={(v) => { saveSig(k, v); setMaking(null); say("Saved on this browser — now click the page to place it"); }} />
        ) : (
          <div className="stack"><img className="sigprev" src={cur.url} alt="" /><button onClick={() => setMaking(k)}>Change {item}</button></div>
        );
      })()}
      {(item === "signature" && sig) || (item === "initials" && ini) || ["date", "text", "x"].includes(item)
        ? <ArmBar armed={armed} setArmed={setArmed} what={item === "x" ? "mark" : item} /> : null}
      <p className="muted">Placed items are objects you can select, move, resize or delete (text items: double-click to edit). This is a visual signature, not a certificate-based digital signature.</p>
    </div>
  );
}

// Long operations (OCR, compress) run on the server in the background: start, poll, show progress, allow cancel.
function useJob(ctx, doc, say) {
  const [job, setJob] = useState(null);
  const timer = useRef();
  const stop = () => clearTimeout(timer.current);
  useEffect(() => stop, []);
  const poll = (id) => {
    timer.current = setTimeout(async () => {
      try {
        const j = await api.job(id);
        setJob(j);
        if (j.status === "running") return poll(id);
        if (j.status === "done") {
          if (j.doc_id && j.doc_id !== doc.id) await ctx.openDoc(j.doc_id);
          else await ctx.load(doc.id);
          if (j.message && j.message !== "Done") say(j.message);
        }
      } catch (e) {
        setJob({ status: "error", error: e.message });
      }
    }, 2000);
  };
  const start = async (fn) => {
    stop();
    setJob({ status: "running", elapsed: 0, starting: true });
    try {
      const j = await fn();
      setJob(j);
      poll(j.id);
    } catch (e) {
      setJob({ status: "error", error: e.message });
    }
  };
  const cancel = () => job?.id && api.cancelJob(job.id).catch(() => {});
  return { job, start, cancel, running: job?.status === "running" };
}

const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function JobStatus({ j, cancel, what }) {
  if (!j) return null;
  if (j.status === "running")
    return (
      <div className="note">
        <div className="row"><div className="spin small" /> <b>{what} running…</b> <span className="muted">{j.starting ? "starting" : mmss(j.elapsed)}</span></div>
        {j.progress != null && <div className="bar"><div style={{ width: j.progress + "%" }} /></div>}
        <div className="muted">Large files can take several minutes. You can keep reading, but wait before editing this document.</div>
        <button onClick={cancel}>Cancel</button>
      </div>
    );
  if (j.status === "cancelled") return <div className="note warn">Cancelled.</div>;
  if (j.status === "error") return <div className="note warn">Failed: {j.error}</div>;
  if (j.status === "done") return <div className="note ok">Finished in {mmss(j.elapsed)}.{j.message && j.message !== "Done" ? " " + j.message : ""}</div>;
  return null;
}

function OcrPanel({ doc, act, ctx, say }) {
  const { me, openDoc, run } = ctx;
  const [v, setV] = useState({ langs: me.ocr_langs, force: false, deskew: true, pages: "" });
  const { job, start, cancel, running } = useJob(ctx, doc, say);
  const sc = useRef();
  const scan = (list) => run("Importing scans…", async () => {
    const pdf = list.length === 1 && /\.pdf$/i.test(list[0].name);
    const info = pdf ? await api.upload(list[0]) : await api.createImages(list);
    await openDoc(info.id, "ocr");
    say("Imported — now press Run OCR");
  });
  return (
    <div className="stack">
      {!me.ocr && <div className="error">OCR engine is not installed on this server.</div>}
      <label>Languages<input value={v.langs} onChange={(e) => setV({ ...v, langs: e.target.value })} title="e.g. eng, eng+tam, eng+hin" /></label>
      <label className="chk"><input type="checkbox" checked={v.deskew} onChange={(e) => setV({ ...v, deskew: e.target.checked })} /> Straighten crooked pages</label>
      <label className="chk"><input type="checkbox" checked={v.force} onChange={(e) => setV({ ...v, force: e.target.checked })} /> Re-OCR pages that already have text</label>
      <label>Pages (blank = all {doc.pages.length})<input value={v.pages} placeholder="e.g. 1-50 or 1,3,7-9" onChange={(e) => setV({ ...v, pages: e.target.value })} /></label>
      {doc.pages.length > 100 && !v.pages.trim() && <div className="note warn">{doc.pages.length} pages is a long job — roughly a few seconds per page. Try a page range first (e.g. 1-20) to check the result.</div>}
      <button className="primary" disabled={!me.ocr || running} onClick={() => start(() => api.ocr(doc.id, v))}>Run OCR</button>
      <JobStatus j={job} cancel={cancel} what="OCR" />
      <hr />
      <b>Bring in scans</b>
      <button onClick={() => sc.current.click()}>Choose scanned images / PDF…</button>
      <input ref={sc} type="file" hidden multiple accept=".pdf,image/*" onChange={(e) => { const f = [...e.target.files]; e.target.value = ""; f.length && scan(f); }} />
      <p className="muted">Several photos or scans (JPG, PNG, TIFF) are combined into one PDF in the order selected. On a phone, choose "Take photo".</p>
    </div>
  );
}

function ProtectPanel({ doc, say }) {
  const [v, setV] = useState({ user_password: "", owner_password: "", allow_print: true, allow_copy: false, allow_edit: false });
  const set = (k) => (e) => setV({ ...v, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <div className="stack">
      <label>Password to open<input type="password" value={v.user_password} onChange={set("user_password")} autoComplete="new-password" /></label>
      <label>Owner password (to change permissions)<input type="password" value={v.owner_password} onChange={set("owner_password")} autoComplete="new-password" /></label>
      <b>Allow others to</b>
      <label className="chk"><input type="checkbox" checked={v.allow_print} onChange={set("allow_print")} /> Print</label>
      <label className="chk"><input type="checkbox" checked={v.allow_copy} onChange={set("allow_copy")} /> Copy text and images</label>
      <label className="chk"><input type="checkbox" checked={v.allow_edit} onChange={set("allow_edit")} /> Edit, annotate and fill</label>
      <button className="primary" disabled={!v.user_password && !v.owner_password}
        onClick={() => download(`/api/docs/${doc.id}/protect`, "POST", v, "protected.pdf").then(() => say("Protected copy downloaded")).catch((e) => say(e.message, true))}>
        Protect & download copy
      </button>
      <p className="muted">AES-256 encrypted copy. The PDF open here stays unprotected; use the downloaded copy to apply these settings. Print/copy restrictions are permission flags that compatible PDF readers may enforce, not DRM, and some tools can ignore them. Passwords are not stored — if lost they cannot be recovered.</p>
    </div>
  );
}

function CompressPanel({ doc, act, ctx, say }) {
  const [level, setLevel] = useState("medium");
  const { job, start, cancel, running } = useJob(ctx, doc, say);
  return (
    <div className="stack">
      <div className="note">Current size: <b>{kb(doc.size)}</b></div>
      <label className="chk"><input type="radio" checked={level === "low"} onChange={() => setLevel("low")} /> Low — best quality</label>
      <label className="chk"><input type="radio" checked={level === "medium"} onChange={() => setLevel("medium")} /> Medium — recommended</label>
      <label className="chk"><input type="radio" checked={level === "high"} onChange={() => setLevel("high")} /> High — smallest file</label>
      <label className="chk"><input type="radio" checked={level === "maximum"} onChange={() => setLevel("maximum")} /> Maximum — strongest reduction, lower image quality</label>
      <button className="primary" disabled={running} onClick={() => start(() => api.compress(doc.id, level))}>Compress</button>
      <JobStatus j={job} cancel={cancel} what="Compression" />
      {job?.status === "done" && job.before != null && <div className="note">{kb(job.before)} → <b>{kb(job.after)}</b>{job.after >= job.before ? " (already optimal)" : ` (${Math.round((1 - job.after / job.before) * 100)}% smaller)`}. Undo to restore.</div>}
    </div>
  );
}

function FormPanel({ cfg, setCfg, fields, ctx, formMode, setFormMode, act, doc }) {
  const rename = (f) => {
    const n = prompt("Field name", f.name);
    if (n && n !== f.name) act("Renaming…", () => api.updateField(doc.id, { name: f.name, new_name: n }));
  };
  return (
    <div className="stack">
      <div className="chips">
        <button className={formMode === "add" ? "active" : ""} onClick={() => setFormMode("add")}>Add field</button>
        <button className={formMode === "select" ? "active" : ""} onClick={() => setFormMode("select")}>Select / move / resize</button>
      </div>
      {formMode === "select" && <div className="note">Click a field to select it: drag to move, drag handles to resize, double-click to rename, Delete to remove.</div>}
      <label>Field type
        <select value={cfg.type} onChange={(e) => setCfg({ ...cfg, type: e.target.value })}>
          <option value="text">Text box</option><option value="checkbox">Check box</option><option value="dropdown">Drop-down list</option><option value="signature">Signature field</option>
        </select>
      </label>
      <label>Field name (optional)<input value={cfg.name} onChange={(e) => setCfg({ ...cfg, name: e.target.value })} placeholder="e.g. Employee name" /></label>
      {cfg.type === "dropdown" && <label>Choices (one per line)<textarea rows={4} value={cfg.options} onChange={(e) => setCfg({ ...cfg, options: e.target.value })} /></label>}
      {formMode === "add" && <div className="note">Drag a rectangle on the page to create the field.</div>}
      <b>Fields in this PDF ({fields.length})</b>
      <ul className="list">
        {fields.map((f) => (
          <li key={f.name}>
            <a onClick={() => goPage(f.page)}>{f.name}</a>
            <span className="muted">{f.type} · p{f.page + 1}</span>
            <span className="rowbtns">
              <button title="Rename" onClick={() => rename(f)}>✎</button>
              <button title="Delete field" onClick={() => confirm(`Delete field "${f.name}"?`) && act("Deleting…", () => api.deleteField(doc.id, { name: f.name }))}>🗑</button>
            </span>
          </li>
        ))}
      </ul>
      <button onClick={() => ctx.setTool("sign")}>Test: fill the form →</button>
    </div>
  );
}

function CommentPanel({ doc, ctool, setCtool, ctx }) {
  const [list, setList] = useState([]);
  useEffect(() => { api.comments(doc.id).then(setList).catch(() => {}); }, [doc.id, doc.rev]);
  return (
    <div className="stack">
      <div className="chips">
        {[["none", "Select / edit"], ["note", "Sticky note"], ["highlight", "Highlight"], ["underline", "Underline"], ["strike", "Strikeout"], ["text", "Text comment"]].map(([k, l]) =>
          <button key={k} className={ctool === k ? "active" : ""} onClick={() => setCtool(k)}>{l}</button>)}
      </div>
      <b>All comments ({list.length})</b>
      <ul className="list">
        {list.length === 0 && <li className="muted">No comments yet.</li>}
        {list.map((c) => (
          <li key={c.xref}>
            <a onClick={() => goPage(c.page)}>Page {c.page + 1} · {c.type}</a>
            <span>{c.text || <i className="muted">(no text)</i>}</span>
            <span className="muted">{c.author}</span>
            <button onClick={() => ctx.act("Deleting…", () => api.deleteAnnot(doc.id, { page: c.page, xref: c.xref }))}>✕</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ArmBar({ armed, setArmed, what }) {
  return armed
    ? <div className="note">Click on the page to place the {what}. <button onClick={() => setArmed(false)}>Cancel</button></div>
    : <div className="note ok">Placed. <b>Click it on the page</b> to select — then drag to move, drag the handles to resize, press Delete (or 🗑) to remove it.
        <div><button className="primary" onClick={() => setArmed(true)}>Place another {what}</button></div></div>;
}

function StampPanel({ cfg, setCfg, ctx, armed, setArmed }) {
  const stamps = ctx.me.stamps;
  return (
    <div className="stack">
      <ArmBar armed={armed} setArmed={setArmed} what="stamp" />
      <div className="chips">
        {Object.entries(stamps).map(([t, c]) => (
          <button key={t} className={cfg.text === t ? "active" : ""} style={{ color: cfg.text === t ? undefined : `rgb(${c.map((x) => x * 255)})` }}
            onClick={() => { setCfg({ ...cfg, text: t, color: c }); setArmed(true); }}>{t}</button>
        ))}
      </div>
      <label>Custom text<input value={cfg.text} onChange={(e) => setCfg({ ...cfg, text: e.target.value })} maxLength={40} /></label>
      <label>Size<input type="range" min={14} max={48} value={cfg.size} onChange={(e) => setCfg({ ...cfg, size: +e.target.value })} /></label>
      <label className="chk"><input type="checkbox" checked={cfg.date} onChange={(e) => setCfg({ ...cfg, date: e.target.checked })} /> Add today's date and my name</label>
      <label className="chk"><input type="checkbox" checked={cfg.all} onChange={(e) => setCfg({ ...cfg, all: e.target.checked })} /> Stamp every page at this position</label>
      <p className="muted">Stamps are real objects: select one to move, resize or delete it. Stamps placed with older versions are part of the page — use Undo to remove those.</p>
    </div>
  );
}

// ------------------------------------------------------------------ Organize pages
function Organize({ ctx }) {
  const { doc, act, say } = ctx;
  const [sel, setSel] = useState([]);
  const [ranges, setRanges] = useState("");
  const n = doc.pages.length;
  const tool = TOOLS.find((t) => t.id === "organize");
  const op = (o, extra = {}) => act("Updating pages…", async () => { const i = await api.pages(doc.id, { op: o, pages: sel, ...extra }); if (o === "delete") setSel([]); return i; });
  const toggle = (i, e) => setSel(e.ctrlKey || e.metaKey || e.shiftKey ? (sel.includes(i) ? sel.filter((x) => x !== i) : [...sel, i]) : sel.length === 1 && sel[0] === i ? [] : [i]);
  return (
    <div className="orgwrap">
      <Head tool={tool}>Click to select (Ctrl-click for several). Drag a page to move it.</Head>
      <div className="orgbar">
        <button disabled={!sel.length} onClick={() => op("rotate", { angle: 270 })}>⟲ Left</button>
        <button disabled={!sel.length} onClick={() => op("rotate", { angle: 90 })}>⟳ Right</button>
        <button disabled={!sel.length} onClick={() => op("duplicate")}>⧉ Duplicate</button>
        <button disabled={!sel.length} onClick={() => op("blank")}>＋ Blank after</button>
        <button disabled={!sel.length || sel.length >= n} onClick={() => confirm(`Delete ${sel.length} page(s)?`) && op("delete")}>🗑 Delete</button>
        <button onClick={() => act("Reversing…", () => api.pages(doc.id, { op: "reverse" }))}>⇅ Reverse</button>
        <button onClick={() => setSel(sel.length === n ? [] : [...Array(n).keys()])}>{sel.length === n ? "Select none" : "Select all"}</button>
        <button disabled={!sel.length} onClick={() => download(`/api/docs/${doc.id}/extract`, "POST", { pages: sel }, "extract.pdf").catch((e) => say(e.message, true))}>⤓ Extract to new PDF</button>
        <span className="grow" />
        <input placeholder="Split: 1-3, 4, 5-" value={ranges} onChange={(e) => setRanges(e.target.value)} size={14} />
        <button disabled={!ranges.trim()} onClick={() => download(`/api/docs/${doc.id}/split`, "POST", { ranges }, "split.zip").catch((e) => say(e.message, true))}>Split → zip</button>
      </div>
      <div className="orggrid">
        {doc.pages.map((_, i) => (
          <div key={i} className={"otile" + (sel.includes(i) ? " sel" : "")} draggable onClick={(e) => toggle(i, e)}
            onDragStart={(e) => e.dataTransfer.setData("text/plain", String(i))} onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const f = parseInt(e.dataTransfer.getData("text/plain"));
              if (isNaN(f) || f === i) return;
              const order = [...Array(n).keys()].filter((x) => x !== f);
              order.splice(i, 0, f);
              setSel([i]);
              act("Reordering…", () => api.pages(doc.id, { op: "reorder", order }));
            }}>
            <img loading="lazy" src={api.imgUrl(doc.id, i, 0.5, doc.rev)} alt="" draggable={false} />
            <span>{i + 1}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Create / Combine / Compare
function FileList({ files, setFiles, accept, label }) {
  const pick = useRef();
  return (
    <>
      <button onClick={() => pick.current.click()}>{label}</button>
      <input ref={pick} type="file" hidden multiple accept={accept} onChange={(e) => { setFiles([...files, ...e.target.files]); e.target.value = ""; }} />
      <ol className="files">
        {files.map((f, i) => (
          <li key={i}>{f.name}
            <span>
              <button onClick={() => i > 0 && setFiles(swap(files, i, i - 1))}>↑</button>
              <button onClick={() => i < files.length - 1 && setFiles(swap(files, i, i + 1))}>↓</button>
              <button onClick={() => setFiles(files.filter((_, j) => j !== i))}>✕</button>
            </span>
          </li>
        ))}
      </ol>
    </>
  );
}

function Create({ ctx }) {
  const [tab, setTab] = useState("images");
  const [imgs, setImgs] = useState([]);
  const [text, setText] = useState("");
  const [blank, setBlank] = useState({ count: 1, size: "a4", landscape: false });
  const [size, setSize] = useState("a4");
  const [fs, setFs] = useState(12);
  const tool = TOOLS.find((t) => t.id === "create");
  const go = (label, fn, to = "edit") => ctx.run(label, async () => { const i = await fn(); await ctx.openDoc(i.id, to); });
  return (
    <div className="screen">
      <Head tool={tool}>Start a new PDF from pictures, typed text, or blank pages.</Head>
      <div className="chips">{[["images", "From images / scans"], ["text", "From text"], ["blank", "Blank pages"]].map(([k, l]) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
      <div className="card stack">
        {tab === "images" && (
          <>
            <FileList files={imgs} setFiles={setImgs} accept="image/*" label="Choose images (JPG, PNG, TIFF…)" />
            <button className="primary" disabled={!imgs.length} onClick={() => go("Creating PDF…", () => api.createImages(imgs))}>Create PDF from {imgs.length || ""} image(s)</button>
            <p className="muted">One image per page, in the order shown. Then use Scan & OCR to make it searchable.</p>
          </>
        )}
        {tab === "text" && (
          <>
            <textarea rows={12} placeholder="Type or paste your text…" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="row">
              <select value={size} onChange={(e) => setSize(e.target.value)}><option value="a4">A4</option><option value="letter">Letter</option></select>
              <label>Font size <input type="number" min={6} max={48} value={fs} onChange={(e) => setFs(+e.target.value)} style={{ width: 60 }} /></label>
            </div>
            <button className="primary" disabled={!text.trim()} onClick={() => go("Creating PDF…", () => api.createText({ text, size, fontsize: fs }))}>Create PDF</button>
          </>
        )}
        {tab === "blank" && (
          <>
            <div className="row">
              <label>Pages <input type="number" min={1} max={500} value={blank.count} onChange={(e) => setBlank({ ...blank, count: +e.target.value })} style={{ width: 70 }} /></label>
              <select value={blank.size} onChange={(e) => setBlank({ ...blank, size: e.target.value })}><option value="a4">A4</option><option value="letter">Letter</option><option value="legal">Legal</option><option value="a3">A3</option></select>
              <label className="chk"><input type="checkbox" checked={blank.landscape} onChange={(e) => setBlank({ ...blank, landscape: e.target.checked })} /> Landscape</label>
            </div>
            <button className="primary" onClick={() => go("Creating PDF…", () => api.createBlank(blank))}>Create blank PDF</button>
          </>
        )}
      </div>
    </div>
  );
}

function Combine({ ctx }) {
  const [files, setFiles] = useState([]);
  const tool = TOOLS.find((t) => t.id === "combine");
  return (
    <div className="screen">
      <Head tool={tool}>Merge several PDFs into one. Reorder with the arrows; then fine-tune in Organize pages.</Head>
      <div className="card stack">
        <FileList files={files} setFiles={setFiles} accept=".pdf" label="Add PDFs…" />
        <button className="primary" disabled={files.length < 2}
          onClick={() => ctx.run("Merging…", async () => { const i = await api.merge(files); setFiles([]); await ctx.openDoc(i.id, "organize"); })}>
          Combine {files.length || ""} files
        </button>
      </div>
    </div>
  );
}

function Compare({ ctx }) {
  const [a, setA] = useState(null);
  const [b, setB] = useState(null);
  const [pwA, setPwA] = useState("");
  const [pwB, setPwB] = useState("");
  const [res, setRes] = useState(null);
  const tool = TOOLS.find((t) => t.id === "compare");
  return (
    <div className="screen">
      <Head tool={tool}>Find what changed between two versions of a document (text comparison).</Head>
      <div className="card stack">
        <div className="row cmpfiles">
          <label>Original<input type="file" accept=".pdf" onChange={(e) => { setA(e.target.files[0]); setPwA(""); setRes(null); }} />
            {a && <input type="password" autoComplete="new-password" placeholder="Password, if protected" aria-label="Original PDF password" value={pwA} onChange={(e) => setPwA(e.target.value)} />}</label>
          <label>Revised<input type="file" accept=".pdf" onChange={(e) => { setB(e.target.files[0]); setPwB(""); setRes(null); }} />
            {b && <input type="password" autoComplete="new-password" placeholder="Password, if protected" aria-label="Revised PDF password" value={pwB} onChange={(e) => setPwB(e.target.value)} />}</label>
        </div>
        <button className="primary" disabled={!a || !b} onClick={() => ctx.run("Comparing…", async () => setRes(await api.compare(a, b, pwA, pwB)))}>Compare</button>
      </div>
      {res && (
        <div className="card stack">
          {res.identical ? <div className="note ok">No text differences found ({res.pages_a} page(s) each).</div>
            : <div className="note">{res.removed} line(s) removed, {res.added} line(s) added · {res.pages_a} page(s) → {res.pages_b} page(s)</div>}
          {res.no_text && <div className="note warn">One file has no readable text (scan?). Run Scan & OCR on it first.</div>}
          {res.changes.map((c, i) => (
            <div key={i} className="diff">
              <b>Page {c.page}</b>
              {c.removed.map((t, k) => <div key={"r" + k} className="del">− {t}</div>)}
              {c.added.map((t, k) => <div key={"a" + k} className="add">+ {t}</div>)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Users (admin)
export function Users({ me, say, run }) {
  const [rows, setRows] = useState([]);
  const [f, setF] = useState({ username: "", password: "", role: "user" });
  const load = () => api.users().then(setRows).catch((e) => say(e.message, true));
  useEffect(() => { load(); }, []);
  const go = (label, fn, ok) => run(label, async () => { await fn(); await load(); ok && say(ok); });
  const lastLogin = (t) => (t ? new Date(t).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "never");
  return (
    <div className="screen">
      <div className="thead"><h2><span className="ico">👥</span> Users</h2>
        <p className="muted">Create accounts and choose a role. <b>Admin</b>: manage users, see the audit log, open any document. <b>User</b>: works with their own and shared documents.</p></div>
      <div className="card stack">
        <b>Add a user</b>
        <div className="row">
          <input placeholder="Username (e.g. priya)" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} autoComplete="off" />
          <input type="password" placeholder="Temporary password (min 8)" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} autoComplete="new-password" />
          <select value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}><option value="user">User</option><option value="admin">Admin</option></select>
          <button className="primary" disabled={!f.username.trim() || f.password.length < 8}
            onClick={() => go("Creating…", async () => { await api.addUser(f); setF({ username: "", password: "", role: "user" }); }, "User created — they must change the password at first sign-in")}>Add user</button>
        </div>
      </div>
      <table className="utable">
        <thead><tr><th>Username</th><th>Role</th><th>Status</th><th>Last sign-in</th><th></th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.username} className={r.active ? "" : "off"}>
              <td><b>{r.username}</b>{r.username === me.user && <span className="muted"> (you)</span>}{r.must_change && <span className="muted"> · must change password</span>}</td>
              <td>
                <select value={r.role} onChange={(e) => go("Saving…", () => api.updateUser(r.username, { role: e.target.value }))}>
                  <option value="user">User</option><option value="admin">Admin</option>
                </select>
              </td>
              <td>{r.active ? "Active" : "Disabled"}</td>
              <td className="muted">{lastLogin(r.last_login)}</td>
              <td className="rowbtns2">
                <button onClick={() => { const p = prompt(`New temporary password for ${r.username} (min 8 characters):`); p && go("Saving…", () => api.updateUser(r.username, { password: p }), "Password reset — they must change it at next sign-in"); }}>Reset password</button>
                <button disabled={r.username === me.user && r.active} onClick={() => go("Saving…", () => api.updateUser(r.username, { active: !r.active }))}>{r.active ? "Disable" : "Enable"}</button>
                <button disabled={r.username === me.user} onClick={() => confirm(`Delete user "${r.username}"? Their documents stay on the server.`) && go("Deleting…", () => api.deleteUser(r.username))}>🗑</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ Audit (admin)
export function Audit() {
  const [rows, setRows] = useState([]);
  const [user, setUser] = useState("");
  const [action, setAction] = useState("");
  const load = () => api.audit(`user=${encodeURIComponent(user)}&action=${encodeURIComponent(action)}`).then(setRows).catch(() => {});
  useEffect(() => { load(); }, []);
  return (
    <div className="screen audit">
      <div className="row">
        <input placeholder="User" value={user} onChange={(e) => setUser(e.target.value)} />
        <input placeholder="Action (e.g. ocr, upload, login_failed)" value={action} onChange={(e) => setAction(e.target.value)} size={32} />
        <button onClick={load}>Filter</button>
      </div>
      <table>
        <thead><tr><th>Time (UTC)</th><th>User</th><th>Action</th><th>Document</th><th>Detail</th><th>IP</th></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}><td>{r.ts.replace("T", " ")}</td><td>{r.user}</td><td>{r.action}</td><td>{r.doc_id}</td><td>{r.detail}</td><td>{r.ip}</td></tr>)}</tbody>
      </table>
    </div>
  );
}
