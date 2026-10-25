"""Decky 插件后端：开关 Steam 录制/截图管理页面。

设计（B2）：
- 本文件运行在 Decky 自带的 Python 里，**只负责**用系统 ``/usr/bin/python3``
  以子进程方式启动项目里的 ``steam_exporter serve``。
- 项目代码（``~/code-study/steam-recording-exporter``）完全不被改动，也无需重新打包。
- 默认关闭、不记忆状态；端口冲突时自动顺延；token 默认 ``hello``。
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
from collections import deque
from pathlib import Path
from typing import Any, Optional

import decky  # type: ignore

# ---------------------------------------------------------------------------
# 默认值（与主程序 serve 的默认保持一致）
# ---------------------------------------------------------------------------
PORT_DEFAULT = 8787
TOKEN_DEFAULT = "hello"
PROJECT_DEFAULT = str(Path.home() / "code-study" / "steam-recording-exporter")
OUT_DEFAULT = str(Path.home() / "Videos" / "SteamExports")
PORT_TRIES = 50  # 端口冲突时最多往后试多少个


def _plugin_dir() -> Optional[Path]:
    """Decky 注入的插件目录（取不到时返回 None）。"""
    value = getattr(decky, "DECKY_PLUGIN_DIR", "") or os.environ.get("DECKY_PLUGIN_DIR", "")
    return Path(value) if value else None


def bundled_project() -> Optional[Path]:
    """自包含打包时，主程序代码放在 <插件目录>/vendor/。"""
    plugin_dir = _plugin_dir()
    if plugin_dir is None:
        return None
    vendor = plugin_dir / "vendor"
    return vendor if (vendor / "steam_exporter").is_dir() else None

# 启动输出里需要回显到面板的信息行
# (key, (中文前缀, English prefix)) —— 服务端启动时会同时打印中英两行
INFO_PREFIXES = (
    ("out", ("输出：", "Output: ")),
    ("cache", ("下载缓存：", "Download cache: ")),
    ("trash", ("回收站：", "Trash: ")),
    ("preview", ("预览缓存：", "Preview cache: ")),
)

MESSAGES = {
    "project_missing": {
        "zh": "项目目录不存在：{path}",
        "en": "Project folder not found: {path}",
    },
    "port_busy": {
        "zh": "端口 {preferred} 被占用，已自动改用 {actual}",
        "en": "Port {preferred} is busy; using {actual} instead",
    },
    "out_mkdir": {
        "zh": "无法创建输出目录：{error}",
        "en": "Cannot create output directory: {error}",
    },
    "start_failed": {
        "zh": "启动失败：{error}",
        "en": "Failed to start: {error}",
    },
    "exited": {
        "zh": "服务启动后立即退出：{tail}",
        "en": "Service exited immediately: {tail}",
    },
    "exited_no_tail": {
        "zh": "服务启动后立即退出",
        "en": "Service exited immediately",
    },
}


# ---------------------------------------------------------------------------
# settings：优先用 Decky 提供的 SettingsManager，取不到就用 JSON 文件兜底
# ---------------------------------------------------------------------------
try:  # pragma: no cover
    from settings import SettingsManager  # type: ignore
except Exception:  # pragma: no cover
    SettingsManager = None


class _JsonSettings:
    """SettingsManager 的极简兜底实现。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        self.read()

    def read(self) -> dict[str, Any]:
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                self._data = json.load(handle)
        except Exception:
            self._data = {}
        return self._data

    def getSetting(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def setSetting(self, key: str, value: Any) -> None:
        self._data[key] = value

    def commit(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 网络辅助
# ---------------------------------------------------------------------------
def _lan_ips() -> list[str]:
    """探测本机局域网 IPv4（排除 127.*）。"""
    ips: set[str] = set()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.3)
        sock.connect(("8.8.8.8", 80))
        ips.add(sock.getsockname()[0])
        sock.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def _ip_rank(ip: str) -> int:
    """越小越优先：192.168.* > 172.16-31.* > 10.* > 其它。"""
    if ip.startswith("192.168."):
        return 0
    if ip.startswith("172."):
        try:
            if 16 <= int(ip.split(".")[1]) <= 31:
                return 1
        except (ValueError, IndexError):
            pass
    if ip.startswith("10."):
        return 2
    return 3


def pick_lan_ip() -> str:
    """探测本机局域网 IP。**只在启动服务时调用一次**（不轮询、不缓存）。"""
    ips = _lan_ips()
    if not ips:
        return "127.0.0.1"
    ips.sort(key=lambda ip: (_ip_rank(ip), ip))
    return ips[0]


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def find_free_port(preferred: int, tries: int = PORT_TRIES) -> int:
    """从 preferred 起找第一个空闲端口；找不到就原样返回。"""
    for port in range(preferred, preferred + tries):
        if _port_free(port):
            return port
    return preferred


# ---------------------------------------------------------------------------
# 插件本体
# ---------------------------------------------------------------------------
class Plugin:
    process: Optional[subprocess.Popen] = None
    port: int = PORT_DEFAULT
    ip: Optional[str] = None  # 只在 start_server 时探测一次
    lang: str = "zh"
    log_lines: deque = deque(maxlen=200)
    error: Optional[str] = None
    _reader: Optional[threading.Thread] = None
    _settings: Any = None

    # -- 设置 ---------------------------------------------------------------
    def _settings_manager(self):
        if self._settings is None:
            settings_dir = getattr(
                decky,
                "DECKY_PLUGIN_SETTINGS_DIR",
                os.path.join(
                    os.path.expanduser("~"), ".config", "steam-media-export"
                ),
            )
            try:
                os.makedirs(settings_dir, exist_ok=True)
            except OSError:
                pass
            if SettingsManager is not None:
                self._settings = SettingsManager(
                    name="settings", settings_directory=settings_dir
                )
                self._settings.read()
            else:
                self._settings = _JsonSettings(
                    os.path.join(settings_dir, "settings.json")
                )
        return self._settings

    def _get(self, key: str, default: Any) -> Any:
        value = self._settings_manager().getSetting(key, default)
        return default if value in (None, "") else value

    def _set(self, key: str, value: Any) -> None:
        manager = self._settings_manager()
        manager.setSetting(key, value)
        manager.commit()

    def _resolve_project(self) -> tuple[str, str]:
        """返回 (主程序目录, 来源)。优先级：显式设置 > 内置 vendor > 默认仓库路径。

        - ``custom``：用户在设置里指定了 project_dir
        - ``bundled``：自包含包里的内置代码（分发用，不依赖仓库）
        - ``repo``：默认的本地仓库路径（开发用）
        """
        explicit = self._settings_manager().getSetting("project_dir", None)
        if explicit:
            return str(Path(explicit).expanduser()), "custom"
        bundled = bundled_project()
        if bundled is not None:
            return str(bundled), "bundled"
        return str(Path(PROJECT_DEFAULT).expanduser()), "repo"

    def _config(self) -> dict[str, str]:
        project, source = self._resolve_project()
        return {
            "project": project,
            "source": source,
            "out": str(Path(self._get("out_dir", OUT_DEFAULT)).expanduser()),
            "token": str(self._get("token", TOKEN_DEFAULT)),
        }

    # -- 进程状态 -----------------------------------------------------------
    def _alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _drain_output(self) -> None:
        try:
            assert self.process is not None and self.process.stdout is not None
            for line in iter(self.process.stdout.readline, ""):
                if not line:
                    break
                self.log_lines.append(line.rstrip("\n"))
        except Exception:
            pass

    def _set_lang(self, lang: Optional[str]) -> None:
        if lang:
            self.lang = "en" if str(lang).lower().startswith("en") else "zh"

    def _msg(self, key: str, **values) -> str:
        item = MESSAGES[key]
        template = item.get(self.lang) or item["zh"]
        return template.format(**values)

    def _hints(self) -> dict[str, str]:
        found: dict[str, str] = {}
        index = 1 if self.lang == "en" else 0
        for line in self.log_lines:
            text = line.strip()
            for key, prefixes in INFO_PREFIXES:
                prefix = prefixes[index]
                if key not in found and text.startswith(prefix):
                    found[key] = text[len(prefix):].strip()
        return found

    # -- 对外方法（前端 call 调用）------------------------------------------
    async def get_status(self, lang: Optional[str] = None) -> dict:
        self._set_lang(lang)
        config = self._config()
        running = self._alive()
        port = self.port if running else int(self._get("port", PORT_DEFAULT))
        token = config["token"]
        query = f"?token={token}" if token else ""
        ip = self.ip
        return {
            "running": running,
            "port": port,
            "configured_port": int(self._get("port", PORT_DEFAULT)),
            "ip": ip or "",
            "url": f"http://{ip}:{port}/{query}" if ip else "",
            "token": token,
            "project_dir": config["project"],
            "source": config["source"],
            "project_ok": Path(config["project"]).is_dir(),
            "out_dir": config["out"],
            "hints": self._hints(),
            "error": self.error,
        }

    async def start_server(self, port: Optional[int] = None, lang: Optional[str] = None) -> dict:
        self._set_lang(lang)
        self.error = None
        # 只在启动时探测一次本机 IP；之后 get_status 直接用它，不再碰网络
        self.ip = pick_lan_ip()
        config = self._config()
        project = Path(config["project"])
        if not project.is_dir():
            self.error = self._msg("project_missing", path=project)
            return await self.get_status()
        if self._alive():
            return await self.get_status()

        preferred = int(port or self._get("port", PORT_DEFAULT))
        if port is not None:
            self._set("port", int(port))
        actual = find_free_port(preferred)
        self.port = actual
        if actual != preferred:
            self.error = self._msg("port_busy", preferred=preferred, actual=actual)

        # 输出目录不存在就帮忙创建
        try:
            Path(config["out"]).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.error = self._msg("out_mkdir", error=exc)

        env = os.environ.copy()
        # Decky 插件后端跑在 PyInstaller 里，环境里的 LD_LIBRARY_PATH 指向 _MEI 目录，
        # 那里带的旧 libstdc++ 会让系统 ffmpeg 报「GLIBCXX_x.x.x not found」
        # （libopenmpt / libx265 / librubberband 等）。子进程是系统 Python，
        # 不需要这些库路径，直接去掉。
        for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME"):
            env.pop(key, None)
        env["PATH"] = "/usr/bin:/usr/local/bin:" + env.get("PATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = config["project"] + (
            ":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
        )
        cmd = [
            "/usr/bin/python3",
            "-u",
            "-m",
            "steam_exporter",
            "serve",
            "--no-open",
            "--host",
            "0.0.0.0",
            "--port",
            str(actual),
            "--out",
            config["out"],
        ]
        if config["token"]:
            cmd += ["--token", config["token"]]

        self.log_lines = deque(maxlen=200)
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=config["project"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.process = None
            self.error = self._msg("start_failed", error=exc)
            return await self.get_status()

        self._reader = threading.Thread(target=self._drain_output, daemon=True)
        self._reader.start()

        # 等启动信息打印出来（最多约 3 秒）
        for _ in range(30):
            await asyncio.sleep(0.1)
            if not self._alive():
                break
            if self._hints().get("out"):
                break
        if not self._alive():
            tail = " / ".join(list(self.log_lines)[-3:])
            self.error = self._msg("exited", tail=tail) if tail else self._msg("exited_no_tail")
        return await self.get_status()

    async def stop_server(self, lang: Optional[str] = None) -> dict:
        self._set_lang(lang)
        if self._alive():
            assert self.process is not None
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        return await self.get_status()

    async def set_port(self, port: int, lang: Optional[str] = None) -> dict:
        self._set_lang(lang)
        self._set("port", int(port))
        if self._alive():
            await self.stop_server()
            return await self.start_server(int(port))
        return await self.get_status()

    # -- 生命周期 -----------------------------------------------------------
    async def _main(self) -> None:
        self.log_lines = deque(maxlen=200)
        self.port = int(self._get("port", PORT_DEFAULT))
        decky.logger.info(
            "[SteamMediaExport] 已加载，项目=%s 端口=%s"
            % (self._get("project_dir", PROJECT_DEFAULT), self.port)
        )

    async def _unload(self) -> None:
        await self.stop_server()
        decky.logger.info("[SteamMediaExport] 已卸载，服务已停止")
