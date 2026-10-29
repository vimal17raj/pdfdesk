let token = sessionStorage.getItem("pdfdesk_token") || "";

export const getToken = () => token;
export const setToken = (t) => {
  token = t;
  t ? sessionStorage.setItem("pdfdesk_token", t) : sessionStorage.removeItem("pdfdesk_token");
};

async function failMsg(r) {
  let msg = r.statusText;
  try {
    msg = (await r.json()).detail || msg;
  } catch {}
  return typeof msg === "string" ? msg : JSON.stringify(msg);
}

async function req(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  let body = opts.body;
  if (body && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  const r = await fetch(path, { ...opts, headers, body });
  if (r.status === 401 && token) {
    setToken("");
    location.reload();
  }
  if (!r.ok) throw new Error(await failMsg(r));
  return r.json();
}

const post = (p, body) => req(p, { method: "POST", body: body ?? {} });
const files = (key, list, extra = {}) => {
  const f = new FormData();
  list.forEach((x) => f.append(key, x));
  Object.entries(extra).forEach(([k, v]) => f.append(k, v));
  return f;
};

export const api = {
  login: (username, password) => post("/api/auth/login", { username, password }),
  me: () => req("/api/me"),
  upload: (file, password = "") => post("/api/docs", files("file", [file], { password })),
  doc: (id) => req(`/api/docs/${id}`),
  recent: () => req("/api/docs"),
  share: (id, user) => post(`/api/docs/${id}/share`, { user }),
  lines: (id, n) => req(`/api/docs/${id}/page/${n}/lines`),
  annots: (id, n) => req(`/api/docs/${id}/page/${n}/annots`),
  outline: (id) => req(`/api/docs/${id}/outline`),
  comments: (id) => req(`/api/docs/${id}/comments`),
  fields: (id) => req(`/api/docs/${id}/fields`),
  search: (id, q) => req(`/api/docs/${id}/search?q=${encodeURIComponent(q)}`),
  undo: (id) => post(`/api/docs/${id}/undo`),
  redo: (id) => post(`/api/docs/${id}/redo`),

  createBlank: (b) => post("/api/create/blank", b),
  createText: (b) => post("/api/create/text", b),
  createImages: (list) => post("/api/create/images", files("files", list)),
  merge: (list) => post("/api/merge", files("files", list)),
  append: (id, list) => post(`/api/docs/${id}/append`, files("files", list)),
  compare: (a, b) => {
    const f = new FormData();
    f.append("a", a);
    f.append("b", b);
    return post("/api/compare", f);
  },

  editText: (id, b) => post(`/api/docs/${id}/edit-text`, b),
  annotate: (id, b) => post(`/api/docs/${id}/annotate`, b),
  deleteAnnot: (id, b) => post(`/api/docs/${id}/delete-annot`, b),
  updateAnnot: (id, b) => post(`/api/docs/${id}/update-annot`, b),
  deleteField: (id, b) => post(`/api/docs/${id}/delete-field`, b),
  updateField: (id, b) => post(`/api/docs/${id}/update-field`, b),
  pages: (id, b) => post(`/api/docs/${id}/pages`, b),
  watermark: (id, text) => post(`/api/docs/${id}/watermark`, { text }),
  ocr: (id, b) => post(`/api/docs/${id}/ocr`, b), // starts a background job
  compress: (id, level) => post(`/api/docs/${id}/compress`, { level }), // starts a background job
  job: (id) => req(`/api/jobs/${id}`),
  cancelJob: (id) => post(`/api/jobs/${id}/cancel`),
  fill: (id, values) => post(`/api/docs/${id}/fill`, { values }),
  addField: (id, b) => post(`/api/docs/${id}/add-field`, b),
  sign: (id, b) => post(`/api/docs/${id}/sign`, b),
  stamp: (id, b) => post(`/api/docs/${id}/stamp`, b),
  summary: (id, length) => post(`/api/docs/${id}/summary`, { length }),
  users: () => req("/api/users"),
  userNames: () => req("/api/users/names"),
  addUser: (b) => post("/api/users", b),
  updateUser: (name, b) => post(`/api/users/${encodeURIComponent(name)}/update`, b),
  deleteUser: (name) => post(`/api/users/${encodeURIComponent(name)}/delete`),
  changePassword: (old, nw) => post("/api/me/password", { old, new: nw }),
  audit: (q = "") => req(`/api/audit?${q}`),
  imgUrl: (id, n, zoom, v) => `/api/docs/${id}/page/${n}.png?zoom=${zoom}&v=${v}&token=${token}`,
};

export async function fetchBlob(path) {
  const r = await fetch(path, { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(await failMsg(r));
  return r.blob();
}

// Binary downloads need the auth header, so fetch as blob and save.
export async function download(path, method = "GET", body, fallbackName = "file") {
  const headers = { Authorization: `Bearer ${token}` };
  if (body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(await failMsg(r));
  const cd = r.headers.get("Content-Disposition") || "";
  const name = (cd.match(/filename="([^"]+)"/) || [])[1] || fallbackName;
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
