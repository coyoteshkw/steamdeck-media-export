import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  definePlugin,
  staticClasses,
} from "@decky/ui";
import { call, useQuickAccessVisible } from "@decky/api";
import qrcode from "qrcode-generator";

interface Status {
  running: boolean;
  port: number;
  configured_port: number;
  ip: string;
  url: string;
  token: string;
  project_dir: string;
  source: string;
  project_ok: boolean;
  out_dir: string;
  hints: Record<string, string>;
  error: string | null;
}

// 跟随系统/Steam 语言（CEF 的 navigator.language）
const LANG: "zh" | "en" = (navigator.language || "").toLowerCase().startsWith("zh")
  ? "zh"
  : "en";

const TEXT = {
  zh: {
    title: "Steam 媒体导出",
    enable: "启用服务",
    running: (ip: string, port: number) => `运行中 · ${ip}:${port}`,
    stopped: "已停止",
    notice: "提示",
    urlLabel: "手机访问地址",
    urlStopped: "（未启动）",
    urlPending: "打开开关后显示地址",
    qrLabel: "扫码打开",
    qrAlt: "访问二维码",
    port: "端口",
    portDesc: "默认 8787，被占用时自动顺延",
    applyPort: "应用端口并重启",
    savePort: "保存端口",
    source: "代码来源",
    projectDir: "项目目录",
    notFound: "未找到：",
    hints: {
      out: "输出目录",
      cache: "下载缓存",
      trash: "回收站",
      preview: "预览缓存",
    } as Record<string, string>,
    sources: {
      bundled: "内置（自包含）",
      repo: "本地仓库",
      custom: "自定义路径",
    } as Record<string, string>,
  },
  en: {
    title: "Steam Media Export",
    enable: "Enable service",
    running: (ip: string, port: number) => `Running · ${ip}:${port}`,
    stopped: "Stopped",
    notice: "Notice",
    urlLabel: "Mobile URL",
    urlStopped: " (stopped)",
    urlPending: "Turn on the switch to show the URL",
    qrLabel: "Scan to open",
    qrAlt: "QR code",
    port: "Port",
    portDesc: "Default 8787; auto-increments if busy",
    applyPort: "Apply port & restart",
    savePort: "Save port",
    source: "Code source",
    projectDir: "Project folder",
    notFound: "Not found: ",
    hints: {
      out: "Output folder",
      cache: "Download cache",
      trash: "Trash",
      preview: "Preview cache",
    } as Record<string, string>,
    sources: {
      bundled: "Bundled (self-contained)",
      repo: "Local repo",
      custom: "Custom path",
    } as Record<string, string>,
  },
};
const L = TEXT[LANG];
const HINT_KEYS = ["out", "cache", "trash", "preview"];

const small: CSSProperties = {
  fontSize: "11px",
  lineHeight: 1.4,
  wordBreak: "break-all",
  userSelect: "text",
  opacity: 0.9,
};

const QrIcon = () => (
  <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" aria-hidden="true">
    <path d="M3 3h8v8H3V3zm2 2v4h4V5H5zm8-2h8v8h-8V3zm2 2v4h4V5h-4zM3 13h8v8H3v-8zm2 2v4h4v-4H5zm11-1h2v2h-2v-2zm-3 0h2v2h-2v-2zm5 3h2v2h-2v-2zm-2 2h2v2h-2v-2zm-3 0h2v2h-2v-2zm4 3h2v2h-2v-2z" />
  </svg>
);

