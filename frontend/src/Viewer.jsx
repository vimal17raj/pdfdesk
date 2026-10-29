import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";

const COLORS = { highlight: [1, 0.9, 0], underline: [0, 0.6, 0], strike: [1, 0, 0], rect: [1, 0, 0], ellipse: [0, 0.3, 1], ink: [1, 0, 0] };
const DRAG_ANNOTS = ["highlight", "underline", "strike", "rect", "ellipse", "redact"];

/*
 * mode: select (click text to edit) | text | highlight | underline | strike | rect | ellipse | ink | note | redact
 *       place (click -> onPlace(page,x,y)) | rect-cb (drag -> onRect(page,rect)) | none
 */
export default function Viewer({ doc, mode, act, zoom, setZoom, hits = [], curHit = -1, showLines, askText, fillUI, fieldBoxes, fieldObjs, onPlace, onRect, ghost, ctx }) {
  const [sel, setSel] = useState(null); // {page, id, kind} — the selected annotation / form field
  const selectable = mode === "select" || mode === "none";
  const [page, setPage] = useState(0);
  const [pin, setPin] = useState("1");
  const [panel, setPanel] = useState(null); // pages | bookmarks | comments
  const [fitMenu, setFitMenu] = useState(false);
  const box = useRef();
  const n = doc.pages.length;
  const go = (p) => {
    const t = Math.max(0, Math.min(n - 1, p));
    setPage(t);
    document.getElementById("pg" + t)?.scrollIntoView({ behavior: "smooth" });
  };
  useEffect(() => { if (page >= n) setPage(Math.max(0, n - 1)); }, [n, page]);
  useEffect(() => { setPin(String(page + 1)); }, [page]);

  const fit = (kind) => {
    setFitMenu(false);
    const el = box.current, p = doc.pages[page] || doc.pages[0];
    if (kind === "width") setZoom(Math.max(0.5, Math.min(3, +((el.clientWidth - 48) / p.w).toFixed(2))));
    else if (kind === "page") setZoom(Math.max(0.5, Math.min(3, +Math.min((el.clientWidth - 48) / p.w, (el.clientHeight - 32) / p.h).toFixed(2))));
    else setZoom(1);
  };
  const toggle = (k) => setPanel(panel === k ? null : k);
  const rail = (k, icon, title, on) => (
    <button key={k} className={"rbtn" + (panel === k || ctx?.tool?.id === k ? " on" : "")} title={title} onClick={on}>{icon}</button>
  );

  return (
    <div className="viewer">
      <div className="pages" ref={box} onScroll={(e) => {
        const el = e.currentTarget, kids = [...el.children];
        const mid = el.scrollTop + el.clientHeight / 3;
        const idx = kids.findIndex((k) => k.offsetTop - el.offsetTop + k.offsetHeight > mid);
        if (idx >= 0 && idx !== page) setPage(idx);
      }}>
        {doc.pages.map((pg, i) => (
          <PageView key={i} doc={doc} i={i} pg={pg} zoom={zoom} mode={mode} act={act} near={Math.abs(i - page) <= 2}
            hits={hits.filter((h) => h.page === i)} curHit={curHit} selectable={selectable} sel={sel} setSel={setSel} showLines={showLines} askText={askText}
            fillUI={fillUI} fieldBoxes={fieldBoxes} fieldObjs={fieldObjs} onPlace={onPlace} onRect={onRect} ghost={ghost} />
        ))}
      </div>
      {panel && <RailPanel kind={panel} doc={doc} page={page} go={go} close={() => setPanel(null)} />}
      <div className="rail">
        <div className="rtop">
          {rail("summary", "✦", "Generative summary (AI assistant)", () => ctx?.setTool("summary"))}
          {rail("comments", "💬", "Comments", () => toggle("comments"))}
          {rail("bookmarks", "🔖", "Bookmarks", () => toggle("bookmarks"))}
          {rail("pages", "🗐", "Page thumbnails", () => toggle("pages"))}
          {rail("sign", "✍", "Fill & e-signatures", () => ctx?.setTool("sign"))}
        </div>
        <div className="rbot">
          <input className="pin" value={pin} title="Go to page" onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
            onKeyDown={(e) => e.key === "Enter" && pin && go(+pin - 1)} onBlur={() => setPin(String(page + 1))} />
          <span className="ptot" title="Total pages">{n}</span>
          <button className="rbtn" title="Previous page" disabled={page <= 0} onClick={() => go(page - 1)}>⌃</button>
          <button className="rbtn" title="Next page" disabled={page >= n - 1} onClick={() => go(page + 1)}>⌄</button>
          <hr />
          <button className="rbtn" title="Rotate this page clockwise" onClick={() => act("Rotating…", () => api.pages(doc.id, { op: "rotate", pages: [page], angle: 90 }))}>↻</button>
          <div className="fitwrap">
            <button className="rbtn" title="Zoom to…" onClick={() => setFitMenu(!fitMenu)}>1:1</button>
            {fitMenu && (
              <div className="fitmenu">
                <button onClick={() => fit("width")}>Fit width</button>
                <button onClick={() => fit("page")}>Fit page</button>
                <button onClick={() => fit("actual")}>Actual size (100%)</button>
              </div>
            )}
          </div>
          <button className="rbtn" title={`Zoom in (${Math.round(zoom * 100)}%)`} onClick={() => setZoom(Math.min(3, +(zoom + 0.2).toFixed(1)))}>🔍＋</button>
          <button className="rbtn" title="Zoom out" onClick={() => setZoom(Math.max(0.5, +(zoom - 0.2).toFixed(1)))}>🔍－</button>
        </div>
      </div>
    </div>
  );
}

