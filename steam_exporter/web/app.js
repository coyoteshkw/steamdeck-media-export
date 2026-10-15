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
  selected: new Set(),
  jobId: null,
  es: null,
  planned: 0,
  done: 0,
  failed: 0,
  skipped: 0,
  bytes: 0,
  currentName: "",
  currentTotal: null,
  outDir: "",
  summary: null,
};

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
async function init() {
  try {
    const summary = await api("/api/summary");
    state.summary = summary;
    applySummary(summary);
  } catch (err) {
    el("summary-line").textContent = "无法连接后端：" + err.message;
    return;
  }
  bindEvents();
  await loadRecordings();
}

function applySummary(s) {
  // 账号
  const userSelect = el("user-select");
  userSelect.innerHTML = "";
  for (const user of s.users) {
    const opt = document.createElement("option");
    opt.value = user.id;
    opt.textContent = user.id + (user.primary ? "（主账号）" : "") + ` · ${user.clips} 剪辑`;
    userSelect.appendChild(opt);
  }
  if (s.users.some((u) => u.id === s.default_user)) userSelect.value = s.default_user;
  else if (s.users.some((u) => u.primary)) userSelect.value = s.users.find((u) => u.primary).id;

  // 编码器
  const codec = el("codec");
  codec.innerHTML = "";
  for (const name of s.encoders.software) codec.appendChild(new Option(name, name));
  for (const name of s.encoders.vaapi) {
    codec.appendChild(new Option(name + "（硬件）", name));
  }

  el("out-dir").value = s.default_out;
  el("template").value = s.default_template;
  state.outDir = s.default_out;

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
  el("select-all").addEventListener("change", (e) => {
    state.selected.clear();
    if (e.target.checked) for (const item of state.items) state.selected.add(item.id);
    renderTable();
    updateSelectionInfo();
  });
  el("export-selected").addEventListener("click", () => doExport(true));
  el("export-all").addEventListener("click", () => doExport(false));
  el("cancel-job").addEventListener("click", cancelJob);
  el("reveal-output").addEventListener("click", revealOutput);
}

function syncModeFields() {
  const compress = el("mode").value === "compress";
  for (const node of document.querySelectorAll(".compress-only")) {
    node.style.display = compress ? "" : "none";
  }
}

// ---------------------------------------------------------------------------
// 列表
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
    el("select-all").checked = false;
    renderTable();
    updateSelectionInfo();
    el("list-info").textContent = `共 ${data.count} 条 · 合计 ${fmtSize(data.total_size)}`;
  } catch (err) {
    el("list-info").textContent = "加载失败：" + err.message;
  }
}

function renderTable() {
  const body = el("table-body");
  body.innerHTML = "";
  el("empty-hint").hidden = state.items.length > 0;

  for (const item of state.items) {
    const tr = document.createElement("tr");
    tr.dataset.id = item.id;

    const tdCheck = document.createElement("td");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = state.selected.has(item.id);
    check.addEventListener("change", () => {
      if (check.checked) state.selected.add(item.id);
      else state.selected.delete(item.id);
      updateSelectionInfo();
    });
    tdCheck.appendChild(check);
    tr.appendChild(tdCheck);

    tr.appendChild(cell(`<span class="kind ${item.kind}">${item.kind_cn}</span>`));
    tr.appendChild(cell(escapeHtml(item.game)));
    tr.appendChild(cell(escapeHtml(item.recorded_display)));
    tr.appendChild(cell(fmtDur(item.duration_s)));
    tr.appendChild(cell(fmtSize(item.size)));

    const tdId = document.createElement("td");
    tdId.className = "mono";
    tdId.textContent = item.id;
    tr.appendChild(tdId);

    const tdStatus = document.createElement("td");
    tdStatus.className = "col-status status-cell";
    tr.appendChild(tdStatus);

    body.appendChild(tr);
  }
}

function cell(html) {
  const td = document.createElement("td");
  td.innerHTML = html;
  return td;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function rowFor(id) {
  return el("table-body").querySelector(`tr[data-id="${CSS.escape(id)}"]`);
}

function setRowStatus(id, status, text) {
  const row = rowFor(id);
  if (!row) return;
  row.classList.remove("running", "done", "failed");
  if (status) row.classList.add(status);
  const cellNode = row.querySelector(".status-cell");
  if (cellNode) cellNode.textContent = text || "";
}

function updateSelectionInfo() {
  el("selection-info").textContent = `已选 ${state.selected.size} 条`;
  el("export-selected").disabled = state.selected.size === 0;
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
    Object.assign(payload, currentFilters());
    payload.games = currentFilters().games;
    payload.all = true;
  }

  try {
    const res = await api("/api/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (payload.dry_run) {
      showPlan(res);
      return;
    }
    toast(`已开始导出：${res.planned} 条`);
    startJob(res.job_id, res.planned, res.total_size);
  } catch (err) {
    toast("导出失败：" + err.message);
  }
}

function showPlan(res) {
  el("job-panel").hidden = false;
  setJobStatus("done", "预演完成");
  el("overall-bar").style.width = "100%";
  el("overall-text").textContent = `计划 ${res.count} 条 · 预计 ${fmtSize(res.total_size)}`;
  el("item-text").textContent = "仅预演，未写文件";
  el("cancel-job").disabled = true;
  const lines = res.items.slice(0, 200).map((it) => `${it.source.game}  ${it.source.recorded_display}  ->  ${it.output}`);
  el("log").textContent = lines.join("\n") + (res.items.length > 200 ? "\n…" : "");
}

function startJob(jobId, planned, totalSize) {
  state.jobId = jobId;
  state.planned = planned;
  state.done = 0;
  state.failed = 0;
  state.skipped = 0;
  state.bytes = 0;

  el("job-panel").hidden = false;
  el("log").textContent = "";
  el("cancel-job").disabled = false;
  el("item-bar").style.width = "0%";
  el("overall-bar").style.width = "0%";
  el("item-text").textContent = "等待开始…";
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
  es.onerror = () => { /* 结束时会主动 close */ };
}

function handleJobEvent(ev) {
  switch (ev.type) {
    case "start":
      state.planned = ev.planned;
      updateOverall();
      break;
    case "item_skipped":
      state.skipped++;
      setRowStatus(ev.source.id, "done", "已存在，跳过");
      appendLog(`跳过（已存在）：${ev.source.game} ${ev.source.recorded_display}`);
      updateOverall();
      break;
    case "item_start":
      if (ev.source) {
        state.currentName = `${ev.source.game} · ${ev.source.recorded_display}`;
        state.currentTotal = ev.source.duration_s || null;
        setRowStatus(ev.source.id, "running", "导出中…");
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
      setRowStatus(ev.source.id, "done", "✓ 完成");
      appendLog(`✓ ${ev.output}  (${fmtSize(ev.size)})`);
      updateOverall();
      break;
    case "item_failed":
      state.failed++;
      if (ev.source) setRowStatus(ev.source.id, "failed", "✗ 失败");
      appendLog(`✗ 失败：${ev.source ? ev.source.id : ""} ${ev.error || ""}`);
      updateOverall();
      break;
    case "cancelled":
      appendLog("已取消当前任务。");
      break;
    case "error":
      appendLog("错误：" + ev.message);
      break;
    case "finished":
      appendLog(
        `完成：成功 ${ev.exported}，跳过 ${ev.skipped}，失败 ${ev.failed}` +
        (ev.cancelled ? "，已取消" : "") + `，输出 ${fmtSize(ev.bytes)}`
      );
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
