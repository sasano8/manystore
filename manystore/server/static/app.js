// manystore storage UI — ビルドレスな vanilla フロントエンド。
// addressing は {bucket}/{path}（M025改・NS=/kv/raw）。GET /kv/raw/ で bucket/featured を
// 描画し、選んだ bucket のキーを「仮想ツリー」で一覧する。
//
// KVS のキーはフラット文字列。native API は prefix を撤去したので GET /kv/raw/{bucket}/ は
// bucket 内の**全キーをフラット**に返す（M030 で capability 化）。フロント側で "/" 区切りに
// 畳めば階層に見せられる（中間階層でも直下のフォルダ/ファイルを列挙可能）。
//   - state.dir : 現在のディレクトリ prefix（"" か "…/" で末尾は必ず "/"）。
//   - state.key : 開いているファイルの完全キー（無ければ null）。
// パンくず（dir1 / dir2 / dir3）はこの位置を表し、各セグメントのクリックでその階層へ移動。
// 左のコピーボタンで現在の生パスをコピー。パンくずをクリックすると生パス入力に切り替わり貼り付け可。

const state = { context: null, dir: "", key: null, entries: [], ws: null, featured: [] };

const $ = (id) => document.getElementById(id);
const setStatus = (s) => ($("status").textContent = s);
// dir は必ず "" もしくは末尾 "/"。先頭/重複スラッシュは正規化する。
const normDir = (p) => {
  p = (p || "").replace(/^\/+/, "").replace(/\/+/g, "/");
  return p && !p.endsWith("/") ? p + "/" : p;
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok && r.status !== 404) throw new Error(`${r.status} ${await r.text()}`);
  return r;
}

async function loadContexts() {
  const data = await (await api("/kv/raw/")).json();
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
  for (const li of document.querySelectorAll("#contexts li"))
    li.classList.toggle("active", li.dataset.ctx === ctx);
  state.key = null;
  await navigateTo(normDir(prefix));
  connectWs(ctx);
  if (prefix && quickWrite) {
    // featured な quick_write 先: 新規キーの雛形を生パス入力に入れて編集モードへ。
    editRawPath(normDir(prefix) + Date.now() + ".md");
  }
}

// 指定ディレクトリ prefix へ移動し、bucket 内の全キー（フラット）を取得してツリー描画する。
// native API は prefix を撤去したので bucket 全体を引き、現在 dir への絞り込みは renderTree が行う。
async function navigateTo(dir) {
  state.dir = normDir(dir);
  const data = await (await api(`/kv/raw/${state.context}/?limit=10000`)).json();
  state.entries = data.entries;
  renderTree();
  renderBreadcrumb();
}

// bucket 全体のフラットキーから現在 dir 配下だけを、直下のフォルダ（次セグメント）とファイルに畳んで描画。
function renderTree() {
  const ul = $("keys");
  ul.innerHTML = "";
  const folders = new Map(); // 名前 -> 配下キー数
  const files = [];
  for (const e of state.entries) {
    if (!e.key.startsWith(state.dir)) continue; // 現在 dir 配下のみ（クライアント側 prefix 絞り）
    const rest = e.key.slice(state.dir.length);
    if (!rest) continue;
    const slash = rest.indexOf("/");
    if (slash === -1) {
      files.push(e);
    } else {
      const name = rest.slice(0, slash);
      folders.set(name, (folders.get(name) || 0) + 1);
    }
  }

  if (state.dir) {
    // 親ディレクトリへ戻る行。
    const up = document.createElement("li");
    up.className = "folder";
    up.innerHTML = `<span>📁 ..</span>`;
    const parent = state.dir.replace(/[^/]+\/$/, "");
    up.onclick = () => navigateTo(parent);
    ul.appendChild(up);
  }

  for (const name of [...folders.keys()].sort()) {
    const li = document.createElement("li");
    li.className = "folder";
    li.innerHTML = `<span>📁 ${name}/</span><span class="size">${folders.get(name)}</span>`;
    li.onclick = () => navigateTo(state.dir + name + "/");
    ul.appendChild(li);
  }

  for (const e of files.sort((a, b) => a.key.localeCompare(b.key))) {
    const name = e.key.slice(state.dir.length);
    const li = document.createElement("li");
    li.innerHTML = `<span>📄 ${name}</span><span class="size">${e.size}</span>`;
    li.onclick = () => openKey(e.key);
    li.dataset.key = e.key;
    ul.appendChild(li);
  }
}