function ObjToolbar({ o, box, patch, remove, edit }) {
  const top = box.top > 44 ? box.top - 42 : box.top + box.height + 6;
  const stop = (e) => e.stopPropagation();
  return (
    <div className="objbar" style={{ left: Math.max(2, box.left), top }} onMouseDown={stop}>
      <span className="muted">{TYPE_LABEL[o.type] || o.type}</span>
      {o.type !== "Stamp" && <button title={o.kind === "field" ? "Rename field" : o.type === "FreeText" ? "Edit text (or double-click)" : "Edit comment text"} onClick={edit}>✎</button>}
      {o.kind === "annot" && HAS_COLOR.includes(o.type) && (
        <input type="color" title="Color" defaultValue={o.color && o.color !== "#000000" || o.type !== "FreeText" ? o.color : "#000000"}
          onChange={(e) => patch(o, { color: hexToRgb(e.target.value) }, "Recoloring…")} />
      )}
      {o.type === "FreeText" && (
        <select title="Font size" defaultValue="" onChange={(e) => e.target.value && patch(o, { size: +e.target.value }, "Resizing text…")}>
          <option value="">Size</option>{[8, 10, 12, 14, 18, 24, 36, 48].map((s) => <option key={s} value={s}>{s} pt</option>)}
        </select>
      )}
      {["Square", "Circle", "Ink"].includes(o.type) && (
        <select title="Line thickness" defaultValue="" onChange={(e) => e.target.value && patch(o, { width: +e.target.value }, "Updating…")}>
          <option value="">Line</option>{[1, 2, 4, 6, 10].map((s) => <option key={s} value={s}>{s} px</option>)}
        </select>
      )}
      <button className="del" title="Delete (Del key)" onClick={() => remove(o)}>🗑</button>
    </div>
  );
}

