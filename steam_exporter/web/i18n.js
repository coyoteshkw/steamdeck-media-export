/* 极简 i18n
 *
 * 思路：中文原文作为 key，英文词典 + 少量正则重写。
 * - 静态 HTML 与 JS 动态插入的文本都由 MutationObserver 自动翻译（按整段文本匹配），
 *   所以 app.js / index.html 不需要到处包 t()。
 * - 带数字/体积等插值的句子用 REWRITES 里的正则处理。
 * - 语言切换后直接刷新页面（URL 里已保存当前视图）。
 */
(function () {
  const STORAGE_KEY = "steam-export-lang";
  const saved = localStorage.getItem(STORAGE_KEY);
  const systemZh = (navigator.language || "").toLowerCase().startsWith("zh");
  const LANG = saved === "zh" || saved === "en" ? saved : systemZh ? "zh" : "en";

  const EN = {
    // 顶栏 / 通用
    "Steam 媒体导出": "Steam Media Export",
    "录像": "Recordings",
    "截图": "Screenshots",
    "筛选": "Filters",
    "☰ 筛选": "☰ Filters",
    "筛选 / 设置": "Filters / Settings",
    "账号": "Account",
    "关闭": "Close",
    "上一张": "Previous",
    "下一张": "Next",
    "加载中…": "Loading…",
    "加载更多": "Load more",
    "语言": "Language",

    // 导出设置
    "导出设置": "Export settings",
    "输出目录": "Output folder",
    "模式": "Mode",
    "无损复制（快）": "Lossless copy (fast)",
    "压缩转码（小）": "Re-encode (smaller)",
    "编码器": "Encoder",
    "CRF / QP（越小越清晰）": "CRF / QP (lower = better)",
    "预设": "Preset",
    "缩放": "Scale",
    "留空 = 原始分辨率": "Empty = original resolution",
    "并发数": "Concurrency",
    "命名模板": "Naming template",
    "裁剪尾部": "Trim tail",
    "强制重导": "Force re-export",
    "仅预演": "Dry run",
    "导出选中": "Export selected",
    "导出全部匹配": "Export all matches",
    "取消任务": "Cancel job",
    "选择": "Select",
    "已选 0 条": "0 selected",

    // 下载 / 缓存
    "下载到本机": "Download to this device",
    "下载画质": "Download quality",
    "原画无损（最快、体积大）": "Original (fastest, large)",
    "快速压缩（veryfast）": "Fast (veryfast)",
    "均衡压缩（medium）": "Balanced (medium)",
    "小体积（720p / CRF26）": "Small (720p / CRF26)",
    "硬件压缩（VAAPI、低 CPU）": "Hardware (VAAPI, low CPU)",
    "点列表每行右侧的下载按钮，直接下到本机；": "Use the download button on each row to save to this device;",
    "不会在 Deck 上保存": "Not saved on the Deck",
    "下载": "Download",
    "清理缓存": "Clear cache",
    "缓存：—": "Cache: —",

    // 回收站
    "删除与回收站": "Delete & Trash",
    "删除会移入回收站，可恢复；建议": "Moved to trash and can be restored; recommended:",
    "先关闭 Steam": "close Steam first",
    "回收站：—": "Trash: —",
    "恢复全部": "Restore all",
    "清空": "Purge",

    // 筛选
    "筛选与设置": "Filters & settings",
    "类型": "Type",
    "游戏关键词": "Game keyword",
    "排序": "Sort",
    "按最近录制": "By newest",
    "按体积": "By size",
    "按名称": "By name",
    "搜索 ID": "Search ID",
    "如 Outer Wilds": "e.g. Outer Wilds",
    "如 Hollow Knight": "e.g. Hollow Knight",
    "删除": "Delete",
    "删除会把录像移到回收站，可恢复；建议": "Recordings move to trash and can be restored; recommended:",
    "开始下载（无损流式）": "Downloading (lossless stream)",
    "开始下载（命中缓存）": "Downloading (cached)",
    "正在准备压缩，完成后自动开始下载…": "Preparing transcode; the download will start automatically…",
    "如 clip_753640_20260905_092200": "e.g. clip_753640_20260905_092200",
    "全选": "Select all",

    // 列表
    "剪辑": "Clips",
    "后台录像": "Background",
    "前台录像": "Foreground",
    "录像（前后台）": "Recordings (bg + fg)",
    "全部": "All",
    "没有匹配的录制。": "No matching recordings.",
    "展开/折叠": "Expand/collapse",
    "点击预览": "Click to preview",
    "在线播放": "Play online",
    "在线播放（最多前 5 分钟）": "Play online (first 5 minutes)",
    "下载到本机（不在 Deck 上保存）": "Download to this device (not saved on the Deck)",
    "删除该录制（默认移到回收站）": "Delete this recording (to trash by default)",
    "正在生成预览…": "Generating preview…",
    "无法生成预览（可能是录制损坏）": "Preview unavailable (recording may be corrupt)",
    "正在加载…（长录像只播放前 5 分钟）": "Loading… (first 5 minutes only)",
    "无法在线播放（录制可能损坏）": "Cannot play (recording may be corrupt)",
    "打开目录": "Open folder",
    "预览": "Preview",

    // 状态 / 日志
    "日志": "Log",
    "收起日志": "Hide log",
    "运行中": "Running",
    "预演完成": "Dry run complete",
    "仅预演，未写文件": "Dry run only, no files written",
    "等待开始…": "Waiting to start…",
    "正在准备压缩…": "Preparing transcode…",
    "准备完成": "Ready",
    "准备完成，开始下载": "Ready, starting download",
    "已取消": "Cancelled",
    "任务已取消": "Job cancelled",
    "有失败": "Some failed",
    "部分导出失败，见日志": "Some exports failed, see log",
    "完成": "Done",
    "导出完成": "Export complete",
    "导出中…": "Exporting…",
    "已存在，跳过": "Already exists, skipped",
    "已恢复": "Restored",
    "✓ 完成": "✓ Done",
    "✗ 失败": "✗ Failed",
    "已取消当前任务。": "Current job cancelled.",
    "正在取消…": "Cancelling…",
    "ffmpeg 就绪": "ffmpeg ready",
    "缺少 ffmpeg": "ffmpeg missing",

    // 删除确认
    "确认删除": "Confirm delete",
    "确认删除截图": "Confirm deleting screenshots",
    "彻底删除（不进回收站，不可恢复）": "Delete permanently (no trash, irreversible)",
    "默认移入回收站，可恢复。": "Moved to trash; can be restored.",

    // 截图
    "全部截图": "All screenshots",
    "全部游戏": "All games",
    "按分类": "By game",
    "游戏截图": "Game screenshots",
    "游戏关键词 / 标题": "Game keyword / caption",
    "只显示有标题的": "Only with captions",
    "起始日期": "Start date",
    "结束日期": "End date",
    "应用筛选": "Apply filters",
    "重置": "Reset",
    "筛选截图": "Filter screenshots",
    "批量下载": "Batch download",
    "打包下载全部结果": "Download all as ZIP",
    "打包成一个 ZIP 下载（原图，不压缩）。单选 / 多选仍是逐张下载。":
      "Download as one ZIP (originals, no recompression). Single/multi-select still downloads one by one.",
    "打包": "ZIP",
    "下载选中": "Download selected",
    "打包选中": "ZIP selected",
    "删除选中": "Delete selected",
    "取消": "Cancel",
    "取消选中": "Clear selection",
    "已选 0": "0 selected",
    "← 返回": "← Back",
    "没有匹配的截图。": "No matching screenshots.",
    "加载原图中…": "Loading original…",
    "原图加载失败": "Failed to load original",

    // 提示 / 错误
    "（主账号）": "(primary)",
    "（硬件）": "(hardware)",
    " · 支持硬件编码": " · hardware encoding",
    "缓存：未启用": "Cache: disabled",
    "回收站：未启用": "Trash: disabled",
    "彻底清空回收站？此操作不可恢复。": "Empty the trash? This cannot be undone.",
    "请先勾选要删除的截图": "Select screenshots to delete first",
    "请先勾选要删除的录制": "Select recordings to delete first",
    "没有可下载的截图": "No screenshots to download",
    "没有选中截图": "No screenshots selected",
  };

  // 带插值的句子：正则 → 字符串或函数
  const REWRITES = [
    [/^未知应用 (\d+)$/, "Unknown app $1"],
    [
      /^(\d+) 个账号 · 版本 (.+?)( · 支持硬件编码)?$/,
      (m) => `${m[1]} accounts · version ${m[2]}` + (m[3] ? " · hardware encoding" : ""),
    ],
    [/^(\d+)（主账号） · (\d+) 剪辑$/, "$1 (primary) · $2 clips"],
    [/^(\d+) · (\d+) 剪辑$/, "$1 · $2 clips"],
    [/^(.+)（硬件）$/, "$1 (hardware)"],
    [/^共 (\d+) 条 · (\d+) 个游戏 · (.+)$/, "$1 items · $2 games · $3"],
    [/^剪辑 (\d+)$/, "Clips $1"],
    [/^后台 (\d+)$/, "Background $1"],
    [/^录像 (\d+)$/, "Foreground $1"],
    [/^下载 (\d+)项\/(.+)$/, "Download $1 / $2"],
    [/^预览 (\d+)项\/(.+)$/, "Preview $1 / $2"],
    [/^缓存：(.*)$/, (m) => `Cache: ${m[1].replace(/(\d+) 项 \/ /, "$1 items / ")}`],
    [/^回收站：(.+?) 项 \/ (.+)$/, "Trash: $1 items / $2"],
    [/^已清理 (\d+) 个缓存文件$/, "Cleared $1 cache files"],
    [/^已恢复 (\d+) 项$/, "Restored $1 items"],
    [/^已清空回收站（释放 (.+)）$/, "Trash emptied (freed $1)"],
    [/^已选 (\d+) 条$/, "$1 selected"],
    [/^导出中 (.+)$/, "Exporting $1"],
    [/^已开始导出：(\d+) 条$/, "Export started: $1 items"],
    [/^计划 (\d+) 条 · 预计 (.+)$/, "Planned $1 items · ~$2"],
    [/^0 \/ (\d+) · 输入约 (.+)$/, "0 / $1 · input ~$2"],
    [/^跳过（已存在）：(.+)$/, "Skipped (exists): $1"],
    [/^✗ 失败：(.+)$/, "✗ Failed: $1"],
    [
      /^完成：成功 (\d+)，跳过 (\d+)，失败 (\d+)(，已取消)?(，输出 (.+))?$/,
      (m) =>
        `Done: ${m[1]} succeeded, ${m[2]} skipped, ${m[3]} failed` +
        (m[4] ? ", cancelled" : "") +
        (m[5] ? `, output ${m[6]}` : ""),
    ],
    [
      /^(\d+) \/ (\d+) · 成功 (\d+) 失败 (\d+) 跳过 (\d+) · 输出 (.+)$/,
      "$1 / $2 · ok $3 failed $4 skipped $5 · output $6",
    ],
    [/^全部：(\d+) 张 · (\d+) 个游戏 · (.+)$/, "All: $1 shots · $2 games · $3"],
    [/^(\d+) 个游戏 · (\d+) 张 · (.+)$/, "$1 games · $2 shots · $3"],
    [/^(\d+) 张 · (.+)$/, "$1 shots · $2"],
    [/^加载更多（已显示 (\d+) \/ (\d+)）$/, "Load more (showing $1 / $2)"],
    [/^选择（已选 (\d+)）$/, "Select ($1 selected)"],
    [/^完成（已选 (\d+)）$/, "Done ($1 selected)"],
    [/^下载选中（(\d+)）$/, "Download selected ($1)"],
    [/^打包选中（(\d+)）$/, "ZIP selected ($1)"],
    [/^删除选中（(\d+)）$/, "Delete selected ($1)"],
    [/^已选 (\d+)$/, "$1 selected"],
    [
      /^将逐张下载 (\d+) 张（约 (.+)）。继续？$/,
      "Download $1 screenshots one by one (~$2). Continue?",
    ],
    [
      /^开始下载 (\d+) 张…（浏览器可能询问是否允许多个文件）$/,
      "Downloading $1 files… (your browser may ask to allow multiple downloads)",
    ],
    [/^打包 (\d+) 张（约 (.+)）为 ZIP 下载？$/, "Download $1 screenshots (~$2) as a ZIP?"],
    [
      /^将删除 (\d+) 张截图，约 (.+)。默认移入回收站，可恢复。$/,
      "Will delete $1 screenshots (~$2). They go to trash and can be restored.",
    ],
    [
      /^将删除 (\d+) 条录制（剪辑 (\d+)、录像 (\d+)），约 (.+)。(默认移入回收站，可恢复。)?\s*(⚠ 检测到 Steam 正在运行，建议先关闭 Steam 再删除。)?$/,
      (m) =>
        `Will delete ${m[1]} recordings (${m[2]} clips, ${m[3]} recordings), ~${m[4]}.` +
        (m[5] ? " Moved to trash; can be restored." : "") +
        (m[6] ? " ⚠ Steam is running; close it before deleting." : ""),
    ],
    [
      /^已移入回收站 (\d+) [张条]（(.+)）$/,
      (m) => `Moved to trash: ${m[1]} items (${m[2]})`,
    ],
    [
      /^已彻底删除 (\d+) [张条]（(.+)）$/,
      (m) => `Permanently deleted: ${m[1]} items (${m[2]})`,
    ],
    [/^加载失败：(.+)$/, "Load failed: $1"],
    [/^下载失败：(.+)$/, "Download failed: $1"],
    [/^清理失败：(.+)$/, "Clear failed: $1"],
    [/^删除失败：(.+)$/, "Delete failed: $1"],
    [/^恢复失败：(.+)$/, "Restore failed: $1"],
    [/^清空失败：(.+)$/, "Purge failed: $1"],
    [/^导出失败：(.+)$/, "Export failed: $1"],
    [/^取消失败：(.+)$/, "Cancel failed: $1"],
    [/^打开失败：(.+)$/, "Open failed: $1"],
    [/^无法连接后端：(.+)$/, "Cannot reach backend: $1"],
    [/^错误：(.+)$/, "Error: $1"],
    [/^删除提示：(.+)$/, "Notice: $1"],
  ];

  function translate(text) {
    if (LANG !== "en" || !text) return text;
    const key = text.trim();
    if (!key) return text;
    let out = null;
    if (Object.prototype.hasOwnProperty.call(EN, key)) {
      out = EN[key];
    } else {
      for (const [re, rep] of REWRITES) {
        const m = re.exec(key);
        if (m) {
          out = typeof rep === "function" ? rep(m) : key.replace(re, rep);
          break;
        }
      }
    }
    return out === null ? text : text.replace(key, out);
  }

  function translateNode(node) {
    if (!node || node.nodeType !== 3) return;
    const parent = node.parentNode;
    if (!parent || parent.nodeName === "SCRIPT" || parent.nodeName === "STYLE") return;
    const next = translate(node.nodeValue);
    if (next !== node.nodeValue) node.nodeValue = next;
  }

  function translateTree(root) {
    if (!root) return;
    if (root.nodeType === 3) {
      translateNode(root);
      return;
    }
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const node of nodes) translateNode(node);
  }

  function translateAttributes(root) {
    if (LANG !== "en") return;
    for (const el of root.querySelectorAll("[title],[placeholder],[aria-label]")) {
      for (const attr of ["title", "placeholder", "aria-label"]) {
        const value = el.getAttribute(attr);
        if (!value) continue;
        const next = translate(value);
        if (next !== value) el.setAttribute(attr, next);
      }
    }
  }

  function apply() {
    document.documentElement.lang = LANG === "en" ? "en" : "zh-CN";
    document.title = LANG === "en" ? "Steam Media Export" : "Steam 媒体导出";
    const toggle = document.getElementById("lang-toggle");
    if (toggle) {
      toggle.textContent = LANG === "en" ? "中文" : "EN";
      toggle.title = LANG === "en" ? "切换到中文" : "Switch to English";
    }
    if (LANG !== "en") return;
    translateTree(document.body);
    translateAttributes(document.body);
  }

  function setLang(lang) {
    localStorage.setItem(STORAGE_KEY, lang);
    location.reload();
  }

  window.I18N = {
    lang: LANG,
    t: (key) => (LANG === "en" ? (EN[key] ?? key) : key),
    pick: (zh, en) => (LANG === "en" ? en : zh),
    setLang,
    toggle: () => setLang(LANG === "zh" ? "en" : "zh"),
  };

  document.addEventListener("DOMContentLoaded", () => {
    apply();
    const toggle = document.getElementById("lang-toggle");
    if (toggle) toggle.addEventListener("click", () => window.I18N.toggle());
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.type === "characterData") {
          translateNode(m.target);
          continue;
        }
        for (const n of m.addedNodes) {
          if (n.nodeType === 3) translateNode(n);
          else if (n.nodeType === 1) translateTree(n);
        }
      }
    });
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      characterData: true,
    });
  });
})();