// パンくず: [context] / dir1 / dir2 / [file]。フォルダセグメントはクリックでその階層へ移動。
function renderBreadcrumb() {
  const nav = $("breadcrumb");
  nav.innerHTML = "";
  const crumb = (label, onclick, isLeaf) => {
    const a = document.createElement("span");
    a.className = "crumb" + (isLeaf ? " leaf" : "");
    a.textContent = label;
    if (onclick) a.onclick = (ev) => (ev.stopPropagation(), onclick());
    nav.appendChild(a);
  };
  const sep = () => {
    const s = document.createElement("span");
    s.className = "sep";
    s.textContent = "/";
    nav.appendChild(s);
  };

  crumb(state.context, () => navigateTo(""));
  const segs = state.dir.split("/").filter(Boolean);
  let acc = "";
  for (const seg of segs) {
    acc += seg + "/";
    const here = acc;
    sep();
    crumb(seg, () => navigateTo(here));
  }
  if (state.key) {
    sep();
    crumb(state.key.slice(state.dir.length), null, true); // 開いているファイル（リーフ・移動不可）
  }
}

// 現在の「生パス」: ファイルを開いていればその完全キー、なければ現在ディレクトリ。
const rawPath = () => state.key || state.dir;

async function openKey(key) {
  state.key = key;
  for (const li of document.querySelectorAll("#keys li"))
    li.classList.toggle("active", li.dataset.key === key);
  renderBreadcrumb();
  const r = await api(`/kv/raw/${state.context}/${encodeURI(key)}`);
  $("viewer").value = r.status === 404 ? "" : await r.text();
}

// パンくずを生パス入力に切り替える（クリック / 新規 / quick_write から）。
function editRawPath(initial) {
  const input = $("path-input");
  input.value = initial !== undefined ? initial : rawPath();
  $("breadcrumb").hidden = true;
  input.hidden = false;
  input.focus();
  input.select();
}

// 生パス入力を確定: 末尾 "/" はフォルダ移動、それ以外はファイルとして開く（無ければ新規）。
async function commitRawPath() {
  const input = $("path-input");
  input.hidden = true;
  $("breadcrumb").hidden = false;
  const path = input.value.trim();
  if (!path) return renderBreadcrumb();
  if (path.endsWith("/")) {
    state.key = null;
    await navigateTo(path);
    return;
  }
  const dir = normDir(path.replace(/[^/]+$/, ""));
  if (dir !== state.dir) {
    state.key = null;
    await navigateTo(dir);
  }
  await openKey(path);
}

async function save() {
  const key = (state.key || $("path-input").value).trim();
  if (!state.context || !key || key.endsWith("/"))
    return setStatus("context と key（ファイルパス）を指定してください");
  await api(`/kv/raw/${state.context}/${encodeURI(key)}`, {
    method: "PUT",
    body: $("viewer").value,
  });
  setStatus(`saved: ${key}`);
  state.key = key;
  await navigateTo(normDir(key.replace(/[^/]+$/, ""))); // ファイルの親へ移動して一覧更新
  await openKey(key);
}

async function del() {
  const key = state.key;
  if (!state.context || !key) return;
  await api(`/kv/raw/${state.context}/${encodeURI(key)}`, { method: "DELETE" });
  setStatus(`deleted: ${key}`);
  $("viewer").value = "";
  state.key = null;
  await navigateTo(state.dir);
}

function connectWs(ctx) {
  if (state.ws) state.ws.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/kv/raw/${ctx}/`);
  ws.onmessage = (ev) => {
    const e = JSON.parse(ev.data);
    setStatus(`${e.type}: ${e.key}`);
    if (state.context === ctx) navigateTo(state.dir); // 変更があれば現在階層を再描画
  };
  ws.onopen = () => setStatus("watching…");
  state.ws = ws;
}

// ── イベント結線 ──
$("copy-btn").onclick = async () => {
  const p = rawPath();
  try {
    await navigator.clipboard.writeText(p);
    setStatus(`copied: ${p || "(root)"}`);
  } catch {
    editRawPath(p); // clipboard 不可（非 https 等）なら選択して手動コピーできるよう入力化
  }
};
$("breadcrumb").onclick = () => editRawPath();
$("path-input").onkeydown = (ev) => {
  if (ev.key === "Enter") commitRawPath();
  if (ev.key === "Escape") {
    $("path-input").hidden = true;
    $("breadcrumb").hidden = false;
  }
};
$("path-input").onblur = () => {
  if (!$("path-input").hidden) commitRawPath();
};
$("save-btn").onclick = save;
$("delete-btn").onclick = del;
$("reload-btn").onclick = () => navigateTo(state.dir);
$("new-btn").onclick = () => {
  state.key = null;
  $("viewer").value = "";
  editRawPath(state.dir); // 現在ディレクトリを prefill → ファイル名を足して保存
};

loadContexts().catch((e) => setStatus("error: " + e.message));