function RailPanel({ kind, doc, page, go, close }) {
  const [items, setItems] = useState(null);
  useEffect(() => {
    setItems(null);
    if (kind === "bookmarks") api.outline(doc.id).then(setItems).catch(() => setItems([]));
    if (kind === "comments") api.comments(doc.id).then(setItems).catch(() => setItems([]));
  }, [kind, doc.id, doc.version]);
  const title = { pages: "Pages", bookmarks: "Bookmarks", comments: "Comments" }[kind];
  return (
    <div className="rpanel">
      <div className="rphead"><b>{title}</b><button onClick={close}>✕</button></div>
      <div className="rpbody">
        {kind === "pages" && doc.pages.map((_, i) => (
          <div key={i} className={"rthumb" + (i === page ? " cur" : "")} onClick={() => go(i)}>
            <img loading="lazy" src={api.imgUrl(doc.id, i, 0.25, doc.version)} alt="" /><span>{i + 1}</span>
          </div>
        ))}
        {kind === "bookmarks" && (items === null ? <p className="muted">Loading…</p> : items.length === 0
          ? <p className="muted">This PDF has no bookmarks.</p>
          : items.map((b, k) => <a key={k} className="bm" style={{ paddingLeft: (b.level - 1) * 14 }} onClick={() => go(b.page)}>{b.title} <span className="muted">{b.page + 1}</span></a>))}
        {kind === "comments" && (items === null ? <p className="muted">Loading…</p> : items.length === 0
          ? <p className="muted">No comments. Use "Add comments" to add some.</p>
          : items.map((c) => (
            <div key={c.xref} className="cm" onClick={() => go(c.page)}>
              <span className="muted">Page {c.page + 1} · {c.type}{c.author ? " · " + c.author : ""}</span>
              <span>{c.text || <i className="muted">(no text)</i>}</span>
            </div>
          )))}
      </div>
    </div>
  );
}

const MOVABLE = ["Stamp", "FreeText", "Square", "Circle", "Text", "Field"];
const FIXED_SIZE = ["Text"]; // sticky-note icons move but don't resize
const HAS_COLOR = ["FreeText", "Square", "Circle", "Ink", "Highlight", "Underline", "StrikeOut"];
const HANDLES = { nw: [0, 0], n: [0.5, 0], ne: [1, 0], e: [1, 0.5], se: [1, 1], s: [0.5, 1], sw: [0, 1], w: [0, 0.5] };
const TYPE_LABEL = { Stamp: "Stamp / image", FreeText: "Text", Square: "Box", Circle: "Ellipse", Text: "Note", Ink: "Drawing", Highlight: "Highlight", Underline: "Underline", StrikeOut: "Strikeout", Field: "Form field" };
const hexToRgb = (h) => [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16) / 255);

