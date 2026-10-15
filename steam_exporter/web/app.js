"use strict";

// ---------------------------------------------------------------------------
// 基础工具
// ---------------------------------------------------------------------------
const TOKEN = new URLSearchParams(location.search).get("token");

function withToken(path) {
  if (!TOKEN) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(TOKEN);
}

async function api(path, options) {
  const res = await fetch(withToken(path), options);
  let data = {};
  try { data = await res.json(); } catch (e) { /* 忽略 */ }
  if (!res.ok) throw new Error(data.error || "HTTP " + res.status);
  return data;
}

function el(id) { return document.getElementById(id); }

function fmtSize(n) {
  if (n == null) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(1)) + " " + units[i];
}

function fmtDur(s) {
  if (s == null) return "-";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(x)}` : `${m}:${pad(x)}`;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function timestamp(item) {
  const t = Date.parse(item.recorded_at);
  return Number.isNaN(t) ? 0 : t;
}

let toastTimer = null;
function toast(message) {
  const node = el("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 3200);
}

// ---------------------------------------------------------------------------
// 状态
// ---------------------------------------------------------------------------
const state = {
  items: [],
  groups: [],
  selected: new Set(),
  expanded: new Set(),
  rendered: new Set(),
  statusById: new Map(),
  sort: "recent",
  jobId: null,
  es: null,
  planned: 0,
  done: 0,
  failed: 0,
  skipped: 0,
  bytes: 0,
  currentName: "",
  currentTotal: null,
  pendingDownload: null,
  pendingDelete: null,
  steamRunning: false,
  outDir: "",
};

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
async function init() {
  try {
    const summary = await api("/api/summary");
    applySummary(summary);
  } catch (err) {
    el("summary-line").textContent = "无法连接后端：" + err.message;
    return;
  }
  bindEvents();
  await loadRecordings();
}

function applySummary(s) {
  const userSelect = el("user-select");
  userSelect.innerHTML = "";
  for (const user of s.users) {
    userSelect.appendChild(new Option(
      user.id + (user.primary ? "（主账号）" : "") + ` · ${user.clips} 剪辑`, user.id));
  }
  if (s.users.some((u) => u.id === s.default_user)) userSelect.value = s.default_user;

  const codec = el("codec");
  codec.innerHTML = "";
  for (const name of s.encoders.software) codec.appendChild(new Option(name, name));
  for (const name of s.encoders.vaapi) codec.appendChild(new Option(name + "（硬件）", name));

  el("out-dir").value = s.default_out;
  el("template").value = s.default_template;
  state.outDir = s.default_out;

  const profile = el("download-profile");
  profile.innerHTML = "";
  for (const item of s.download_profiles || []) {
    profile.appendChild(new Option(item.label, item.id));
  }
  if (profile.options.length) profile.value = "fast";
  updateCacheInfo(s.cache, s.preview);
  state.steamRunning = !!s.steam_running;
  updateTrashInfo(s.trash);

  const badge = el("ffmpeg-badge");
  if (s.ffmpeg) { badge.textContent = "ffmpeg 就绪"; badge.className = "badge ok"; }
  else { badge.textContent = "缺少 ffmpeg"; badge.className = "badge err"; }

  el("summary-line").textContent =
    `${s.users.length} 个账号 · 版本 ${s.version}` +
    (s.encoders.vaapi_device ? " · 支持硬件编码" : "");

  syncModeFields();
}

function bindEvents() {
  el("apply-filter").addEventListener("click", loadRecordings);
  el("user-select").addEventListener("change", loadRecordings);
  el("mode").addEventListener("change", syncModeFields);
  el("sort-by").addEventListener("change", (e) => {
    state.sort = e.target.value;
    renderGroups();
  });
  el("select-all").addEventListener("change", (e) => {
    state.selected.clear();
    if (e.target.checked) for (const item of state.items) state.selected.add(item.id);
    refreshAllChecks();
    updateSelectionInfo();
  });
  el("export-selected").addEventListener("click", () => doExport(true));
  el("export-all").addEventListener("click", () => doExport(false));
  el("cancel-job").addEventListener("click", cancelJob);
  el("reveal-output").addEventListener("click", revealOutput);
  el("toggle-log").addEventListener("click", () => setLogOpen(el("log").hidden));
  el("clear-cache").addEventListener("click", clearCache);
  el("delete-selected").addEventListener("click", () => openDeleteConfirm(Array.from(state.selected)));
  el("trash-restore").addEventListener("click", restoreTrash);
  el("trash-purge").addEventListener("click", purgeTrash);
  el("confirm-cancel").addEventListener("click", closeDeleteConfirm);
  el("confirm-ok").addEventListener("click", confirmDelete);
  el("preview-close").addEventListener("click", closePreview);
  el("preview-modal").addEventListener("click", (event) => {
    if (event.target.id === "preview-modal") closePreview();
  });
  el("play-close").addEventListener("click", closePlay);
  el("play-modal").addEventListener("click", (event) => {
    if (event.target.id === "play-modal") closePlay();
  });
}

function syncModeFields() {
  const compress = el("mode").value === "compress";
  for (const node of document.querySelectorAll(".compress-only")) {
    node.style.display = compress ? "" : "none";
  }
}

// ---------------------------------------------------------------------------
// 列表（按游戏分组）
// ---------------------------------------------------------------------------
function currentFilters() {
  return {
    type: el("type-filter").value,
    games: [el("game-filter").value.trim()].filter(Boolean),
    since: el("since-filter").value || "",
    until: el("until-filter").value || "",
    q: el("q-filter").value.trim(),
  };
}

async function loadRecordings() {
  const user = el("user-select").value || "auto";
  const f = currentFilters();
  const params = new URLSearchParams();
  params.set("user", user);
  params.set("type", f.type);
  for (const g of f.games) params.append("game", g);
  if (f.since) params.set("since", f.since);
  if (f.until) params.set("until", f.until);
  if (f.q) params.set("q", f.q);

  el("list-info").textContent = "加载中…";
  try {
    const data = await api("/api/recordings?" + params.toString());
    state.items = data.items;
    state.selected.clear();
    state.rendered.clear();
    el("select-all").checked = false;
    renderGroups();
    el("list-info").textContent =
      `共 ${data.count} 条 · ${state.groups.length} 个游戏 · ${fmtSize(data.total_size)}`;
  } catch (err) {
    el("list-info").textContent = "加载失败：" + err.message;
  }
}

function groupItems(items) {
  const map = new Map();
  for (const item of items) {
    let group = map.get(item.game);
    if (!group) {
      group = { game: item.game, items: [], size: 0, clips: 0, backgrounds: 0, foregrounds: 0, latest: 0 };
      map.set(item.game, group);
    }
    group.items.push(item);
    group.size += item.size || 0;
    if (item.kind === "clip") group.clips++;
    else if (item.kind === "background") group.backgrounds++;
    else group.foregrounds++;
    group.latest = Math.max(group.latest, timestamp(item));
  }
  const groups = Array.from(map.values());
  for (const group of groups) {
    group.items.sort((a, b) => timestamp(b) - timestamp(a));
  }
  sortGroups(groups);
  return groups;
}

function sortGroups(groups) {
  if (state.sort === "name") {
    groups.sort((a, b) => a.game.localeCompare(b.game, "zh"));
  } else if (state.sort === "size") {
    groups.sort((a, b) => b.size - a.size);
  } else {
    groups.sort((a, b) => b.latest - a.latest);
  }
}

function renderGroups() {
  const container = el("groups");
  container.innerHTML = "";
  const groups = groupItems(state.items);
  state.groups = groups;
  el("empty-hint").hidden = groups.length > 0;
  for (const group of groups) container.appendChild(renderGroup(group));
  updateSelectionInfo();
}

function metaText(group) {
  const parts = [];
  if (group.clips) parts.push(`剪辑 ${group.clips}`);
  if (group.backgrounds) parts.push(`后台 ${group.backgrounds}`);
  if (group.foregrounds) parts.push(`录像 ${group.foregrounds}`);
  parts.push(fmtSize(group.size));
  return parts.join(" · ");
}

function renderGroup(group) {
  const wrap = document.createElement("div");
  wrap.className = "group";
  wrap.dataset.game = group.game;

  const head = document.createElement("div");
  head.className = "group-head";

  const caret = document.createElement("button");
  caret.className = "caret";
  caret.textContent = state.expanded.has(group.game) ? "▾" : "▸";
  caret.setAttribute("aria-label", "展开/折叠");

  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "group-check";
  refreshGroupCheck(group, check);
  check.addEventListener("change", () => {
    for (const item of group.items) {
      if (check.checked) state.selected.add(item.id);
      else state.selected.delete(item.id);
    }
    wrap.querySelectorAll(".item-check").forEach((cb) => { cb.checked = check.checked; });
    refreshGroupCheck(group, check);
    updateSelectionInfo();
  });

  const title = document.createElement("div");
  title.className = "group-title";
  title.innerHTML =
    `<span class="game-name">${escapeHtml(group.game)}</span>` +
    `<span class="group-meta">${metaText(group)}</span>`;

  const thumb = document.createElement("img");
  thumb.className = "game-thumb";
  thumb.alt = "";
  thumb.loading = "lazy";
  const appid = group.items[0] ? group.items[0].appid : "";
  thumb.src = withToken(`/api/icon/${encodeURIComponent(appid)}`);
  thumb.addEventListener("error", () => {
    const fallback = document.createElement("div");
    fallback.className = "game-thumb";
    fallback.textContent = (group.game || "?").trim().charAt(0).toUpperCase();
    thumb.replaceWith(fallback);
  });

  const summary = document.createElement("div");
  summary.className = "group-summary";

  head.append(caret, check, thumb, title, summary);
  const toggle = () => toggleGroup(group.game, wrap);
  caret.addEventListener("click", toggle);
  title.addEventListener("click", toggle);
  wrap.appendChild(head);

  const children = document.createElement("div");
  children.className = "group-items";
  children.hidden = !state.expanded.has(group.game);
  wrap.appendChild(children);
  if (state.expanded.has(group.game)) renderChildren(group, children);
  updateGroupSummary(group, summary);
  return wrap;
}

function renderChildren(group, container) {
  container.innerHTML = "";
  for (const item of group.items) container.appendChild(renderItemRow(item));
  state.rendered.add(group.game);
}

function renderItemRow(item) {
  const row = document.createElement("div");
  row.className = "item-row";
  row.dataset.id = item.id;

  const check = document.createElement("input");
  check.type = "checkbox";
  check.className = "item-check";
  check.checked = state.selected.has(item.id);
  check.addEventListener("change", () => {
    if (check.checked) state.selected.add(item.id);
    else state.selected.delete(item.id);
    const group = state.groups.find((g) => g.game === item.game);
    const groupCheck = document.querySelector(
      `.group[data-game="${CSS.escape(item.game)}"] .group-check`);
    if (group && groupCheck) refreshGroupCheck(group, groupCheck);
    updateSelectionInfo();
  });

  const thumb = document.createElement("img");
  thumb.className = "item-thumb";
  thumb.alt = "";
  thumb.loading = "lazy";
  thumb.title = "点击预览";
  thumb.dataset.src = withToken(
    `/api/thumb/${encodeURIComponent(item.id)}?user=${encodeURIComponent(el("user-select").value || "auto")}`);
  thumb.addEventListener("click", (event) => {
    event.stopPropagation();
    openPreview(item);
  });
  thumb.addEventListener("error", () => { thumb.classList.add("missing"); });
  ensureThumbObserver().observe(thumb);

  const kind = document.createElement("span");
  kind.className = `kind ${item.kind}`;
  kind.textContent = item.kind_cn;

  row.append(
    check,
    thumb,
    kind,
    span("item-time", item.recorded_display),
    span("item-dur", fmtDur(item.duration_s)),
    span("item-size", fmtSize(item.size)),
    span("item-id mono", item.id),
  );

  const status = document.createElement("span");
  status.className = "item-status";
  const current = state.statusById.get(item.id);
  if (current) {
    status.textContent = current.text;
    row.classList.add(current.status);
  }
  row.appendChild(status);

  const action = document.createElement("div");
  action.className = "item-action";
  if (item.playable !== false) {
    const play = document.createElement("button");
    play.className = "icon-btn";
    play.title = "在线播放（最多前 5 分钟）";
    play.innerHTML =
      '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" stroke="none">' +
      '<path d="M8 5v14l11-7z"/></svg>';
    play.addEventListener("click", (event) => {
      event.stopPropagation();
      openPlay(item);
    });
    action.appendChild(play);
  }
  const download = document.createElement("button");
  download.className = "icon-btn";
  download.title = "下载到本机（不在 Deck 上保存）";
  download.innerHTML =
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>';
  download.addEventListener("click", (event) => {
    event.stopPropagation();
    downloadItem(item);
  });
  action.appendChild(download);

  const remove = document.createElement("button");
  remove.className = "icon-btn danger";
  remove.title = "删除该录制（默认移到回收站）";
  remove.innerHTML =
    '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/></svg>';
  remove.addEventListener("click", (event) => {
    event.stopPropagation();
    openDeleteConfirm([item.id]);
  });
  action.appendChild(remove);
  row.appendChild(action);
  return row;
}

let thumbObserver = null;

function ensureThumbObserver() {
  if (thumbObserver) return thumbObserver;
  thumbObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const img = entry.target;
        if (img.dataset.src && !img.src) img.src = img.dataset.src;
        thumbObserver.unobserve(img);
      }
    },
    { rootMargin: "150px" }
  );
  return thumbObserver;
}

function openPreview(item) {
  const user = el("user-select").value || "auto";
  el("preview-title").textContent = `${item.game} · ${item.recorded_display}`;
  const video = el("preview-video");
  const hint = el("preview-hint");
  hint.hidden = false;
  hint.textContent = "正在生成预览…";
  video.src = withToken(
    `/api/preview/${encodeURIComponent(item.id)}?user=${encodeURIComponent(user)}`);
  video.load();
  video.onloadeddata = () => { hint.hidden = true; };
  video.onerror = () => {
    hint.hidden = false;
    hint.textContent = "无法生成预览（可能是录制损坏）";
  };
  el("preview-modal").hidden = false;
}

function closePreview() {
  const video = el("preview-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  el("preview-modal").hidden = true;
}

function openPlay(item) {
  const user = el("user-select").value || "auto";
  el("play-title").textContent = `${item.game} · ${item.recorded_display}`;
  const video = el("play-video");
  const hint = el("play-hint");
  hint.hidden = false;
  hint.textContent =
    item.duration_s && item.duration_s > 300
      ? "正在加载…（长录像只播放前 5 分钟）"
      : "正在加载…";
  video.src = withToken(
    `/api/play/${encodeURIComponent(item.id)}?user=${encodeURIComponent(user)}`);
  video.load();
  video.onloadeddata = () => { hint.hidden = true; };
  video.onerror = () => {
    hint.hidden = false;
    hint.textContent = "无法在线播放（录制可能损坏）";
  };
  el("play-modal").hidden = false;
}

function closePlay() {
  const video = el("play-video");
  video.pause();
  video.removeAttribute("src");
  video.load();
  el("play-modal").hidden = true;
}

function downloadItem(item) {
  const profile = el("download-profile").value || "fast";
  const user = el("user-select").value || "auto";
  if (profile === "lossless") {
    const params = new URLSearchParams();
    params.set("user", user);
    params.set("id", item.id);
    params.set("mode", "lossless");
    params.set("trim", el("trim").checked ? "1" : "0");
    if (TOKEN) params.set("token", TOKEN);
    toast("开始下载（无损流式）");
    triggerUrl("/api/download?" + params.toString());
    return;
  }
  prepareDownload({ user, id: item.id, profile }, item);
}

async function prepareDownload(payload, item) {
  try {
    const res = await api("/api/download/prepare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ready) {
      toast("开始下载（命中缓存）");
      triggerFileDownload(res.key);
      return;
    }
    state.pendingDownload = { key: res.key };
    toast("正在准备压缩，完成后自动开始下载…");
    startJob(res.job_id, 1, item ? item.size : 0, true);
  } catch (err) {
    toast("下载失败：" + err.message);
  }
}

function triggerUrl(url) {
  const link = document.createElement("a");
  link.href = url;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function triggerFileDownload(key) {
  triggerUrl(withToken(`/api/download/file?key=${encodeURIComponent(key)}`));
}

function updateCacheInfo(cache, preview) {
  const node = el("cache-info");
  const parts = [];
  if (cache) parts.push(`下载 ${cache.count}项/${fmtSize(cache.bytes)}`);
  if (preview) parts.push(`预览 ${preview.count}项/${fmtSize(preview.bytes)}`);
  node.textContent = parts.length ? "缓存：" + parts.join("，") : "缓存：未启用";
}

async function clearCache() {
  try {
    const res = await api("/api/cache/clear", { method: "POST" });
    el("cache-info").textContent = "缓存：0 项 / 0 B";
    toast(`已清理 ${res.removed} 个缓存文件`);
  } catch (err) {
    toast("清理失败：" + err.message);
  }
}

function updateTrashInfo(trash) {
  const node = el("trash-info");
  if (!trash) {
    node.textContent = "回收站：未启用";
    return;
  }
  node.textContent = `回收站：${trash.count} 项 / ${fmtSize(trash.bytes)}`;
  el("trash-restore").disabled = trash.count === 0;
  el("trash-purge").disabled = trash.count === 0;
}

function openDeleteConfirm(ids) {
  ids = (ids || []).filter(Boolean);
  if (!ids.length) {
    toast("请先勾选要删除的录制");
    return;
  }
  const items = state.items.filter((item) => ids.includes(item.id));
  const size = items.reduce((sum, item) => sum + (item.size || 0), 0);
  const clips = items.filter((item) => item.kind === "clip").length;
  const recordings = items.length - clips;
  state.pendingDelete = ids;
  el("confirm-permanent").checked = false;
  let body = `将删除 ${items.length} 条录制（剪辑 ${clips}、录像 ${recordings}），约 ${fmtSize(size)}。`;
  body += "默认移入回收站，可恢复。";
  if (state.steamRunning) {
    body += " ⚠ 检测到 Steam 正在运行，建议先关闭 Steam 再删除。";
  }
  el("confirm-body").textContent = body;
  el("confirm-modal").hidden = false;
}

function closeDeleteConfirm() {
  el("confirm-modal").hidden = true;
  state.pendingDelete = null;
}

async function confirmDelete() {
  const ids = state.pendingDelete;
  if (!ids || !ids.length) return;
  const permanent = el("confirm-permanent").checked;
  const button = el("confirm-ok");
  button.disabled = true;
  try {
    const res = await api("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user: el("user-select").value || "auto",
        ids,
        permanent,
      }),
    });
    closeDeleteConfirm();
    toast(`${permanent ? "已彻底删除" : "已移入回收站"} ${res.deleted} 条（${fmtSize(res.bytes)}）`);
    if (res.errors && res.errors.length) appendLog("删除提示：" + res.errors.join("；"));
    state.selected.clear();
    await loadRecordings();
    const trash = await api("/api/trash");
    updateTrashInfo(trash.stats);
  } catch (err) {
    toast("删除失败：" + err.message);
  } finally {
    button.disabled = false;
  }
}

async function restoreTrash() {
  try {
    const res = await api("/api/trash/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    updateTrashInfo(res.stats);
    toast(`已恢复 ${res.restored} 项`);
    await loadRecordings();
  } catch (err) {
    toast("恢复失败：" + err.message);
  }
}

async function purgeTrash() {
  if (!window.confirm("彻底清空回收站？此操作不可恢复。")) return;
  try {
    const res = await api("/api/trash/purge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    });
    updateTrashInfo(res.stats);
    toast(`已清空回收站（释放 ${fmtSize(res.freed)}）`);
  } catch (err) {
    toast("清空失败：" + err.message);
  }
}

function span(className, text) {
  const node = document.createElement("span");
  node.className = className;
  node.textContent = text;
  return node;
}

function toggleGroup(game, wrap) {
  const children = wrap.querySelector(".group-items");
  const caret = wrap.querySelector(".caret");
  if (state.expanded.has(game)) {
    state.expanded.delete(game);
    children.hidden = true;
    caret.textContent = "▸";
  } else {
    state.expanded.add(game);
    children.hidden = false;
    caret.textContent = "▾";
    const group = state.groups.find((g) => g.game === game);
    if (group && !state.rendered.has(game)) renderChildren(group, children);
  }
}

function refreshGroupCheck(group, check) {
  let selected = 0;
  for (const item of group.items) if (state.selected.has(item.id)) selected++;
  check.checked = selected > 0 && selected === group.items.length;
  check.indeterminate = selected > 0 && selected < group.items.length;
}

function refreshAllChecks() {
  const container = el("groups");
  for (const group of state.groups) {
    const wrap = container.querySelector(`.group[data-game="${CSS.escape(group.game)}"]`);
    if (!wrap) continue;
    const groupCheck = wrap.querySelector(".group-check");
    if (groupCheck) refreshGroupCheck(group, groupCheck);
    wrap.querySelectorAll(".item-row").forEach((row) => {
      const cb = row.querySelector(".item-check");
      if (cb) cb.checked = state.selected.has(row.dataset.id);
    });
  }
}

function updateSelectionInfo() {
  el("selection-info").textContent = `已选 ${state.selected.size} 条`;
  el("export-selected").disabled = state.selected.size === 0;
  el("delete-selected").disabled = state.selected.size === 0;
}

function updateGroupSummary(group, node) {
  if (!node) return;
  let done = 0, failed = 0, running = 0;
  for (const item of group.items) {
    const status = state.statusById.get(item.id);
    if (!status) continue;
    if (status.status === "done") done++;
    else if (status.status === "failed") failed++;
    else if (status.status === "running") running++;
  }
  node.innerHTML = "";
  if (running) node.append(chip("run", `导出中 ${running}`));
  if (done) node.append(chip("ok", `✓ ${done}`));
  if (failed) node.append(chip("err", `✗ ${failed}`));
}

function chip(kind, text) {
  const node = document.createElement("span");
  node.className = `chip ${kind}`;
  node.textContent = text;
  return node;
}

function setItemStatus(id, status, text) {
  state.statusById.set(id, { status, text });
  const row = el("groups").querySelector(`.item-row[data-id="${CSS.escape(id)}"]`);
  if (!row) return;
  row.classList.remove("running", "done", "failed");
  if (status) row.classList.add(status);
  const statusNode = row.querySelector(".item-status");
  if (statusNode) statusNode.textContent = text || "";
  const wrap = row.closest(".group");
  if (wrap) {
    const group = state.groups.find((g) => g.game === wrap.dataset.game);
    updateGroupSummary(group, wrap.querySelector(".group-summary"));
  }
}

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------
function collectOptions() {
  return {
    user: el("user-select").value || "auto",
    mode: el("mode").value,
    codec: el("codec").value,
    crf: Number(el("crf").value) || 23,
    preset: el("preset").value,
    scale: el("scale").value.trim(),
    jobs: Number(el("jobs").value) || 1,
    faststart: el("faststart").checked,
    trim: el("trim").checked,
    force: el("force").checked,
    dry_run: el("dry-run").checked,
    out: el("out-dir").value.trim(),
    template: el("template").value.trim(),
  };
}

async function doExport(useSelected) {
  const payload = collectOptions();
  if (useSelected) {
    payload.ids = Array.from(state.selected);
  } else {
    const f = currentFilters();
    Object.assign(payload, f);
    payload.all = true;
  }

  try {
    const res = await api("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (payload.dry_run) { showPlan(res); return; }
    state.pendingDownload = null;
    toast(`已开始导出：${res.planned} 条`);
    startJob(res.job_id, res.planned, res.total_size);
  } catch (err) {
    toast("导出失败：" + err.message);
  }
}

function showPlan(res) {
  document.body.classList.add("job-active");
  el("job-panel").hidden = false;
  setJobStatus("done", "预演完成");
  el("overall-bar").style.width = "100%";
  el("overall-text").textContent = `计划 ${res.count} 条 · 预计 ${fmtSize(res.total_size)}`;
  el("item-text").textContent = "仅预演，未写文件";
  el("cancel-job").disabled = true;
  const lines = res.items.slice(0, 200).map(
    (it) => `${it.source.game}  ${it.source.recorded_display}  ->  ${it.output}`);
  el("log").textContent = lines.join("\n") + (res.items.length > 200 ? "\n…" : "");
  setLogOpen(true);
}

function startJob(jobId, planned, totalSize, isDownload = false) {
  state.jobId = jobId;
  state.planned = planned;
  state.done = 0;
  state.failed = 0;
  state.skipped = 0;
  state.bytes = 0;

  el("job-panel").hidden = false;
  document.body.classList.add("job-active");
  setLogOpen(false);
  el("log").textContent = "";
  el("cancel-job").disabled = false;
  el("item-bar").style.width = "0%";
  el("overall-bar").style.width = "0%";
  el("item-text").textContent = isDownload ? "正在准备压缩…" : "等待开始…";
  el("overall-text").textContent = `0 / ${planned} · 输入约 ${fmtSize(totalSize)}`;
  setJobStatus("run", "运行中");

  if (state.es) state.es.close();
  const es = new EventSource(withToken(`/api/jobs/${jobId}/events`));
  state.es = es;
  es.onmessage = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch (e) { return; }
    handleJobEvent(data);
  };
  es.onerror = () => { /* 结束时主动 close */ };
}

function handleJobEvent(ev) {
  switch (ev.type) {
    case "start":
      state.planned = ev.planned;
      updateOverall();
      break;
    case "item_skipped":
      state.skipped++;
      setItemStatus(ev.source.id, "done", "已存在，跳过");
      appendLog(`跳过（已存在）：${ev.source.game} ${ev.source.recorded_display}`);
      updateOverall();
      break;
    case "item_warning":
      if (ev.source) setItemStatus(ev.source.id, "running", "已恢复");
      appendLog(`⚠ ${ev.source ? ev.source.id : ""} ${ev.message || ""}`);
      break;
    case "item_start":
      if (ev.source) {
        state.currentName = `${ev.source.game} · ${ev.source.recorded_display}`;
        state.currentTotal = ev.source.duration_s || null;
        setItemStatus(ev.source.id, "running", "导出中…");
        el("item-text").textContent = state.currentName;
        el("item-bar").style.width = "0%";
      }
      break;
    case "progress": {
      const pct = ev.fraction == null ? null : Math.round(ev.fraction * 100);
      if (pct != null) el("item-bar").style.width = pct + "%";
      const parts = [state.currentName];
      if (pct != null) parts.push(pct + "%");
      if (ev.seconds != null) {
        parts.push(fmtDur(ev.seconds) + (state.currentTotal ? "/" + fmtDur(state.currentTotal) : ""));
      }
      if (ev.speed) parts.push(ev.speed.toFixed(1) + "x");
      el("item-text").textContent = parts.filter(Boolean).join("  ");
      break;
    }
    case "item_done":
      state.done++;
      state.bytes += ev.size || 0;
      setItemStatus(ev.source.id, "done", "✓ 完成");
      appendLog(`✓ ${ev.output}  (${fmtSize(ev.size)})`);
      updateOverall();
      break;
    case "item_failed":
      state.failed++;
      if (ev.source) setItemStatus(ev.source.id, "failed", "✗ 失败");
      appendLog(`✗ 失败：${ev.source ? ev.source.id : ""} ${ev.error || ""}`);
      setLogOpen(true);
      updateOverall();
      break;
    case "cancelled":
      appendLog("已取消当前任务。");
      break;
    case "error":
      appendLog("错误：" + ev.message);
      setLogOpen(true);
      break;
    case "finished":
      appendLog(
        `完成：成功 ${ev.exported}，跳过 ${ev.skipped}，失败 ${ev.failed}` +
        (ev.cancelled ? "，已取消" : "") + `，输出 ${fmtSize(ev.bytes)}`);
      break;
    case "end":
      finishJob(ev.status);
      break;
    default:
      break;
  }
}

function updateOverall() {
  const processed = state.done + state.failed + state.skipped;
  const pct = state.planned ? Math.round((processed / state.planned) * 100) : 0;
  el("overall-bar").style.width = pct + "%";
  el("overall-text").textContent =
    `${processed} / ${state.planned} · 成功 ${state.done} 失败 ${state.failed} 跳过 ${state.skipped} · 输出 ${fmtSize(state.bytes)}`;
}

function finishJob(status) {
  if (state.es) { state.es.close(); state.es = null; }
  el("cancel-job").disabled = true;
  el("item-bar").style.width = "100%";
  if (status === "done" && state.pendingDownload) {
    const key = state.pendingDownload.key;
    state.pendingDownload = null;
    setJobStatus("done", "准备完成");
    triggerFileDownload(key);
    toast("准备完成，开始下载");
    return;
  }
  state.pendingDownload = null;
  if (status === "cancelled") { setJobStatus("warn", "已取消"); toast("任务已取消"); }
  else if (status === "failed") { setJobStatus("err", "有失败"); toast("部分导出失败，见日志"); }
  else { setJobStatus("done", "完成"); toast("导出完成"); }
}

function setJobStatus(kind, text) {
  const cls = { done: "badge ok", err: "badge err", run: "badge run", warn: "badge" }[kind] || "badge";
  const node = el("job-status");
  node.className = cls;
  node.textContent = text;
}

function appendLog(line) {
  const node = el("log");
  node.textContent += (node.textContent ? "\n" : "") + line;
  node.scrollTop = node.scrollHeight;
}

function setLogOpen(open) {
  el("log").hidden = !open;
  el("toggle-log").textContent = open ? "收起日志" : "日志";
}

async function cancelJob() {
  if (!state.jobId) return;
  try {
    await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    toast("正在取消…");
  } catch (err) {
    toast("取消失败：" + err.message);
  }
}

async function revealOutput() {
  const path = el("out-dir").value.trim() || state.outDir;
  try {
    await api("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch (err) {
    toast("打开失败：" + err.message);
  }
}

init();
