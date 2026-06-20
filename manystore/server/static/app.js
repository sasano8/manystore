// manystore storage UI — ビルドレスな vanilla フロントエンド。
// /contexts を読んで context/featured を描画し、選んだ context のキーを一覧、
// クリックで内容を表示・編集（PUT）・削除。WS で変更をライブ反映する。

const state = { context: null, key: null, ws: null, featured: [] };

const $ = (id) => document.getElementById(id);
const setStatus = (s) => ($("status").textContent = s);

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok && r.status !== 404) throw new Error(`${r.status} ${await r.text()}`);
  return r;
}

async function loadContexts() {
  const data = await (await api("/contexts")).json();
  state.featured = data.featured || [];

  const ctxUl = $("contexts");
  ctxUl.innerHTML = "";
  for (const c of data.contexts) {
    const li = document.createElement("li");
    li.textContent = c.name;
    if (!c.writable) li.innerHTML += ' <span class="pin-badge">RO</span>';
    li.onclick = () => selectContext(c.name);
    li.dataset.ctx = c.name;
    ctxUl.appendChild(li);
  }

  const featUl = $("featured");
  featUl.innerHTML = "";
  for (const f of state.featured) {
    const li = document.createElement("li");
    li.textContent = f.label || `${f.context}/${f.path}`;
    if (f.pin) li.innerHTML += ' <span class="pin-badge">PIN</span>';
    li.onclick = () => selectContext(f.context, f.path, f.quick_write);
    featUl.appendChild(li);
  }

  const start = data.default_context || (data.contexts[0] && data.contexts[0].name);
  if (start) selectContext(start);
}

async function selectContext(ctx, prefix = "", quickWrite = false) {
  state.context = ctx;
  $("current-context").textContent = ctx;
  for (const li of document.querySelectorAll("#contexts li"))
    li.classList.toggle("active", li.dataset.ctx === ctx);
  if (prefix && quickWrite) {
    // featured な quick_write 先: 新規キーの雛形を key 欄に入れる（汎用の「新規テキスト」）。
    $("key-input").value = prefix.replace(/\/$/, "") + "/" + Date.now() + ".md";
  }
  await loadKeys(prefix);
  connectWs(ctx);
}

async function loadKeys(prefix = "") {
  const data = await (
    await api(`/contexts/${state.context}/keys?prefix=${encodeURIComponent(prefix)}`)
  ).json();
  const ul = $("keys");
  ul.innerHTML = "";
  for (const e of data.entries) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${e.key}</span><span class="size">${e.size}</span>`;
    li.onclick = () => openKey(e.key);
    li.dataset.key = e.key;
    ul.appendChild(li);
  }
}

async function openKey(key) {
  state.key = key;
  $("key-input").value = key;
  for (const li of document.querySelectorAll("#keys li"))
    li.classList.toggle("active", li.dataset.key === key);
  const r = await api(`/contexts/${state.context}/objects/${encodeURI(key)}`);
  $("viewer").value = r.status === 404 ? "" : await r.text();
}

async function save() {
  const key = $("key-input").value.trim();
  if (!state.context || !key) return setStatus("context と key を指定してください");
  await api(`/contexts/${state.context}/objects/${encodeURI(key)}`, {
    method: "PUT",
    body: $("viewer").value,
  });
  setStatus(`saved: ${key}`);
  await loadKeys();
}

async function del() {
  const key = $("key-input").value.trim();
  if (!state.context || !key) return;
  await api(`/contexts/${state.context}/objects/${encodeURI(key)}`, { method: "DELETE" });
  setStatus(`deleted: ${key}`);
  $("viewer").value = "";
  await loadKeys();
}

function connectWs(ctx) {
  if (state.ws) state.ws.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/contexts/${ctx}/events`);
  ws.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    setStatus(`${e.type}: ${e.key}`);
    loadKeys(); // 変更があればキー一覧を更新（ライブ反映）
  };
  ws.onopen = () => setStatus("watching…");
  state.ws = ws;
}

$("save-btn").onclick = save;
$("delete-btn").onclick = del;
$("reload-btn").onclick = () => loadKeys();
$("new-btn").onclick = () => {
  const key = $("key-input").value.trim();
  if (key) openKey(key);
};

loadContexts().catch((e) => setStatus("error: " + e.message));