function PageView({ doc, i, pg, zoom, mode, act, near, hits, curHit, selectable, sel, setSel, showLines, askText, fillUI, fieldBoxes, fieldObjs, onPlace, onRect, ghost }) {
  const [lines, setLines] = useState([]);
  const [annots, setAnnots] = useState([]);
  const [drag, setDrag] = useState(null);
  const [ed, setEd] = useState(null);
  const [tb, setTb] = useState(null);
  const [mouse, setMouse] = useState(null);
  const [gest, setGest] = useState(null); // {id, how:'move'|handle, start, orig, cur}
  const [oed, setOed] = useState(null); // inline editor for the selected object's text / field name
  const ref = useRef();
  const w = pg.w * zoom, h = pg.h * zoom;

  useEffect(() => {
    if (!near) return;
    if (showLines) api.lines(doc.id, i).then(setLines).catch(() => {}); else setLines([]);
    if (selectable) api.annots(doc.id, i).then(setAnnots).catch(() => {}); else setAnnots([]);
  }, [doc.id, doc.version, i, near, showLines, selectable]);

  // everything the user can select on this page: annotations (+ form fields in the form tool)
  const objects = selectable ? [
    ...annots.filter((a) => !["Link", "Widget", "Popup"].includes(a.type)).map((a) => ({ id: a.xref, kind: "annot", type: a.type, rect: a.rect, text: a.text, color: a.color })),
    ...(fieldObjs || []).filter((f) => f.page === i).map((f) => ({ id: f.name, kind: "field", type: "Field", rect: f.rect, text: f.name })),
  ] : [];
  const selObj = sel && sel.page === i ? objects.find((o) => o.id === sel.id && o.kind === sel.kind) : null;
  const shown = (o) => (gest && gest.id === o.id ? gest.cur : o.rect);

  const pt = (e) => {
    const r = ref.current.getBoundingClientRect();
    return [(e.clientX - r.left) / zoom, (e.clientY - r.top) / zoom];
  };

  const patch = (o, p, label = "Updating…") =>
    act(label, () => (o.kind === "field"
      ? api.updateField(doc.id, { name: o.id, rect: p.rect, new_name: p.text })
      : api.updateAnnot(doc.id, { page: i, xref: o.id, ...p })));
  const remove = (o) =>
    act("Deleting…", () => (o.kind === "field" ? api.deleteField(doc.id, { name: o.id }) : api.deleteAnnot(doc.id, { page: i, xref: o.id })))
      .then(() => setSel(null));

  // drag to move / resize the selected object
  const startGest = (e, o, how) => {
    e.stopPropagation();
    e.preventDefault();
    setSel({ page: i, id: o.id, kind: o.kind });
    if (!MOVABLE.includes(o.type)) return;
    const [x, y] = pt(e);
    setGest({ id: o.id, how, start: [x, y], orig: o.rect, cur: o.rect, obj: o });
  };
  useEffect(() => {
    if (!gest) return;
    const mv = (e) => {
      const [x, y] = pt(e);
      const dx = x - gest.start[0], dy = y - gest.start[1];
      let [x0, y0, x1, y1] = gest.orig;
      if (gest.how === "move") { x0 += dx; x1 += dx; y0 += dy; y1 += dy; }
      else {
        const keep = gest.obj.type === "Stamp"; // keep a stamp / signature picture's proportions
        const ar = (gest.orig[2] - gest.orig[0]) / (gest.orig[3] - gest.orig[1]);
        if (gest.how.includes("w")) x0 = Math.min(x0 + dx, x1 - 8);
        if (gest.how.includes("e")) x1 = Math.max(x1 + dx, x0 + 8);
        if (gest.how.includes("n")) y0 = Math.min(y0 + dy, y1 - 8);
        if (gest.how.includes("s")) y1 = Math.max(y1 + dy, y0 + 8);
        if (keep) {
          const nh = (x1 - x0) / ar;
          if (gest.how.includes("n")) y0 = y1 - nh; else y1 = y0 + nh;
        }
      }
      setGest({ ...gest, cur: [x0, y0, x1, y1] });
    };
    const upg = () => {
      const g = gest;
      setGest(null);
      const moved = g.cur.some((v, k) => Math.abs(v - g.orig[k]) > 1);
      if (moved) patch(g.obj, { rect: g.cur }, "Saving…");
    };
    window.addEventListener("mousemove", mv);
    window.addEventListener("mouseup", upg);
    return () => { window.removeEventListener("mousemove", mv); window.removeEventListener("mouseup", upg); };
  });
  // Delete / Escape on the selected object
  useEffect(() => {
    if (!selObj) return;
    const k = (e) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
      if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); remove(selObj); }
      if (e.key === "Escape") setSel(null);
    };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  });

  const dragging = DRAG_ANNOTS.includes(mode) || mode === "ink" || mode === "rect-cb";

  const down = (e) => {
    if (e.button !== 0 || e.target !== ref.current) return;
    if (selectable) setSel(null);
    const [x, y] = pt(e);
    if (mode === "text") return setTb({ x, y, text: "" });
    if (mode === "place") return onPlace?.(i, x, y);
    if (mode === "note") {
      const t = prompt("Comment text");
      if (t === null) return;
      return act("Adding comment…", () => api.annotate(doc.id, { page: i, type: "note", rect: [x, y, x + 20, y + 20], text: t }));
    }
    if (dragging) setDrag({ x0: x, y0: y, x1: x, y1: y, pts: [[x, y]] });
  };
  const move = (e) => {
    if (mode === "place") setMouse(pt(e));
    if (!drag) return;
    const [x, y] = pt(e);
    setDrag({ ...drag, x1: x, y1: y, pts: mode === "ink" ? [...drag.pts, [x, y]] : drag.pts });
  };
  const up = () => {
    setMouse(null);
    if (!drag) return;
    const d = drag;
    setDrag(null);
    const rect = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
    if (mode === "ink") {
      if (d.pts.length < 3) return;
      return act("Saving drawing…", () => api.annotate(doc.id, { page: i, type: "ink", strokes: [d.pts], color: COLORS.ink }));
    }
    if (rect[2] - rect[0] < 4 || rect[3] - rect[1] < 4) return;
    if (mode === "rect-cb") return onRect?.(i, rect);
    if (mode === "redact" && !confirm("Redaction permanently removes the content under the box. Continue?")) return;
    let text = "";
    if (askText) {
      text = prompt("Comment (optional)") ?? "";
    }
    act("Saving…", () => api.annotate(doc.id, { page: i, type: mode, rect, color: COLORS[mode], text }));
  };

  const commitEdit = () => {
    const e = ed;
    setEd(null);
    if (!e || e.text === e.orig) return;
    act("Updating text…", () => api.editText(doc.id, { page: i, bbox: e.bbox, text: e.text, size: e.size, color: e.color }));
  };

  const cursor = mode === "select" || mode === "text" ? "text" : mode === "none" ? "default" : "crosshair";
  const pageFields = fillUI ? fillUI.fields.filter((f) => f.page === i) : [];
  const boxes = fieldBoxes ? fieldBoxes.filter((f) => f.page === i) : [];
  const box = (r) => ({ left: r[0] * zoom, top: r[1] * zoom, width: (r[2] - r[0]) * zoom, height: (r[3] - r[1]) * zoom });

  return (
    <div className="page" id={"pg" + i} style={{ width: w, height: h }}>
      {near ? <img src={api.imgUrl(doc.id, i, Math.min(3, zoom * 1.5).toFixed(2), doc.version)} width={w} height={h} alt="" draggable={false} /> : <div className="ph" />}
      <div ref={ref} className="overlay" style={{ cursor }} onMouseDown={down} onMouseMove={move} onMouseUp={up}
        onMouseLeave={() => { setMouse(null); up(); }}>
        {showLines && lines.map((l, k) => (
          <div key={k} className="line" title="Click to edit" style={box(l.bbox)} onClick={() => setEd({ ...l, orig: l.text })} />
        ))}
        {hits.map((hh) => <div key={hh.gi} id={"hit" + hh.gi} className={"hit" + (hh.gi === curHit ? " cur" : "")} style={box(hh.rect)} />)}
        {boxes.map((f, k) => <div key={k} className="fbox" title={f.name + " (" + f.type + ")"} style={box(f.rect)} />)}
        {objects.map((o) => {
          const isSel = selObj && selObj.id === o.id && selObj.kind === o.kind;
          const r = shown(o);
          return (
            <div key={o.kind + o.id} className={"obj" + (isSel ? " sel" : "") + (MOVABLE.includes(o.type) ? " mv" : "")}
              title={`${TYPE_LABEL[o.type] || o.type}${o.text ? ": " + o.text : ""} — click to select`} style={box(r)}
              onMouseDown={(e) => startGest(e, o, "move")}
              onDoubleClick={(e) => { e.stopPropagation(); if (o.type !== "Stamp") setOed({ o, text: o.text }); }}>
              {isSel && MOVABLE.includes(o.type) && !FIXED_SIZE.includes(o.type) &&
                Object.entries(HANDLES).filter(([k]) => o.type !== "Stamp" || k.length === 2).map(([k, [fx, fy]]) => (
                  <span key={k} className={"handle h-" + k} style={{ left: fx * 100 + "%", top: fy * 100 + "%" }} onMouseDown={(e) => startGest(e, o, k)} />
                ))}
            </div>
          );
        })}
        {selObj && !gest && (
          <ObjToolbar o={selObj} box={box(selObj.rect)} patch={patch} remove={remove} edit={() => setOed({ o: selObj, text: selObj.text })} />
        )}
        {oed && (
          <input autoFocus className="inline" value={oed.text} placeholder={oed.o.kind === "field" ? "Field name" : "Text / comment"}
            style={{ left: oed.o.rect[0] * zoom, top: oed.o.rect[1] * zoom, minWidth: Math.max(160, (oed.o.rect[2] - oed.o.rect[0]) * zoom), fontSize: 14 }}
            onChange={(e) => setOed({ ...oed, text: e.target.value })}
            onBlur={() => { const c = oed; setOed(null); if (c.text !== c.o.text) patch(c.o, { text: c.text }, "Updating…"); }}
            onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); if (e.key === "Escape") setOed(null); }} />
        )}
        {pageFields.map((f) => {
          const v = fillUI.values[f.name];
          const st = box(f.rect);
          if (f.type === "CheckBox")
            return <input key={f.name} type="checkbox" className="ffield" style={st} checked={!!v} onChange={(e) => fillUI.setValue(f.name, e.target.checked)} />;
          if (f.type === "ComboBox" || f.type === "ListBox")
            return (
              <select key={f.name} className="ffield" style={{ ...st, fontSize: Math.max(10, st.height * 0.6) }} value={v ?? ""} onChange={(e) => fillUI.setValue(f.name, e.target.value)}>
                {f.choices.map((c, k) => <option key={k} value={Array.isArray(c) ? c[0] : c}>{Array.isArray(c) ? c[1] ?? c[0] : c}</option>)}
              </select>
            );
          if (f.type === "Text")
            return <input key={f.name} className="ffield" style={{ ...st, fontSize: Math.max(10, st.height * 0.6) }} value={v ?? ""} readOnly={f.readonly} onChange={(e) => fillUI.setValue(f.name, e.target.value)} />;
          return <div key={f.name} className="ffield sigf" style={st} title={f.type}>{f.type}</div>;
        })}
        {drag && mode !== "ink" && (
          <div className={"drag " + mode} style={{ left: Math.min(drag.x0, drag.x1) * zoom, top: Math.min(drag.y0, drag.y1) * zoom, width: Math.abs(drag.x1 - drag.x0) * zoom, height: Math.abs(drag.y1 - drag.y0) * zoom }} />
        )}
        {drag && mode === "ink" && (
          <svg className="inksvg"><polyline fill="none" stroke="red" strokeWidth="2" points={drag.pts.map(([x, y]) => `${x * zoom},${y * zoom}`).join(" ")} /></svg>
        )}
        {mode === "place" && mouse && ghost && (
          <div className="ghost" style={{ left: mouse[0] * zoom - (ghost.w * zoom) / 2, top: mouse[1] * zoom - (ghost.h * zoom) / 2, width: ghost.w * zoom, height: ghost.h * zoom }}>
            {ghost.img ? <img src={ghost.img} alt="" draggable={false} /> : ghost.label}
          </div>
        )}
        {ed && (
          <input autoFocus className="inline" value={ed.text}
            style={{ left: ed.bbox[0] * zoom, top: ed.bbox[1] * zoom, minWidth: (ed.bbox[2] - ed.bbox[0]) * zoom + 20, height: (ed.bbox[3] - ed.bbox[1]) * zoom + 4, fontSize: ed.size * zoom }}
            onChange={(e) => setEd({ ...ed, text: e.target.value })}
            onBlur={commitEdit}
            onKeyDown={(e) => { if (e.key === "Enter") commitEdit(); if (e.key === "Escape") setEd(null); }} />
        )}
        {tb && (
          <input autoFocus className="inline" placeholder="Type text, press Enter" value={tb.text}
            style={{ left: tb.x * zoom, top: tb.y * zoom, minWidth: 180, fontSize: 12 * zoom }}
            onChange={(e) => setTb({ ...tb, text: e.target.value })}
            onBlur={() => setTb(null)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setTb(null);
              if (e.key === "Enter" && tb.text.trim()) {
                const t = tb;
                setTb(null);
                act("Adding text…", () => api.annotate(doc.id, { page: i, type: "text", rect: [t.x, t.y, t.x + Math.max(120, t.text.length * 7), t.y + 20], text: t.text, size: 12 }));
              }
            }} />
        )}
      </div>
    </div>
  );
}
