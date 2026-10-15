"""本地 HTTP 服务 + 网页界面后端。

只依赖 Python 标准库；直接复用 :mod:`steam_exporter` 的核心逻辑。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .exporter import (
    DEFAULT_TEMPLATE,
    MODE_COMPRESS,
    MODE_LOSSLESS,
    SOFTWARE_CODECS,
    VAAPI_CODECS,
    ExportOptions,
    Exporter,
    find_vaapi_device,
)
from .library import (
    TYPE_CHOICES,
    Filters,
    build_resolver,
    choose_userdata,
    collect_sources,
    filter_sources,
    sort_sources,
)
from .steam import find_steam_roots, find_userdata_dirs, primary_user
from .util import find_tool

WEB_DIR = Path(__file__).parent / "web"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
}

# 游戏图缓存
_ART_CACHE: dict[str, Path | None] = {}


def find_game_art(appid: str) -> Path | None:
    """在 Steam 本地缓存里找一张该游戏的代表图（头部/胶囊/Logo/自定义网格）。

    找不到返回 None。结果会缓存。
    """
    if not appid or not appid.isdigit():
        return None
    if appid in _ART_CACHE:
        return _ART_CACHE[appid]

    result: Path | None = None
    for root in find_steam_roots():
        cache = root / "appcache" / "librarycache"
        appdir = cache / appid

        # 优先级：横版头部/胶囊 > 竖版 > Hero > Logo
        priority = (
            "library_header.jpg",
            "header.jpg",
            "library_capsule.jpg",
            "library_600x900.jpg",
            "library_600x900_2x.jpg",
            "library_hero.jpg",
            "logo.png",
        )
        for name in priority:
            candidate = appdir / name
            if candidate.is_file():
                result = candidate
                break

        if result is None and appdir.is_dir():
            subs = [s for s in sorted(appdir.iterdir()) if s.is_dir()]
            for name in priority:
                for sub in subs:
                    candidate = sub / name
                    if candidate.is_file():
                        result = candidate
                        break
                if result is not None:
                    break

        if result is None:
            # 顶层的任意 <hash>.jpg（不递归）
            if appdir.is_dir():
                for candidate in sorted(appdir.glob("*.jpg")):
                    result = candidate
                    break

        if result is None:
            top_icon = cache / f"{appid}_icon.jpg"
            if top_icon.is_file():
                result = top_icon

        # 非 Steam 快捷方式：自定义网格图
        if result is None:
            grid_names = (
                f"{appid}p.png",
                f"{appid}.png",
                f"{appid}_hero.png",
                f"{appid}_logo.png",
                f"{appid}_icon.png",
                f"{appid}_icon.ico",
            )
            userdata = root / "userdata"
            if userdata.is_dir():
                for user in sorted(userdata.iterdir()):
                    grid = user / "config" / "grid"
                    if not grid.is_dir():
                        continue
                    for name in grid_names:
                        candidate = grid / name
                        if candidate.is_file():
                            result = candidate
                            break
                    if result is not None:
                        break

        if result is not None:
            break

    _ART_CACHE[appid] = result
    return result

END_STATUSES = {"done", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
class Job:
    def __init__(self, sources, options: ExportOptions) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.sources = sources
        self.options = options
        self.cancel_event = threading.Event()
        self.status = "queued"
        self.created = time.time()
        self.stats: dict | None = None
        self._events: list[dict] = []
        self._cond = threading.Condition()

    # 由 Exporter 在工作线程中调用
    def emit(self, event: dict) -> None:
        with self._cond:
            self._events.append(event)
            self._cond.notify_all()

    def events_since(self, index: int, timeout: float = 10.0) -> tuple[list[dict], int]:
        with self._cond:
            if index >= len(self._events):
                self._cond.wait(timeout)
            return list(self._events[index:]), len(self._events)

    def event_count(self) -> int:
        with self._cond:
            return len(self._events)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "created": self.created,
            "planned": len(self.sources),
            "total_size": sum(s.size_bytes for s in self.sources),
            "stats": self.stats,
        }


class Jobs:
    def __init__(self) -> None:
        self._items: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            self._items[job.id] = job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._items.get(job_id)


# ---------------------------------------------------------------------------
# 应用状态
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self, default_user: str, default_out: Path, token: str | None) -> None:
        self.resolver = build_resolver()
        self.default_user = default_user
        self.default_out = default_out
        self.token = token
        self.jobs = Jobs()
        self._cache: dict[str, tuple[float, list]] = {}
        self._cache_lock = threading.Lock()

    def sources(self, user: str | None, ttl: float = 30.0) -> list:
        key = user or "auto"
        now = time.time()
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and now - entry[0] < ttl:
                return entry[1]
        data = collect_sources(key, self.resolver)
        with self._cache_lock:
            self._cache[key] = (now, data)
        return data


# ---------------------------------------------------------------------------
# 请求处理
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = f"steam_exporter/{__version__}"
    state: AppState  # 由 serve() 注入

    def log_message(self, fmt, *args):  # 静默默认访问日志
        pass

    # -- 响应工具 --
    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _authorized(self, query: dict) -> bool:
        token = self.state.token
        if not token:
            return True
        return query.get("token", [None])[0] == token

    # -- 路由 --
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path.startswith("/api/") and not self._authorized(query):
            return self._error(403, "无效的 token")
        try:
            if path.startswith("/api/"):
                self._route_api_get(path, query)
            else:
                self._serve_static(path)
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if not self._authorized(query):
            return self._error(403, "无效的 token")
        payload = self._read_json()
        try:
            if path == "/api/export":
                self._handle_export(payload)
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                self._handle_cancel(path, payload)
            elif path == "/api/reveal":
                self._handle_reveal(payload)
            else:
                self._error(404, "未知接口")
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def _route_api_get(self, path: str, query: dict) -> None:
        if path == "/api/summary":
            return self._handle_summary()
        if path == "/api/recordings":
            return self._handle_recordings(query)
        if path.startswith("/api/icon/"):
            return self._handle_icon(path[len("/api/icon/"):])
        if path == "/api/jobs":
            return self._send_json({"jobs": []})
        if path.startswith("/api/jobs/"):
            rest = path[len("/api/jobs/"):]
            if rest.endswith("/events"):
                return self._handle_events(rest[: -len("/events")])
            return self._handle_job(rest)
        self._error(404, "未知接口")

    # -- GET 处理 --
    def _handle_summary(self) -> None:
        roots = [str(r) for r in find_steam_roots()]
        users = []
        main = primary_user()
        for d in find_userdata_dirs():
            clips = d / "gamerecordings" / "clips"
            videos = d / "gamerecordings" / "video"
            users.append(
                {
                    "id": d.name,
                    "clips": sum(1 for _ in clips.iterdir()) if clips.is_dir() else 0,
                    "videos": sum(1 for _ in videos.iterdir()) if videos.is_dir() else 0,
                    "primary": d.name == main,
                }
            )
        chosen = choose_userdata(self.state.default_user)
        effective_user = chosen[0].name if chosen else self.state.default_user
        device = find_vaapi_device()
        self._send_json(
            {
                "version": __version__,
                "ffmpeg": find_tool("ffmpeg"),
                "steam_roots": roots,
                "users": users,
                "default_user": effective_user,
                "default_out": str(self.state.default_out),
                "encoders": {
                    "software": sorted(SOFTWARE_CODECS),
                    "vaapi": sorted(VAAPI_CODECS),
                    "vaapi_device": device,
                },
                "modes": [MODE_LOSSLESS, MODE_COMPRESS],
                "type_choices": TYPE_CHOICES,
                "default_template": DEFAULT_TEMPLATE,
            }
        )

    def _handle_recordings(self, query: dict) -> None:
        user = query.get("user", ["auto"])[0]
        filters = self._filters_from_query(query)
        sources = self.state.sources(user)
        selected = filter_sources(sources, filters)
        selected = sort_sources(selected, newest_first=True)
        total = sum(s.size_bytes for s in selected)
        self._send_json(
            {
                "count": len(selected),
                "total_size": total,
                "items": [s.to_dict() for s in selected],
            }
        )

    def _filters_from_query(self, query: dict) -> Filters:
        def one(name, default=None):
            values = query.get(name)
            return values[0] if values else default

        since = _parse_date(one("since"))
        until = _parse_date(one("until"))
        games = [g for g in query.get("game", []) if g]
        q = one("q")
        ids = [q] if q else []
        return Filters(
            type=one("type", "all") or "all",
            games=games,
            since=since,
            until=until,
            ids=ids,
        )

    def _handle_icon(self, appid: str) -> None:
        art = find_game_art(appid.strip())
        if art is None:
            return self._error(404, "没有找到图标")
        content_type = CONTENT_TYPES.get(art.suffix.lower(), "image/jpeg")
        try:
            body = art.read_bytes()
        except OSError as exc:
            return self._error(500, str(exc))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=604800")
        self.end_headers()
        self.wfile.write(body)

    def _handle_job(self, job_id: str) -> None:
        job = self.state.jobs.get(job_id)
        if not job:
            return self._error(404, "任务不存在")
        self._send_json(job.snapshot())

    def _handle_events(self, job_id: str) -> None:
        job = self.state.jobs.get(job_id)
        if not job:
            return self._error(404, "任务不存在")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        index = 0
        while True:
            events, new_index = job.events_since(index, timeout=10.0)
            if new_index > index:
                for event in events:
                    data = json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                index = new_index
            else:
                self.wfile.write(b": ping\n\n")
            self.wfile.flush()
            if job.status in END_STATUSES and index >= job.event_count():
                break

    # -- POST 处理 --
    def _select_sources(self, payload: dict):
        user = payload.get("user") or "auto"
        sources = self.state.sources(user)
        ids = payload.get("ids") or []
        if ids:
            wanted = set(str(i) for i in ids)
            return [s for s in sources if s.source_id in wanted]
        filters = Filters(
            type=payload.get("type", "all") or "all",
            games=[g for g in (payload.get("games") or []) if g],
            since=_parse_date(payload.get("since")),
            until=_parse_date(payload.get("until")),
            ids=[payload["q"]] if payload.get("q") else [],
        )
        if not filters.is_active() and not payload.get("all"):
            return None  # 需要显式 all
        return filter_sources(sources, filters)

    def _build_options(self, payload: dict) -> ExportOptions:
        mode = payload.get("mode", MODE_LOSSLESS)
        if mode not in (MODE_LOSSLESS, MODE_COMPRESS):
            mode = MODE_LOSSLESS
        codec = payload.get("codec") or "libx264"
        vaapi_device = None
        if mode == MODE_COMPRESS and codec in VAAPI_CODECS:
            vaapi_device = find_vaapi_device()
        out = Path(payload.get("out") or self.state.default_out).expanduser()
        return ExportOptions(
            out_dir=out,
            mode=mode,
            template=payload.get("template") or DEFAULT_TEMPLATE,
            crf=int(payload.get("crf", 23)),
            preset=payload.get("preset") or "medium",
            codec=codec,
            audio_bitrate=payload.get("audio_bitrate") or "160k",
            scale=payload.get("scale") or None,
            faststart=bool(payload.get("faststart", True)),
            force=bool(payload.get("force", False)),
            dry_run=bool(payload.get("dry_run", False)),
            jobs=max(1, int(payload.get("jobs", 1))),
            use_manifest=bool(payload.get("use_manifest", True)),
            vaapi_device=vaapi_device,
            trim=bool(payload.get("trim", True)),
        )

    def _handle_export(self, payload: dict) -> None:
        sources = self._select_sources(payload)
        if sources is None:
            return self._error(400, "请指定要导出的范围（先筛选或勾选，或选择全部）")
        if not sources:
            return self._error(400, "没有匹配的录制")
        options = self._build_options(payload)

        if options.dry_run:
            events: list[dict] = []
            exporter = Exporter(options, on_event=events.append)
            stats = exporter.export(sources)
            plan = [e for e in events if e.get("type") == "planned"]
            return self._send_json(
                {
                    "dry_run": True,
                    "count": len(plan),
                    "total_size": sum(s.size_bytes for s in sources),
                    "items": plan,
                }
            )

        job = Job(sources, options)
        self.state.jobs.add(job)
        threading.Thread(target=_run_job, args=(job,), daemon=True).start()
        self._send_json(
            {
                "job_id": job.id,
                "planned": len(sources),
                "total_size": sum(s.size_bytes for s in sources),
            }
        )

    def _handle_cancel(self, path: str, payload: dict) -> None:
        job_id = path[len("/api/jobs/"): -len("/cancel")]
        job = self.state.jobs.get(job_id)
        if not job:
            return self._error(404, "任务不存在")
        job.cancel_event.set()
        self._send_json({"ok": True})

    def _handle_reveal(self, payload: dict) -> None:
        import subprocess

        target = payload.get("path")
        if not target:
            return self._error(400, "缺少 path")
        path = Path(target).expanduser()
        try:
            if path.is_file():
                path = path.parent
            if not path.is_dir():
                return self._error(400, "目录不存在")
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return self._error(500, str(exc))
        self._send_json({"ok": True})

    # -- 静态文件 --
    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            return self._error(404, "未找到")
        # 防目录穿越
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents and target != WEB_DIR.resolve():
            return self._error(403, "非法路径")
        if not target.is_file():
            return self._error(404, "未找到")
        content_type = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send_bytes(target.read_bytes(), content_type)


def _run_job(job: Job) -> None:
    job.status = "running"
    exporter = Exporter(job.options, on_event=job.emit, cancel_event=job.cancel_event)
    try:
        stats = exporter.export(job.sources)
        job.stats = {
            "exported": stats.exported,
            "skipped": stats.skipped,
            "failed": stats.failed,
            "cancelled": stats.cancelled,
            "bytes": stats.bytes,
            "outputs": [str(p) for p in stats.outputs],
        }
        if stats.cancelled:
            job.status = "cancelled"
        elif stats.failed and not stats.exported:
            job.status = "failed"
        else:
            job.status = "done"
    except Exception as exc:  # noqa: BLE001
        job.emit({"type": "error", "message": str(exc)})
        job.status = "failed"
    finally:
        job.emit({"type": "end", "status": job.status})


def _parse_date(text) -> date | None:
    if not text:
        return None
    if isinstance(text, date):
        return text
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            from datetime import datetime

            return datetime.strptime(str(text), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
def _local_ips() -> list[str]:
    """探测本机局域网 IPv4 地址（用于打印可访问 URL）。"""
    import socket

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
    return sorted(ip for ip in ips if not ip.startswith("127."))


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
    token: str | None = None,
    default_user: str = "auto",
    default_out: Path = Path("~/Videos/SteamExports"),
) -> int:
    from .util import raise_nofile_limit

    raise_nofile_limit()
    Handler.state = AppState(default_user, default_out, token)

    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"无法启动服务：{exc}")
        print(f"（端口 {port} 可能被占用，试试 --port 其他端口）")
        return 1

    query = f"?token={token}" if token else ""
    print("Steam 录制导出 · 网页管理界面")
    if host in ("0.0.0.0", "::", ""):
        ips = _local_ips()
        if ips:
            print("  局域网地址（手机 / 平板 / 另一台电脑都可访问）：")
            for ip in ips:
                print(f"    http://{ip}:{port}/{query}")
        else:
            print(f"  正在监听 {host}:{port}（未探测到局域网 IP）")
    else:
        print(f"  地址：http://{host}:{port}/{query}")
    print(f"  输出：{default_out}")
    if host not in ("127.0.0.1", "localhost"):
        if token:
            print("  已启用访问令牌，访问时需带 ?token=…")
        else:
            print("  ⚠ 监听了非本机地址但未设 --token，局域网内任何人都能操作，强烈建议加 --token")
    print("  按 Ctrl+C 停止")
    print()

    if open_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
    return 0


def _open_browser(url: str) -> None:
    import subprocess

    for command in (["xdg-open", url], ["gio", "open", url]):
        try:
            subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
        except OSError:
            continue
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