function Content() {
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [portInput, setPortInput] = useState("8787");
  const visible = useQuickAccessVisible();
  // 用户正在改端口时，别被轮询结果覆盖
  const portDirty = useRef(false);

  const refresh = async () => {
    const next = await call<[string], Status>("get_status", LANG);
    setStatus(next);
    if (!portDirty.current) {
      setPortInput(String(next.configured_port ?? next.port));
    }
    return next;
  };

  const running = !!status?.running;

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // 只在「侧栏可见 + 服务在跑」时轮询：关掉面板或服务已停都不需要轮询
    if (!visible || !running) return;
    const timer = window.setInterval(() => {
      refresh();
    }, 4000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, running]);

  const qrDataUrl = useMemo(() => {
    if (!status?.url) return "";
    try {
      const qr = qrcode(0, "M");
      qr.addData(status.url);
      qr.make();
      return qr.createDataURL(4, 8);
    } catch (err) {
      return "";
    }
  }, [status?.url]);

  const onToggle = async (checked: boolean) => {
    setBusy(true);
    try {
      if (checked) {
        await call<[number, string], Status>(
          "start_server", Number(portInput) || 8787, LANG);
      } else {
        await call<[string], Status>("stop_server", LANG);
      }
      portDirty.current = false;
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const applyPort = async () => {
    setBusy(true);
    try {
      await call<[number, string], Status>("set_port", Number(portInput) || 8787, LANG);
      portDirty.current = false;
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const portChanged = status ? Number(portInput) !== status.configured_port : false;

  return (
    <PanelSection title={L.title} spinner={busy}>
      <PanelSectionRow>
        <ToggleField
          label={L.enable}
          description={
            running ? L.running(status?.ip || "", status?.port || 0) : L.stopped
          }
          checked={running}
          disabled={busy || status?.project_ok === false}
          onChange={onToggle}
        />
      </PanelSectionRow>

      {status?.error ? (
        <PanelSectionRow>
          <Field label={L.notice} bottomSeparator="none" childrenLayout="below">
            <div style={{ ...small, color: "#ff9a9a" }}>{status.error}</div>
          </Field>
        </PanelSectionRow>
      ) : null}

      <PanelSectionRow>
        <Field label={L.urlLabel} bottomSeparator="none" childrenLayout="below">
          <div style={{ ...small, opacity: running ? 0.9 : 0.5 }}>
            {status?.url
              ? `${status.url}${running ? "" : L.urlStopped}`
              : L.urlPending}
          </div>
        </Field>
      </PanelSectionRow>

      {running && qrDataUrl ? (
        <PanelSectionRow>
          <Field label={L.qrLabel} bottomSeparator="none" childrenLayout="below">
            <div style={{ display: "flex", justifyContent: "center", width: "100%" }}>
              <img
                src={qrDataUrl}
                alt={L.qrAlt}
                width={170}
                height={170}
                style={{
                  background: "#fff",
                  padding: "6px",
                  borderRadius: "6px",
                  imageRendering: "pixelated",
                }}
              />
            </div>
          </Field>
        </PanelSectionRow>
      ) : null}

      <PanelSectionRow>
        <TextField
          label={L.port}
          value={portInput}
          mustBeNumeric
          disabled={busy}
          description={L.portDesc}
          onChange={(e) => {
            portDirty.current = true;
            setPortInput(e.target.value);
          }}
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || !portChanged} onClick={applyPort}>
          {running ? L.applyPort : L.savePort}
        </ButtonItem>
      </PanelSectionRow>

      {HINT_KEYS.map((key) =>
        status?.hints?.[key] ? (
          <PanelSectionRow key={key}>
            <Field label={L.hints[key]} bottomSeparator="none" childrenLayout="below">
              <div style={small}>{status.hints[key]}</div>
            </Field>
          </PanelSectionRow>
        ) : null
      )}

      <PanelSectionRow>
        <Field label={L.source} bottomSeparator="none" childrenLayout="below">
          <div style={small}>
            {L.sources[status?.source ?? ""] ?? "—"}　{status?.project_dir}
          </div>
        </Field>
      </PanelSectionRow>

      {status?.project_ok === false ? (
        <PanelSectionRow>
          <Field label={L.projectDir} bottomSeparator="none" childrenLayout="below">
            <div style={{ ...small, color: "#ff9a9a" }}>
              {L.notFound}
              {status.project_dir}
            </div>
          </Field>
        </PanelSectionRow>
      ) : null}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "SteamMediaExport",
  title: <div className={staticClasses.Title}>{L.title}</div>,
  content: <Content />,
  icon: <QrIcon />,
  onDismount() {},
}));
