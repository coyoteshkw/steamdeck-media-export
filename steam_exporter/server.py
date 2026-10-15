"""本地 HTTP 服务 + 网页界面后端。

只依赖 Python 标准库；直接复用 :mod:`steam_exporter` 的核心逻辑。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__
from .download_cache import DownloadCache
from .model import KIND_CLIP
from .preview import PreviewCache, generate_preview, generate_thumbnail
from .trash import Trash, dir_size
from .exporter import (
    DEFAULT_TEMPLATE,
    MODE_COMPRESS,
    MODE_LOSSLESS,
    SOFTWARE_CODECS,
    VAAPI_CODECS,
    ExportOptions,
    Exporter,
    build_ffmpeg_args,
    build_limited_stream_input,
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
from .screenshots import (
    ShotFilters,
    choose_screenshot_userdata,
    filter_screenshots,
    find_screenshot_userdata_dirs,
    group_screenshots,
    scan_screenshots,
    sort_screenshots,
)
from .util import (
    ExportCancelled,
    FFmpegError,
    find_tool,
    run_ffmpeg,
    sanitize_value,
)

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
    def __init__(
        self,
        sources,
        options: ExportOptions,
        *,
        kind: str = "export",
        source=None,
        cache_key: str | None = None,
        filename: str | None = None,
        cache: DownloadCache | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.sources = sources
        self.options = options
        self.source = source
        self.cache_key = cache_key
        self.filename = filename
        self.cache = cache
        self.result: dict | None = None
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
            "kind": self.kind,
            "status": self.status,
            "created": self.created,
            "planned": len(self.sources),
            "total_size": sum(s.size_bytes for s in self.sources),
            "stats": self.stats,
            "result": self.result,
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
    def __init__(
        self,
        default_user: str,
        default_out: Path,
        token: str | None,
        download_cache: DownloadCache | None = None,
        trash: Trash | None = None,
        preview_cache: PreviewCache | None = None,
    ) -> None:
        self.resolver = build_resolver()
        self.default_user = default_user
        self.default_out = default_out
        self.token = token
        self.download_cache = download_cache
        self.trash = trash
        self.preview_cache = preview_cache
        self.jobs = Jobs()
        self._cache: dict[str, tuple[float, list]] = {}
        self._shot_cache: dict[str, tuple[float, list]] = {}
        self._cache_lock = threading.Lock()

    def invalidate(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._shot_cache.clear()

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

    def screenshots(self, user: str | None, ttl: float = 60.0) -> list:
        key = user or "auto"
        now = time.time()
        with self._cache_lock:
            entry = self._shot_cache.get(key)
            if entry and now - entry[0] < ttl:
                return entry[1]
        data = scan_screenshots(key, self.resolver)
        with self._cache_lock:
            self._shot_cache[key] = (now, data)
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
        self._responded = True
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, status: int = 200, cache: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self._responded = True
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
            if not getattr(self, "_responded", False):
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
            elif path == "/api/delete":
                self._handle_delete(payload)
            elif path == "/api/screenshots/delete":
                self._handle_shot_delete(payload)
            elif path == "/api/trash/restore":
                self._handle_trash_restore(payload)
            elif path == "/api/trash/purge":
                self._handle_trash_purge(payload)
            elif path == "/api/download/prepare":
                self._handle_download_prepare(payload)
            elif path == "/api/cache/clear":
                self._handle_cache_clear(payload)
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
        if path == "/api/screenshots/summary":
            return self._handle_shot_summary(query)
        if path == "/api/screenshots/games":
            return self._handle_shot_games(query)
        if path == "/api/screenshots/list":
            return self._handle_shot_list(query)
        if path == "/api/screenshots/zip":
            return self._handle_shot_zip(query)
        if path.startswith("/api/screenshot/thumb/"):
            return self._handle_shot_thumb(
                unquote(path[len("/api/screenshot/thumb/") :]), query
            )
        if path.startswith("/api/screenshot/full/"):
            return self._handle_shot_full(
                unquote(path[len("/api/screenshot/full/") :]), query
            )
        if path == "/api/download":
            return self._handle_download(query)
        if path == "/api/download/file":
            return self._handle_download_file(query)
        if path.startswith("/api/thumb/"):
            return self._handle_thumb(path[len("/api/thumb/") :], query)
        if path.startswith("/api/preview/"):
            return self._handle_preview(path[len("/api/preview/") :], query)
        if path.startswith("/api/play/"):
            return self._handle_play(path[len("/api/play/") :], query)
        if path.startswith("/api/icon/"):
            return self._handle_icon(path[len("/api/icon/"):])
        if path == "/api/jobs":
            return self._send_json({"jobs": []})
        if path == "/api/trash":
            return self._handle_trash_list()
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
                "download_profiles": _download_profiles(),
                "cache": self.state.download_cache.stats()
                if self.state.download_cache
                else None,
                "trash": self.state.trash.stats() if self.state.trash else None,
                "preview": self.state.preview_cache.stats()
                if self.state.preview_cache
                else None,
                "steam_running": _steam_running(),
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

    def _handle_download(self, query: dict) -> None:
        user = query.get("user", ["auto"])[0]
        source_id = query.get("id", [""])[0]
        sources = self.state.sources(user)
        source = next((s for s in sources if s.source_id == source_id), None)
        if source is None:
            return self._error(404, "找不到该录制")
        payload = {k: v[0] for k, v in query.items() if v}
        options = self._build_options(payload)
        self._stream_download(source, options)

    def _stream_download(self, source, options: ExportOptions) -> None:
        """边转码边把结果推给浏览器，**不在 Deck 上落盘**（无导出文件、不写入清单）。"""
        base = options.tmp_dir or Path(tempfile.gettempdir())
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError:
            base = Path(tempfile.gettempdir())
        link_root = Path(tempfile.mkdtemp(prefix="steamexport-dl-", dir=str(base)))
        try:
            args = build_ffmpeg_args(source, options, None, link_root=link_root, streaming=True)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(link_root, ignore_errors=True)
            return self._error(400, str(exc))

        filename = download_filename(source)
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"] + args
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", _content_disposition(filename))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self._responded = True
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(262144)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            process.kill()
        finally:
            if process.stdout is not None:
                process.stdout.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            if process.stderr is not None:
                process.stderr.close()
            shutil.rmtree(link_root, ignore_errors=True)

    def _download_options(self, payload: dict) -> ExportOptions:
        profile = payload.get("profile", "fast")
        options = self._build_options(payload)
        options.trim = True
        options.use_manifest = False
        options.force = False
        if profile == "lossless":
            options.mode = MODE_LOSSLESS
            options.vaapi_device = None
        else:
            options.mode = MODE_COMPRESS
            if profile == "balanced":
                options.codec, options.preset, options.crf, options.scale = (
                    "libx264",
                    "medium",
                    23,
                    None,
                )
            elif profile == "small":
                options.codec, options.preset, options.crf, options.scale = (
                    "libx264",
                    "veryfast",
                    26,
                    "1280:-2",
                )
            elif profile == "hardware":
                device = find_vaapi_device()
                if device:
                    options.codec, options.crf, options.vaapi_device = (
                        "h264_vaapi",
                        23,
                        device,
                    )
                else:
                    options.codec, options.preset, options.crf = "libx264", "veryfast", 23
            else:  # fast
                options.codec, options.preset, options.crf, options.scale = (
                    "libx264",
                    "veryfast",
                    23,
                    None,
                )
        return options

    def _handle_download_prepare(self, payload: dict) -> None:
        """准备压缩下载：命中缓存直接返回，否则起一个编码到缓存的 job。"""
        cache = self.state.download_cache
        if cache is None:
            return self._error(500, "下载缓存未启用")
        user = payload.get("user") or "auto"
        source_id = payload.get("id")
        source = next(
            (s for s in self.state.sources(user) if s.source_id == source_id), None
        )
        if source is None:
            return self._error(404, "找不到该录制")
        options = self._download_options(payload)
        if options.mode == MODE_LOSSLESS:
            # 无损不走缓存，直接流式
            return self._send_json({"stream": True})
        cache.cleanup()
        key = cache.key(source, options)
        filename = download_filename(source)
        if cache.has(key):
            cache.touch(key)
            return self._send_json({"ready": True, "key": key, "filename": filename})
        job = Job(
            [source],
            options,
            kind="download",
            source=source,
            cache_key=key,
            filename=filename,
            cache=cache,
        )
        self.state.jobs.add(job)
        threading.Thread(target=_run_job, args=(job,), daemon=True).start()
        return self._send_json(
            {
                "ready": False,
                "job_id": job.id,
                "key": key,
                "filename": filename,
                "planned": 1,
                "total_size": source.size_bytes,
            }
        )

    def _handle_download_file(self, query: dict) -> None:
        cache = self.state.download_cache
        if cache is None:
            return self._error(500, "下载缓存未启用")
        key = query.get("key", [""])[0]
        if not key or not cache.has(key):
            return self._error(404, "缓存已过期，请重新点击下载")
        cache.touch(key)
        path = cache.path_for(key)
        filename = cache.get_name(key) or path.name
        self._serve_file(path, filename)

    def _serve_file(
        self,
        path: Path,
        filename: str,
        attachment: bool = True,
        content_type: str = "video/mp4",
        cache: str = "no-store",
    ) -> None:
        """以普通文件方式发送（带 Content-Length 与 Range，可续传）。"""
        try:
            size = path.stat().st_size
        except OSError:
            return self._error(404, "文件不存在")
        byte_range = _parse_range(self.headers.get("Range"), size)
        partial = byte_range is not None
        start, end = byte_range if partial else (0, size - 1)
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", content_type)
        if attachment:
            self.send_header("Content-Disposition", _content_disposition(filename))
        else:
            self.send_header("Content-Disposition", "inline")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self._responded = True
        try:
            with open(path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle_cache_clear(self, payload: dict) -> None:
        removed = 0
        if self.state.download_cache is not None:
            removed += self.state.download_cache.clear()
        if self.state.preview_cache is not None:
            removed += self.state.preview_cache.clear()
        self._send_json({"ok": True, "removed": removed})

    def _handle_delete(self, payload: dict) -> None:
        """删除录制：默认移入回收站（可恢复），permanent=true 则彻底删除。"""
        user = payload.get("user") or "auto"
        ids = payload.get("ids") or ([payload["id"]] if payload.get("id") else [])
        permanent = bool(payload.get("permanent"))
        trash = self.state.trash
        sources = {s.source_id: s for s in self.state.sources(user)}
        deleted = 0
        removed = 0
        results = []
        errors = []
        for source_id in ids:
            source = sources.get(str(source_id))
            if source is None:
                errors.append(f"找不到 {source_id}")
                continue
            paths = _delete_paths(source)
            if not paths:
                errors.append(f"{source_id} 没有可删除的文件")
                continue
            if permanent:
                size = Trash.delete_permanently(paths)
                results.append({"id": source_id, "size": size})
            else:
                if trash is None:
                    errors.append("回收站未启用")
                    continue
                entry = trash.move(source, paths)
                if entry is None:
                    errors.append(f"{source_id} 移入回收站失败")
                    continue
                size = entry["size"]
                results.append({"id": source_id, "size": size, "trash_id": entry["id"]})
            deleted += 1
            removed += size
        self.state.invalidate()
        self._send_json(
            {
                "ok": True,
                "deleted": deleted,
                "bytes": removed,
                "permanent": permanent,
                "results": results,
                "errors": errors,
                "steam_running": _steam_running(),
            }
        )

    def _handle_trash_list(self) -> None:
        trash = self.state.trash
        if trash is None:
            return self._send_json({"stats": None, "entries": []})
        self._send_json({"stats": trash.stats(), "entries": trash.entries()})

    def _handle_trash_restore(self, payload: dict) -> None:
        trash = self.state.trash
        if trash is None:
            return self._error(500, "回收站未启用")
        if payload.get("all"):
            restored = trash.restore_all()
        else:
            restored = trash.restore(str(payload.get("id", "")))
        self.state.invalidate()
        self._send_json({"ok": True, "restored": restored, "stats": trash.stats()})

    def _handle_trash_purge(self, payload: dict) -> None:
        trash = self.state.trash
        if trash is None:
            return self._error(500, "回收站未启用")
        if payload.get("all"):
            freed = trash.purge_all()
        else:
            freed = trash.purge(str(payload.get("id", "")))
        self._send_json({"ok": True, "freed": freed, "stats": trash.stats()})

    def _find_source(self, source_id: str, query: dict):
        user = query.get("user", ["auto"])[0]
        return next(
            (s for s in self.state.sources(user) if s.source_id == source_id), None
        )

    def _handle_thumb(self, source_id: str, query: dict) -> None:
        source = self._find_source(source_id, query)
        if source is None:
            return self._error(404, "找不到录制")
        # 剪辑直接用 Steam 自带的封面
        if source.thumbnail is not None and source.thumbnail.is_file():
            try:
                body = source.thumbnail.read_bytes()
            except OSError:
                body = b""
            if body:
                return self._send_bytes(
                    body, "image/jpeg", cache="public, max-age=86400"
                )
        cache = self.state.preview_cache
        if cache is None:
            return self._error(404, "无缩略图")
        cache.cleanup()
        key = cache.key(source, "thumb")
        path = cache.path_for(key, "jpg")
        if not cache.has(key, "jpg"):
            if not generate_thumbnail(source, path):
                return self._error(404, "无法生成缩略图")
        cache.touch(key, "jpg")
        try:
            body = path.read_bytes()
        except OSError:
            return self._error(500, "读取失败")
        self._send_bytes(body, "image/jpeg", cache="public, max-age=86400")

    def _handle_preview(self, source_id: str, query: dict) -> None:
        source = self._find_source(source_id, query)
        if source is None:
            return self._error(404, "找不到录制")
        cache = self.state.preview_cache
        if cache is None:
            return self._error(500, "预览未启用")
        cache.cleanup()
        key = cache.key(source, "preview")
        path = cache.path_for(key, "mp4")
        if not cache.has(key, "mp4"):
            if not generate_preview(source, path):
                return self._error(404, "无法生成预览")
        cache.touch(key, "mp4")
        filename = download_filename(source).replace(".mp4", " 预览.mp4")
        self._serve_file(path, filename, attachment=False)

    def _handle_play(self, source_id: str, query: dict) -> None:
        """在线播放：流式 remux，最多前 5 分钟；不转码、不落盘。"""
        source = self._find_source(source_id, query)
        if source is None:
            return self._error(404, "找不到录制")
        if not source.playable:
            return self._error(409, "该录制头部损坏，无法在线播放")
        duration = source.duration_s or 300.0
        limit = min(300.0, duration)
        self._stream_playback(source, limit)

    def _stream_playback(self, source, limit_seconds: float) -> None:
        link_root = Path(
            tempfile.mkdtemp(prefix="steamexport-play-", dir=tempfile.gettempdir())
        )
        try:
            video = build_limited_stream_input(source.parts, 0, limit_seconds, link_root)
            if video is None:
                shutil.rmtree(link_root, ignore_errors=True)
                return self._error(409, "没有可播放的视频分片")
            audio = build_limited_stream_input(source.parts, 1, limit_seconds, link_root)
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(link_root, ignore_errors=True)
            return self._error(500, str(exc))

        args = ["-i", video]
        if audio:
            args += ["-i", audio]
        args += ["-map", "0:v:0"]
        if audio:
            args += ["-map", "1:a:0"]
        args += ["-c", "copy"]
        args += ["-t", f"{limit_seconds:.3f}"]
        args += [
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", *args]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", "inline")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self._responded = True
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(262144)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            process.kill()
        finally:
            if process.stdout is not None:
                process.stdout.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            if process.stderr is not None:
                process.stderr.close()
            shutil.rmtree(link_root, ignore_errors=True)

    # -- 截图 --
    def _shot_filters(self, source) -> ShotFilters:
        def one(name, default=None):
            value = source.get(name)
            if isinstance(value, list):
                value = value[0] if value else None
            return value if value is not None else default

        games = source.get("game")
        if isinstance(games, str):
            game_ids = [games] if games else []
        else:
            game_ids = [str(g) for g in (games or []) if g]
        return ShotFilters(
            game_ids=game_ids,
            since=_parse_date(one("since")),
            until=_parse_date(one("until")),
            q=(one("q") or "").strip(),
            caption_only=str(one("caption") or "") in ("1", "true", "True"),
        )

    def _handle_shot_summary(self, query: dict) -> None:
        dirs = find_screenshot_userdata_dirs()
        main = primary_user()
        users = [
            {"id": d.name, "count": _shot_count(d), "primary": d.name == main}
            for d in dirs
        ]
        chosen = choose_screenshot_userdata(query.get("user", ["auto"])[0])
        effective = chosen[0].name if chosen else "auto"
        shots = self.state.screenshots(effective)
        self._send_json(
            {
                "count": len(shots),
                "total_size": sum(s.size_bytes for s in shots),
                "games": len({s.game_id for s in shots}),
                "users": users,
                "default_user": effective,
                "trash": self.state.trash.stats() if self.state.trash else None,
            }
        )

    def _handle_shot_games(self, query: dict) -> None:
        user = query.get("user", ["auto"])[0]
        filters = self._shot_filters(query)
        shots = filter_screenshots(self.state.screenshots(user), filters)
        self._send_json(
            {
                "count": len(group_screenshots(shots)),
                "shots": len(shots),
                "total_size": sum(s.size_bytes for s in shots),
                "games": group_screenshots(shots),
            }
        )

    def _handle_shot_list(self, query: dict) -> None:
        user = query.get("user", ["auto"])[0]
        filters = self._shot_filters(query)
        shots = filter_screenshots(self.state.screenshots(user), filters)
        shots = sort_screenshots(shots, newest_first=True)
        self._send_json(
            {
                "count": len(shots),
                "total_size": sum(s.size_bytes for s in shots),
                "items": [s.to_dict() for s in shots],
            }
        )

    def _handle_shot_zip(self, query: dict) -> None:
        """把筛选出的截图打包成 ZIP 流式下载（不压缩、不落盘）。"""
        user = query.get("user", ["auto"])[0]
        filters = self._shot_filters(query)
        shots = filter_screenshots(self.state.screenshots(user), filters)
        shots = sort_screenshots(shots, newest_first=True)
        if not shots:
            return self._error(404, "没有可打包的截图")
        self._stream_zip(shots)

    def _stream_zip(self, shots) -> None:
        import zipfile

        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", _content_disposition("Steam截屏.zip"))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self._responded = True
        used: set[str] = set()
        try:
            # JPEG 已压过，ZIP_STORED（不压缩）几乎不耗 CPU；wfile 不可 seek，
            # zipfile 会自动用 data descriptor，产出标准 ZIP
            with zipfile.ZipFile(
                self.wfile, "w", zipfile.ZIP_STORED, allowZip64=True
            ) as archive:
                for shot in shots:
                    name = screenshot_zip_name(shot)
                    candidate = name
                    base, dot, ext = name.rpartition(".")
                    index = 1
                    while candidate in used:
                        candidate = (
                            f"{base} ({index}).{ext}" if dot else f"{base} ({index})"
                        )
                        index += 1
                    used.add(candidate)
                    try:
                        archive.write(shot.path, candidate)
                    except OSError:
                        continue
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _find_screenshot(self, shot_id: str, query: dict):
        user = query.get("user", ["auto"])[0]
        return next(
            (s for s in self.state.screenshots(user) if s.id == shot_id), None
        )

    def _handle_shot_thumb(self, shot_id: str, query: dict) -> None:
        shot = self._find_screenshot(shot_id, query)
        if shot is None:
            return self._error(404, "找不到截图")
        path = shot.thumbnail or shot.path
        content_type = CONTENT_TYPES.get(path.suffix.lower(), "image/jpeg")
        try:
            body = path.read_bytes()
        except OSError as exc:
            return self._error(500, str(exc))
        self._send_bytes(body, content_type, cache="public, max-age=86400")

    def _handle_shot_full(self, shot_id: str, query: dict) -> None:
        shot = self._find_screenshot(shot_id, query)
        if shot is None:
            return self._error(404, "找不到截图")
        download = query.get("dl", ["0"])[0] in ("1", "true")
        content_type = CONTENT_TYPES.get(shot.path.suffix.lower(), "image/jpeg")
        self._serve_file(
            shot.path,
            screenshot_filename(shot),
            attachment=download,
            content_type=content_type,
            cache="public, max-age=3600",
        )

    def _handle_shot_delete(self, payload: dict) -> None:
        """删除截图：默认移入回收站（可恢复），permanent=true 则彻底删除。"""
        user = payload.get("user") or "auto"
        ids = payload.get("ids") or ([payload["id"]] if payload.get("id") else [])
        permanent = bool(payload.get("permanent"))
        trash = self.state.trash
        shots = {s.id: s for s in self.state.screenshots(user)}
        deleted = 0
        removed = 0
        results = []
        errors = []
        for shot_id in ids:
            shot = shots.get(str(shot_id))
            if shot is None:
                errors.append(f"找不到 {shot_id}")
                continue
            paths = shot.delete_paths()
            if permanent:
                size = Trash.delete_permanently(paths)
                results.append({"id": shot_id, "size": size})
            else:
                if trash is None:
                    errors.append("回收站未启用")
                    continue
                entry = trash.move(shot, paths)
                if entry is None:
                    errors.append(f"{shot_id} 移入回收站失败")
                    continue
                size = entry["size"]
                results.append({"id": shot_id, "size": size, "trash_id": entry["id"]})
            deleted += 1
            removed += size
        self.state.invalidate()
        self._send_json(
            {
                "ok": True,
                "deleted": deleted,
                "bytes": removed,
                "permanent": permanent,
                "results": results,
                "errors": errors,
                "steam_running": _steam_running(),
            }
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
        try:
            stat = target.stat()
        except OSError:
            return self._error(404, "未找到")
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self._responded = True
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._responded = True
        self.wfile.write(body)


def download_filename(source) -> str:
    """下载到本机时用的文件名。"""
    game = sanitize_value(source.game or "video") or "video"
    stamp = source.recorded_at.astimezone().strftime("%Y-%m-%d %H-%M-%S")
    return f"{game} - {stamp} - {source.kind_cn}.mp4"


def screenshot_filename(shot) -> str:
    """截图下载到本机时用的文件名。"""
    game = sanitize_value(shot.game or "screenshot") or "screenshot"
    stamp = shot.recorded_at.astimezone().strftime("%Y-%m-%d %H-%M-%S")
    ext = shot.path.suffix or ".jpg"
    return f"{game} - {stamp}{ext}"


def screenshot_zip_name(shot) -> str:
    """ZIP 包内的路径：``游戏名/时间.jpg``。"""
    game = sanitize_value(shot.game or "screenshot") or "screenshot"
    stamp = shot.recorded_at.astimezone().strftime("%Y-%m-%d %H-%M-%S")
    ext = shot.path.suffix or ".jpg"
    return f"{game}/{stamp}{ext}"


def _shot_count(userdata: Path) -> int:
    remote = userdata / "760" / "remote"
    total = 0
    if remote.is_dir():
        for game_dir in remote.iterdir():
            shots_dir = game_dir / "screenshots"
            if shots_dir.is_dir():
                total += sum(1 for p in shots_dir.iterdir() if p.is_file())
    return total


def default_cache_dir() -> Path:
    import os

    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "steam-exporter" / "downloads"


def default_trash_dir() -> Path:
    import os

    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "steam-exporter" / "trash"


def default_preview_dir() -> Path:
    import os

    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "steam-exporter" / "previews"


def _steam_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", "steam"], capture_output=True, text=True
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except OSError:
        return False


def _gamerecordings_root(source) -> Path | None:
    candidates = list(source.parts)
    if source.clip_dir is not None:
        candidates.append(source.clip_dir)
    for start in candidates:
        for ancestor in (start, *start.parents):
            if ancestor.name == "gamerecordings":
                return ancestor
    return None


def _recording_timeline(source) -> Path | None:
    """找录像对应的顶层 timeline（按 daterecorded 最接近的匹配）。"""
    root = _gamerecordings_root(source)
    if root is None:
        return None
    timelines = root / "timelines"
    if not timelines.is_dir():
        return None
    try:
        target = source.recorded_at.timestamp()
    except (ValueError, OSError):
        return None
    best = None
    best_delta = 600.0
    for path in timelines.glob(f"timeline_{source.game_id}*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            timestamp = float(data.get("daterecorded", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        delta = abs(timestamp - target)
        if delta < best_delta:
            best_delta = delta
            best = path
    return best


def _delete_paths(source) -> list[Path]:
    """要删除的内容：剪辑是整个目录；录像是各分片目录 + 对应 timeline。"""
    if source.kind == KIND_CLIP and source.clip_dir is not None:
        return [source.clip_dir]
    paths = list(source.parts)
    timeline = _recording_timeline(source)
    if timeline is not None:
        paths.append(timeline)
    return paths


def _download_profiles() -> list[dict]:
    """下载到本机的预设画质。"""
    profiles = [
        {"id": "lossless", "label": "原画无损（最快、体积大）"},
        {"id": "fast", "label": "快速压缩（veryfast）"},
        {"id": "balanced", "label": "均衡压缩（medium）"},
        {"id": "small", "label": "小体积（720p / CRF26）"},
    ]
    if find_vaapi_device():
        profiles.append({"id": "hardware", "label": "硬件压缩（VAAPI、低 CPU）"})
    return profiles


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """解析 HTTP Range 头（只取第一个区间），返回 (start, end)。"""
    if not header or not header.startswith("bytes=") or size <= 0:
        return None
    spec = header[len("bytes=") :].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            suffix = int(end_s)
            if suffix <= 0:
                return None
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size:
        return None
    end = min(end, size - 1)
    if end < start:
        return None
    return start, end


def _content_disposition(filename: str) -> str:
    fallback = filename.encode("ascii", "ignore").decode().strip() or "video.mp4"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _run_job(job: Job) -> None:
    job.status = "running"
    if job.kind == "download":
        _run_download_job(job)
    else:
        _run_export_job(job)


def _run_export_job(job: Job) -> None:
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


def _run_download_job(job: Job) -> None:
    """把单条录制编码到临时缓存（不写导出目录、不记清单）。"""
    source = job.source
    cache = job.cache
    key = job.cache_key
    target = cache.path_for(key)
    temp = cache.temp_for(key)
    job.emit({"type": "start", "planned": 1, "skipped": 0})
    job.emit(
        {
            "type": "item_start",
            "index": 1,
            "total": 1,
            "source": source.to_dict(),
            "output": str(target),
            "prepare": True,
        }
    )

    def progress(fraction, seconds, speed):
        job.emit(
            {
                "type": "progress",
                "index": 1,
                "total": 1,
                "fraction": None if fraction is None else max(0.0, min(1.0, fraction)),
                "seconds": seconds,
                "speed": speed,
            }
        )

    try:
        _safe_unlink(target)
        _safe_unlink(temp)
        args = build_ffmpeg_args(source, job.options, temp)
        run_ffmpeg(
            args,
            total_duration=source.duration_s,
            on_progress=progress,
            cancel_event=job.cancel_event,
        )
        temp.replace(target)
        cache.store_name(key, job.filename or target.name)
        cache.touch(key)
        size = target.stat().st_size
        job.result = {"key": key, "filename": job.filename, "size": size}
        job.stats = {
            "exported": 1,
            "skipped": 0,
            "failed": 0,
            "cancelled": False,
            "bytes": size,
            "outputs": [str(target)],
        }
        job.emit(
            {
                "type": "item_done",
                "index": 1,
                "total": 1,
                "source": source.to_dict(),
                "output": str(target),
                "size": size,
                "prepare": True,
            }
        )
        job.emit(
            {
                "type": "finished",
                "exported": 1,
                "skipped": 0,
                "failed": 0,
                "cancelled": False,
                "bytes": size,
                "outputs": [str(target)],
            }
        )
        job.status = "done"
    except ExportCancelled:
        _safe_unlink(temp)
        job.emit({"type": "cancelled", "index": 1})
        job.status = "cancelled"
    except (FFmpegError, OSError) as exc:
        _safe_unlink(temp)
        job.emit(
            {"type": "item_failed", "index": 1, "source": source.to_dict(), "error": str(exc)}
        )
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
class _QuietHTTPServer(ThreadingHTTPServer):
    """跳过 ``HTTPServer.server_bind`` 里的 ``socket.getfqdn()``。

    设置 ``--host 0.0.0.0`` 时，标准库会对 host 做一次反向 DNS 解析，
    在 DNS 不可达的环境（如 SteamOS 上 systemd-resolved 超时）会**阻塞约 5 秒**
    才完成绑定，看起来就像启动卡死。这里直接用绑定的 host 作为 ``server_name``，
    跳过反查（该字段仅用于日志展示）。
    """

    def server_bind(self) -> None:  # type: ignore[override]
        import socketserver

        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


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
    cache_dir: Path | None = None,
    cache_ttl: float = 600.0,
    cache_max: int = 5 * 1024**3,
    trash_dir: Path | None = None,
    preview_dir: Path | None = None,
    preview_ttl: float = 7 * 86400.0,
    preview_max: int = 300 * 1024**2,
) -> int:
    from .util import raise_nofile_limit

    raise_nofile_limit()
    cache_root = cache_dir or default_cache_dir()
    download_cache = DownloadCache(cache_root, ttl=cache_ttl, max_bytes=cache_max)
    download_cache.cleanup()
    trash_root = trash_dir or default_trash_dir()
    trash = Trash(trash_root)
    preview_root = preview_dir or default_preview_dir()
    preview_cache = PreviewCache(preview_root, ttl=preview_ttl, max_bytes=preview_max)
    preview_cache.cleanup()
    Handler.state = AppState(
        default_user, default_out, token, download_cache, trash, preview_cache
    )

    try:
        httpd = _QuietHTTPServer((host, port), Handler)
    except OSError as exc:
        print(f"无法启动服务：{exc}")
        print(f"（端口 {port} 可能被占用，试试 --port 其他端口）")
        return 1

    query = f"?token={token}" if token else ""
    print("Steam 媒体导出 · 网页管理界面")
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
    print(f"  下载缓存：{cache_root}（TTL {int(cache_ttl/60)} 分钟，上限 {cache_max//1024**3}GB）")
    print(f"  回收站：{trash_root}")
    print(f"  预览缓存：{preview_root}（TTL {int(preview_ttl/86400)} 天，上限 {preview_max//1024**2}MB）")
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
