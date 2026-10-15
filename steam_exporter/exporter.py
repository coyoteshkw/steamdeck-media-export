"""导出引擎：把 :class:`~.model.Source` 写成交付用的 MP4。"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .model import KIND_LABELS_CN, Source
from .util import (
    ExportCancelled,
    FFmpegError,
    ensure_parent,
    human_duration,
    human_size,
    nofile_limit,
    run_ffmpeg,
    sanitize_value,
    unique_path,
)

MANIFEST_NAME = ".steam_exporter_manifest.json"
MANIFEST_VERSION = 1

MODE_LOSSLESS = "lossless"
MODE_COMPRESS = "compress"

# Linux 对单个命令行参数有 MAX_ARG_STRLEN = 128 KiB 的限制。
# 长录像的分片路径拼成的 concat: 参数会超过它，因此超过阈值时改用短符号链接。
MAX_CONCAT_ARG_LEN = 100_000

# 支持软件/硬件编码器
SOFTWARE_CODECS = {"libx264", "libx265", "libsvtav1"}
VAAPI_CODECS = {"h264_vaapi", "hevc_vaapi", "av1_vaapi"}

DEFAULT_TEMPLATE = "{game}/{date} {time} - {kind_cn}.mp4"


@dataclass
class ExportOptions:
    out_dir: Path
    mode: str = MODE_LOSSLESS
    template: str = DEFAULT_TEMPLATE
    crf: int = 23
    preset: str = "medium"
    codec: str = "libx264"
    audio_bitrate: str = "160k"
    scale: str | None = None
    faststart: bool = True
    force: bool = False
    dry_run: bool = False
    jobs: int = 1
    use_manifest: bool = True
    vaapi_device: str | None = None
    tmp_dir: Path | None = None
    trim: bool = True


@dataclass
class ExportStats:
    planned: int = 0
    exported: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    bytes: int = 0
    outputs: list[Path] = field(default_factory=list)


class ProgressReporter:
    """把 ffmpeg 进度回调渲染成命令行百分比行。

    终端下用 ``\r`` 原地刷新；非终端（重定向到文件）时按整数百分比换行输出。
    """

    def __init__(self, prefix: str, total: float | None, *, enabled: bool = True) -> None:
        self.prefix = prefix
        self.total = total
        self.enabled = enabled
        self._tty = sys.stdout.isatty()
        self._last_pct: int | None = None
        self._last_sec: int | None = None
        self._shown = False

    def __call__(self, fraction: float | None, seconds: float, speed: float | None) -> None:
        if not self.enabled:
            return
        if fraction is None:
            # 总时长未知：只能显示已处理时长
            current = int(seconds)
            if current == self._last_sec:
                return
            self._last_sec = current
            self._shown = True
            self._emit(f"{self.prefix} 已处理 {human_duration(seconds)}")
            return
        pct = int(max(0.0, min(1.0, fraction)) * 100)
        if pct == self._last_pct:
            return
        self._last_pct = pct
        self._shown = True
        text = f"{self.prefix} {pct:3d}%"
        if self.total:
            text += f"  {human_duration(seconds)}/{human_duration(self.total)}"
        if speed:
            text += f"  {speed:.1f}x"
        self._emit(text)

    def _emit(self, text: str) -> None:
        if self._tty:
            sys.stdout.write("\r" + text.ljust(78))
            sys.stdout.flush()
        else:
            print(text, flush=True)

    def finish(self) -> None:
        """清除原地刷新的进度行，避免和后续输出重叠。"""
        if self.enabled and self._tty and self._shown:
            sys.stdout.write("\r" + " " * 78 + "\r")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# 输入构建
# ---------------------------------------------------------------------------
# --- 初始化段（init）校验与损坏恢复 -----------------------------------------
_INIT_VALID_CACHE: dict[str, bool] = {}
_INIT_SIZE_CACHE: dict[str, tuple[int, int] | None] = {}
_DONOR_CACHE: dict[tuple[str, int], list] = {}


def _init_present(path: Path) -> bool:
    """init 段存在且非空（0 字节视为缺失）。"""
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _init_is_valid(path: Path) -> bool:
    """init 段非空且包含 ftyp/moov，可被 ffmpeg 解析。"""
    key = str(path)
    if key in _INIT_VALID_CACHE:
        return _INIT_VALID_CACHE[key]
    ok = False
    try:
        data = path.read_bytes()
        ok = b"ftyp" in data[:64] and b"moov" in data
    except OSError:
        ok = False
    _INIT_VALID_CACHE[key] = ok
    return ok


def _find_box(data: bytes, box_type: bytes) -> bytes | None:
    """在 data 的顶层 box 中查找，返回其负载。"""
    i = 0
    n = len(data)
    while i + 8 <= n:
        size = struct.unpack_from(">I", data, i)[0]
        kind = data[i + 4 : i + 8]
        header = 8
        if size == 1:
            if i + 16 > n:
                return None
            size = struct.unpack_from(">Q", data, i + 8)[0]
            header = 16
        elif size == 0:
            size = n - i
        if size < header or i + size > n:
            return None
        if kind == box_type:
            return data[i + header : i + size]
        i += size
    return None


def _init_video_size(path: Path) -> tuple[int, int] | None:
    """从 init 段的 moov/trak/tkhd 读出视频宽高。"""
    key = str(path)
    if key in _INIT_SIZE_CACHE:
        return _INIT_SIZE_CACHE[key]
    result = None
    try:
        data = path.read_bytes()
        moov = _find_box(data, b"moov")
        trak = _find_box(moov, b"trak") if moov else None
        tkhd = _find_box(trak, b"tkhd") if trak else None
        if tkhd and len(tkhd) >= 8:
            width = struct.unpack_from(">I", tkhd, len(tkhd) - 8)[0] / 65536
            height = struct.unpack_from(">I", tkhd, len(tkhd) - 4)[0] / 65536
            result = (int(round(width)), int(round(height)))
    except (OSError, struct.error):
        result = None
    _INIT_SIZE_CACHE[key] = result
    return result


def _gamerecordings_root(parts: list[Path]) -> Path | None:
    for part in parts:
        for ancestor in (part, *part.parents):
            if ancestor.name == "gamerecordings":
                return ancestor
    return None


def _collect_donor_inits(root: Path, stream_index: int) -> list[tuple]:
    """搜集可作为“头部捐赠者”的有效 init 段。"""
    candidates: list[tuple] = []
    seen: set[str] = set()
    patterns = [
        str(root / "clips" / "*" / "video" / "*" / f"init-stream{stream_index}.m4s"),
        str(root / "video" / "*" / f"init-stream{stream_index}.m4s"),
    ]
    for pattern in patterns:
        for match in sorted(glob.glob(pattern)):
            if match in seen:
                continue
            seen.add(match)
            path = Path(match)
            if not _init_is_valid(path):
                continue
            size = _init_video_size(path) if stream_index == 0 else None
            candidates.append((size, path))
    return candidates


def _find_donor_init(source: Source, stream_index: int) -> Path | None:
    """录制损坏（init 段丢失）时，从其它录制借用同分辨率的 init 段。

    只在视频维度完全一致时才借用，避免花屏/解码失败。
    """
    root = _gamerecordings_root(source.parts)
    if root is None:
        return None
    key = (str(root), stream_index)
    if key not in _DONOR_CACHE:
        _DONOR_CACHE[key] = _collect_donor_inits(root, stream_index)
    candidates = _DONOR_CACHE[key]
    if not candidates:
        return None
    if stream_index == 0 and source.width and source.height:
        wanted = (source.width, source.height)
        for size, path in candidates:
            if size == wanted:
                return path
        return None
    return candidates[0][1]


def _has_init(parts: list[Path], stream_index: int) -> bool:
    return any(_init_present(part / f"init-stream{stream_index}.m4s") for part in parts)


def _collect_stream_files(
    parts: list[Path],
    stream_index: int,
    donor_init: Path | None = None,
) -> list[Path]:
    files: list[Path] = []
    for part in parts:
        init = part / f"init-stream{stream_index}.m4s"
        if _init_present(init):
            files.append(init)
        elif donor_init is not None and _init_is_valid(donor_init):
            files.append(donor_init)  # 借用其它录制的头
        for chunk in sorted(part.glob(f"chunk-stream{stream_index}-*.m4s")):
            try:
                if chunk.stat().st_size > 0:  # 跳过 0 字节残片
                    files.append(chunk)
            except OSError:
                continue
    return files


def _concat_spec(files: list[Path]) -> str:
    return "concat:" + "|".join(str(f) for f in files)


def _merge_stream_files(files: list[Path], root: Path, stream_index: int) -> str:
    """分片过多时的兜底方案：把 ``init + chunk`` 逐字节合并成一个临时文件。"""
    root.mkdir(parents=True, exist_ok=True)
    merged = root / f"merged-stream{stream_index}.mp4"
    with open(merged, "wb") as out:
        for item in files:
            with open(item, "rb") as src:
                shutil.copyfileobj(src, out, length=1024 * 1024)
    return str(merged)


def build_stream_input(
    parts: list[Path],
    stream_index: int,
    link_root: Path | None = None,
    donor_init: Path | None = None,
) -> str | None:
    """把多个会话目录的 ``init + chunk`` 串成一个 ffmpeg ``concat:`` 输入。

    ffmpeg 的 ``concat:`` 协议把文件列表拼成**单个命令行参数**。Linux 对单个
    参数有 128 KiB 上限（``MAX_ARG_STRLEN``），长录像（上千个分片）会直接触发
    ``OSError: [Errno 7] Argument list too long``。

    因此当参数过长时，改为在 ``link_root`` 下建立一批**短名符号链接**，
    用短路径重新拼 ``concat:``；再不行（极端数量）则逐字节合并成临时文件。

    ``donor_init``：当本录制的 init 段丢失（0 字节/损坏）时，借用它作为头部。
    """
    files = _collect_stream_files(parts, stream_index, donor_init)
    if not files:
        return None

    spec = _concat_spec(files)
    too_long = len(spec) > MAX_CONCAT_ARG_LEN
    # ffmpeg 的 concat 协议会同时打开所有分片文件；视频+音频两条流加起来
    # 不能超过进程的文件描述符软上限，否则报 Too many open files。
    too_many_fds = (len(files) * 2 + 64) > nofile_limit()

    if not too_long and not too_many_fds:
        return spec
    if link_root is None:
        return spec

    # 分片太多：直接合并成单个临时文件，一次只打开一个输入
    if too_many_fds:
        return _merge_stream_files(files, Path(link_root), stream_index)

    # 仅是参数过长：用短名符号链接（不复制数据）
    link_dir = Path(link_root) / f"stream{stream_index}"
    link_dir.mkdir(parents=True, exist_ok=True)
    short: list[Path] = []
    try:
        for index, item in enumerate(files):
            target = link_dir / f"{index:05d}.m4s"
            if not target.exists():
                os.symlink(str(item.resolve()), str(target))
            short.append(target)
    except OSError:
        # 某些文件系统不支持符号链接，直接合并
        return _merge_stream_files(files, Path(link_root), stream_index)

    short_spec = _concat_spec(short)
    if len(short_spec) <= MAX_CONCAT_ARG_LEN:
        return short_spec
    return _merge_stream_files(files, Path(link_root), stream_index)


def find_vaapi_device() -> str | None:
    preferred = Path("/dev/dri/renderD128")
    if preferred.exists():
        return str(preferred)
    candidates = sorted(Path("/dev/dri").glob("renderD*")) if Path("/dev/dri").is_dir() else []
    return str(candidates[0]) if candidates else None


# ---------------------------------------------------------------------------
# 命令构建
# ---------------------------------------------------------------------------
def build_ffmpeg_args(
    source: Source,
    options: ExportOptions,
    output: Path | None = None,
    link_root: Path | None = None,
    warnings: list[str] | None = None,
    streaming: bool = False,
) -> list[str]:
    donor_video = None
    if not _has_init(source.parts, 0):
        donor_video = _find_donor_init(source, 0)
        if donor_video is not None:
            if warnings is not None:
                warnings.append(
                    "视频初始化段缺失（可能是 0 字节/损坏），已借用其它录制的"
                    "同分辨率头部做 best-effort 恢复"
                )
        else:
            raise FFmpegError(
                f"视频初始化段缺失（可能是 0 字节/损坏），且未找到可借用的同分辨率头部："
                f"{source.source_id}"
            )

    donor_audio = None
    if not _has_init(source.parts, 1):
        donor_audio = _find_donor_init(source, 1)
        if donor_audio is not None and warnings is not None:
            warnings.append("音频初始化段缺失，已借用其它录制的头部做 best-effort 恢复")

    video_in = build_stream_input(source.parts, 0, link_root, donor_video)
    if video_in is None:
        raise FFmpegError(f"没有找到视频分片：{source.source_id}")
    audio_in = build_stream_input(source.parts, 1, link_root, donor_audio)

    args: list[str] = []
    codec = options.codec
    vaapi = codec in VAAPI_CODECS
    if vaapi and options.vaapi_device:
        args += ["-vaapi_device", options.vaapi_device]

    args += ["-i", video_in]
    if audio_in:
        args += ["-i", audio_in]
    args += ["-map", "0:v:0"]
    if audio_in:
        args += ["-map", "1:a:0"]

    # Steam 剪辑按整段（默认 3 秒）保存，尾部常多出不满一段的余量。
    # 元数据（session.mpd / clip.pb）里的时长才是 Steam 实际播放长度，
    # 据此裁掉尾部，避免“导出比实际多几秒”。
    if options.trim and source.duration_s and source.duration_s > 0:
        args += ["-t", f"{source.duration_s:.3f}"]

    if options.mode == MODE_LOSSLESS:
        args += ["-c", "copy"]
    elif options.mode == MODE_COMPRESS:
        args += _compression_args(options)
    else:
        raise FFmpegError(f"未知导出模式：{options.mode}")

    if streaming:
        # 边转边发（下载到本机）：用分片 MP4，边生成边推给浏览器，不在 Deck 落盘
        args += ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
        args += ["-f", "mp4", "pipe:1"]
    else:
        if options.faststart:
            args += ["-movflags", "+faststart"]
        args += ["-y", str(output)]
    return args


def _compression_args(options: ExportOptions) -> list[str]:
    codec = options.codec
    args = ["-c:v", codec]
    filters: list[str] = []
    if options.scale:
        filters.append(f"scale={options.scale}")
    if codec in VAAPI_CODECS:
        filters.append("format=nv12")
        filters.append("hwupload")
        args += ["-vf", ",".join(filters)]
        args += ["-qp", str(options.crf)]
    else:
        if filters:
            args += ["-vf", ",".join(filters)]
        args += ["-pix_fmt", "yuv420p"]
        if "svtav1" in codec:
            args += ["-crf", str(options.crf), "-preset", str(_svtav1_preset(options.preset))]
        else:
            args += ["-crf", str(options.crf), "-preset", options.preset]
    args += ["-c:a", "aac", "-b:a", options.audio_bitrate]
    return args


def _svtav1_preset(preset: str) -> int:
    mapping = {
        "ultrafast": 13,
        "superfast": 12,
        "veryfast": 10,
        "faster": 9,
        "fast": 8,
        "medium": 6,
        "slow": 4,
        "slower": 2,
        "veryslow": 0,
    }
    return mapping.get(preset, 6)


# ---------------------------------------------------------------------------
# 命名
# ---------------------------------------------------------------------------
_TEMPLATE_VAR_RE = re.compile(r"\{(\w+)\}")


def render_relative_path(source: Source, template: str) -> str:
    local = source.recorded_at.astimezone()
    values = {
        "game": source.game,
        "appid": source.app_id,
        "gameid": source.game_id,
        "date": local.strftime("%Y-%m-%d"),
        "time": local.strftime("%H-%M-%S"),
        "datetime": local.strftime("%Y-%m-%d %H-%M-%S"),
        "kind": source.kind,
        "kind_cn": KIND_LABELS_CN.get(source.kind, source.kind),
        "id": source.source_id,
        "title": source.title or "",
        "user": source.user,
        "duration": _duration_token(source.duration_s),
    }

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"未知模板变量 {{{key}}}")
        return sanitize_value(str(values[key]))

    rendered = _TEMPLATE_VAR_RE.sub(substitute, template)
    if not rendered.lower().endswith(".mp4"):
        rendered += ".mp4"
    return rendered


def _duration_token(seconds: float | None) -> str:
    if seconds is None:
        return "未知时长"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    return f"{minutes}m{secs:02d}s"


def resolve_output_path(
    source: Source,
    options: ExportOptions,
    taken: set[str],
) -> tuple[Path, str]:
    rel = render_relative_path(source, options.template)
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError(f"模板生成了非法路径：{rel}")
    full = options.out_dir / rel_path
    # 防止逃逸出 out_dir
    normalized = Path(os.path.normpath(str(full)))
    out_root = Path(os.path.normpath(str(options.out_dir)))
    if out_root != normalized and out_root not in normalized.parents:
        raise ValueError(f"模板生成了越界路径：{rel}")
    chosen = unique_path(normalized, taken=[Path(p) for p in taken])
    return chosen, rel


# ---------------------------------------------------------------------------
# 清单
# ---------------------------------------------------------------------------
class Manifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            self.entries = data["entries"]

    @staticmethod
    def key(source: Source) -> str:
        return f"{source.user}:{source.kind}:{source.source_id}"

    def is_done(self, source: Source) -> bool:
        entry = self.entries.get(self.key(source))
        if not entry:
            return False
        out = entry.get("path")
        return bool(out) and (self.path.parent / out).exists()

    def record(self, source: Source, rel: str, size: int, mode: str) -> None:
        self.entries[self.key(source)] = {
            "path": rel,
            "size": size,
            "mode": mode,
        }

    def save(self) -> None:
        payload = {"version": MANIFEST_VERSION, "entries": self.entries}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
class Exporter:
    def __init__(
        self,
        options: ExportOptions,
        *,
        on_event: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.options = options
        self.on_event = on_event
        self.cancel_event = cancel_event
        # 作为库调用时也保证 fd 软上限已提升（命令行入口同样会调用）
        from .util import raise_nofile_limit

        raise_nofile_limit()
        self.manifest = (
            Manifest(options.out_dir / MANIFEST_NAME)
            if options.use_manifest
            else None
        )
        self._taken: set[str] = set()
        self._lock = threading.Lock()

    # -- 事件与日志 --
    def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:  # 回调异常不应影响导出
            pass

    def _log(self, message: str) -> None:
        if self.on_event is None:
            print(message)

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def export(self, sources: list[Source]) -> ExportStats:
        options = self.options
        stats = ExportStats(planned=len(sources))
        options.out_dir.mkdir(parents=True, exist_ok=True)

        pending: list[tuple[Source, Path, str]] = []
        for source in sources:
            if (
                not options.force
                and self.manifest
                and self.manifest.is_done(source)
            ):
                stats.skipped += 1
                self._emit({"type": "item_skipped", "source": source.to_dict()})
                continue
            try:
                output, rel = resolve_output_path(source, options, self._taken)
            except (KeyError, ValueError) as exc:
                self._log(f"  ! 跳过 {source.source_id}：{exc}")
                stats.failed += 1
                self._emit(
                    {"type": "item_failed", "source": source.to_dict(), "error": str(exc)}
                )
                continue
            self._taken.add(str(output))
            pending.append((source, output, rel))

        if options.dry_run:
            total = sum(s.size_bytes for s, _p, _r in pending)
            for source, output, _rel in pending:
                self._log(f"  [计划] {source.describe()}  ->  {output}")
                self._emit(
                    {"type": "planned", "source": source.to_dict(), "output": str(output)}
                )
            self._emit({"type": "plan_done", "count": len(pending), "total_size": total})
            self._log(
                f"共 {len(pending)} 条，预计输出约 {human_size(total)}"
                f"（无损；压缩后会更小）"
            )
            return stats

        self._emit({"type": "start", "planned": len(pending), "skipped": stats.skipped})
        if options.jobs > 1 and len(pending) > 1:
            self._export_parallel(pending, stats)
        else:
            self._export_sequential(pending, stats)

        if self.manifest and not options.dry_run:
            self.manifest.save()
        return stats

    def _export_one(
        self,
        source: Source,
        output: Path,
        rel: str,
        on_progress=None,
    ) -> int:
        ensure_parent(output)
        base = self.options.tmp_dir or self.options.out_dir
        base.mkdir(parents=True, exist_ok=True)
        link_root = Path(tempfile.mkdtemp(prefix=".steamexport-", dir=str(base)))
        try:
            warnings: list[str] = []
            args = build_ffmpeg_args(
                source, self.options, output, link_root=link_root, warnings=warnings
            )
            for message in warnings:
                self._log(f"  ! {message}")
                self._emit(
                    {
                        "type": "item_warning",
                        "source": source.to_dict(),
                        "message": message,
                    }
                )
            try:
                run_ffmpeg(
                    args,
                    total_duration=source.duration_s,
                    on_progress=on_progress,
                    cancel_event=self.cancel_event,
                )
            except ExportCancelled:
                # 取消时删掉半成品，避免留下损坏的 mp4
                try:
                    output.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        finally:
            shutil.rmtree(link_root, ignore_errors=True)
        try:
            size = output.stat().st_size
        except OSError:
            size = 0
        if self.manifest:
            with self._lock:
                self.manifest.record(source, rel, size, self.options.mode)
        return size

    def _event_progress(self, index: int, total: int):
        def callback(fraction, seconds, speed):
            if fraction is not None:
                fraction = max(0.0, min(1.0, fraction))
            self._emit(
                {
                    "type": "progress",
                    "index": index,
                    "total": total,
                    "fraction": fraction,
                    "seconds": seconds,
                    "speed": speed,
                }
            )

        return callback

    def _export_sequential(self, pending, stats: ExportStats) -> None:
        total = len(pending)
        for index, (source, output, rel) in enumerate(pending, 1):
            if self._cancelled():
                stats.cancelled = True
                break
            self._emit(
                {
                    "type": "item_start",
                    "index": index,
                    "total": total,
                    "source": source.to_dict(),
                    "output": str(output),
                }
            )
            self._log(f"[{index}/{total}] {source.describe()}")
            if self.on_event is None:
                reporter = ProgressReporter(f"[{index}/{total}] 进度", source.duration_s)
                on_progress = reporter
            else:
                reporter = None
                on_progress = self._event_progress(index, total)
            try:
                size = self._export_one(source, output, rel, on_progress)
            except ExportCancelled:
                if reporter:
                    reporter.finish()
                stats.cancelled = True
                self._emit({"type": "cancelled", "index": index})
                break
            except (FFmpegError, OSError) as exc:
                if reporter:
                    reporter.finish()
                stats.failed += 1
                self._log(f"  ✗ 失败：{exc}")
                self._emit(
                    {
                        "type": "item_failed",
                        "index": index,
                        "source": source.to_dict(),
                        "error": str(exc),
                    }
                )
                continue
            if reporter:
                reporter.finish()
            stats.exported += 1
            stats.bytes += size
            stats.outputs.append(output)
            self._log(f"  ✓ {output}  ({human_size(size)})")
            self._emit(
                {
                    "type": "item_done",
                    "index": index,
                    "total": total,
                    "source": source.to_dict(),
                    "output": str(output),
                    "size": size,
                }
            )
        self._emit_finished(stats)

    def _export_parallel(self, pending, stats: ExportStats) -> None:
        total = len(pending)
        counter = {"done": 0}

        def task(index, item):
            source, output, rel = item
            if self._cancelled():
                raise ExportCancelled("已被用户取消")
            self._emit(
                {
                    "type": "item_start",
                    "index": index,
                    "total": total,
                    "source": source.to_dict(),
                    "output": str(output),
                }
            )
            size = self._export_one(source, output, rel)
            with self._lock:
                counter["done"] += 1
                done = counter["done"]
            return source, output, size, done

        cancelled = False
        with ThreadPoolExecutor(max_workers=self.options.jobs) as pool:
            futures = {pool.submit(task, i, item): item for i, item in enumerate(pending, 1)}
            for future in as_completed(futures):
                source, output, _rel = futures[future]
                try:
                    source, output, size, done = future.result()
                except ExportCancelled:
                    cancelled = True
                    continue
                except (FFmpegError, OSError) as exc:
                    stats.failed += 1
                    self._log(f"  ✗ 失败 {source.source_id}：{exc}")
                    self._emit(
                        {"type": "item_failed", "source": source.to_dict(), "error": str(exc)}
                    )
                    continue
                stats.exported += 1
                stats.bytes += size
                stats.outputs.append(output)
                self._log(f"[{done}/{total}] ✓ {output}  ({human_size(size)})")
                self._emit(
                    {
                        "type": "item_done",
                        "index": done,
                        "total": total,
                        "source": source.to_dict(),
                        "output": str(output),
                        "size": size,
                    }
                )
        if cancelled or self._cancelled():
            stats.cancelled = True
        self._emit_finished(stats)

    def _emit_finished(self, stats: ExportStats) -> None:
        self._emit(
            {
                "type": "finished",
                "exported": stats.exported,
                "skipped": stats.skipped,
                "failed": stats.failed,
                "cancelled": stats.cancelled,
                "bytes": stats.bytes,
                "outputs": [str(p) for p in stats.outputs],
            }
        )
