"""通用辅助函数：体积/时长格式化、ffmpeg 调用、mpd 解析、路径安全化。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------
_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def human_size(num: int | float | None) -> str:
    """把字节数格式化为人类可读字符串。"""
    if num is None:
        return "-"
    value = float(num)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_UNITS[-1]}"


def human_duration(seconds: float | None) -> str:
    """把秒数格式化为 ``1:02:03`` / ``2:03``。"""
    if seconds is None:
        return "-"
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


_INVALID_PATH_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_component(text: str, *, replacement: str = "_") -> str:
    """把字符串变成安全的单个路径分量（不会包含 ``/``）。"""
    cleaned = _INVALID_PATH_CHARS.sub(replacement, text).strip()
    cleaned = cleaned.strip(". ")
    return cleaned or "unnamed"


def sanitize_value(text: str, *, replacement: str = "_") -> str:
    """把模板变量的值安全化：去掉控制字符，但保留模板里的目录分隔由调用方处理。

    这里替换掉可能破坏路径的字符（``\\ : * ? " < > |`` 以及控制字符），
    并去掉首尾空白；``/`` 保留给模板本身使用，变量值里的 ``/`` 会被替换。
    """
    cleaned = text.replace("/", replacement)
    cleaned = _INVALID_PATH_CHARS.sub(replacement, cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------
class FFmpegError(RuntimeError):
    pass


class ExportCancelled(Exception):
    """用户主动取消了导出。"""


def find_tool(name: str) -> str | None:
    return shutil.which(name)


def has_ffmpeg() -> bool:
    return find_tool("ffmpeg") is not None


def raise_nofile_limit() -> None:
    """把进程的文件描述符软上限提升到硬上限（子进程 ffmpeg 会继承）。

    ffmpeg 的 ``concat:`` 协议处理长录像时会**同时打开上千个分片文件**，
    而许多终端环境下软上限只有 1024（如 systemd 的 ``DefaultLimitNOFILESoft``），
    就会报 ``Too many open files``。硬上限通常很高，且普通用户可以把软上限
    提升到硬上限，无需特权。
    """
    try:
        import resource
    except ImportError:  # 非 Unix
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError):
        return
    if hard == resource.RLIM_INFINITY:
        target = max(soft, 1 << 20)
    else:
        target = hard
    if target <= soft:
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        pass


def nofile_limit() -> int:
    """返回当前文件描述符软上限（未知时返回保守值）。"""
    try:
        import resource

        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft == resource.RLIM_INFINITY:
            return 1 << 20
        return int(soft)
    except (ImportError, ValueError, OSError):
        return 1024


def run_ffmpeg(
    args: Sequence[str],
    *,
    quiet: bool = True,
    dry_run: bool = False,
    total_duration: float | None = None,
    on_progress: "Callable[[float | None, float, float | None], None] | None" = None,
    cancel_event=None,
) -> None:
    """执行 ffmpeg 命令；失败抛出 :class:`FFmpegError`。

    传入 ``on_progress`` 时，会开启 ffmpeg 的 ``-progress`` 输出并逐行解析，
    回调签名为 ``(fraction, seconds, speed)``：fraction 在总时长未知时为 None。
    传入 ``cancel_event``（``threading.Event``）时，随时可终止 ffmpeg 并抛出
    :class:`ExportCancelled`。
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostdin"]
    if quiet:
        cmd += ["-loglevel", "error"]
    if on_progress is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += list(args)
    if dry_run:
        print("  [dry-run] " + " ".join(_quote(a) for a in cmd))
        return

    if on_progress is None:
        process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        done, _watcher = _start_cancel_watcher(process, cancel_event)
        try:
            _out, stderr_text = process.communicate()
        finally:
            done.set()
        if cancel_event is not None and cancel_event.is_set():
            raise ExportCancelled("已被用户取消")
        if process.returncode != 0:
            _raise_ffmpeg(stderr_text, process.returncode)
        return

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    done, _watcher = _start_cancel_watcher(process, cancel_event)
    errors: list[str] = []
    speed: float | None = None
    try:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\n")
            if line.startswith("out_time="):
                seconds = parse_out_time(line.split("=", 1)[1])
                if seconds is not None:
                    fraction = (
                        seconds / total_duration
                        if total_duration and total_duration > 0
                        else None
                    )
                    on_progress(fraction, seconds, speed)
            elif line.startswith("speed="):
                speed = _parse_speed(line.split("=", 1)[1])
            elif line.startswith("progress="):
                continue
            elif line and not any(line.startswith(p) for p in _PROGRESS_PREFIXES):
                errors.append(line)
        process.wait()
    finally:
        done.set()
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled("已被用户取消")
    if process.returncode != 0:
        _raise_ffmpeg("\n".join(errors), process.returncode)


def _start_cancel_watcher(process: "subprocess.Popen", cancel_event):
    """后台监视线程：一旦 cancel_event 被设置就立即终止 ffmpeg。

    不依赖 ffmpeg 的进度输出节奏，取消延迟不超过约 0.25 秒。
    """
    done = threading.Event()
    if cancel_event is None:
        return done, None

    def _watch():
        while not done.is_set():
            if cancel_event.wait(0.25):
                _terminate(process)
                return

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    return done, thread


def _terminate(process: "subprocess.Popen") -> None:
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


_PROGRESS_PREFIXES = (
    "frame=",
    "fps=",
    "stream_",
    "bitrate=",
    "total_size=",
    "out_time_us=",
    "out_time_ms=",
    "out_time=",
    "dup_frames=",
    "drop_frames=",
    "speed=",
    "progress=",
)


def parse_out_time(value: str) -> float | None:
    """解析 ffmpeg 的 ``out_time=HH:MM:SS.ffffff``。"""
    value = value.strip()
    if not value or value == "N/A":
        return None
    parts = value.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None


def _parse_speed(value: str) -> float | None:
    try:
        return float(value.strip().rstrip("x"))
    except ValueError:
        return None


def _raise_ffmpeg(stderr: str, returncode: int) -> None:
    tail = (stderr or "").strip().splitlines()
    detail = "\n".join(tail[-8:]) if tail else f"退出码 {returncode}"
    raise FFmpegError(f"ffmpeg 执行失败：\n{detail}")


def _quote(arg: str) -> str:
    if re.search(r"[\s|'\"]", arg):
        return "'" + arg.replace("'", "'\\''") + "'"
    return arg


# ---------------------------------------------------------------------------
# DASH mpd
# ---------------------------------------------------------------------------
_DUR_RE = re.compile(r'mediaPresentationDuration="([^"]+)"')


def parse_iso8601_duration(text: str) -> float | None:
    """解析 ISO8601 duration（如 ``PT12M48.31S``）为秒。"""
    m = re.fullmatch(
        r"P(?:(?P<d>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<m>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<s>\d+(?:\.\d+)?)S)?)?",
        text,
    )
    if not m:
        return None
    d = float(m.group("d") or 0)
    h = float(m.group("h") or 0)
    mi = float(m.group("m") or 0)
    s = float(m.group("s") or 0)
    return d * 86400 + h * 3600 + mi * 60 + s


def mpd_duration_seconds(mpd_path: Path) -> float | None:
    """读取单个 ``session.mpd`` 声明的时长。"""
    try:
        text = mpd_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = _DUR_RE.search(text)
    if not m:
        return None
    return parse_iso8601_duration(m.group(1))


_SEG_DUR_RE = re.compile(r'duration="(\d+)"')
_SEG_TICK_RE = re.compile(r'timescale="(\d+)"')


def session_duration_seconds(part: Path) -> float | None:
    """估算一个视频会话目录的时长。

    - 普通录制：直接使用 ``session.mpd`` 的 ``mediaPresentationDuration``。
    - 滚动缓冲（mpd 为 0 且分片已轮换）：按「现存分片数 × 单段时长」估算。
    """
    mpd = part / "session.mpd"
    declared = mpd_duration_seconds(mpd)
    if declared and declared > 0.5:
        return declared

    try:
        text = mpd.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return declared
    match_dur = _SEG_DUR_RE.search(text)
    match_tick = _SEG_TICK_RE.search(text)
    if not match_dur or not match_tick:
        return declared
    tick = int(match_tick.group(1)) or 1
    segment = int(match_dur.group(1)) / tick
    chunks = sum(1 for _ in part.glob("chunk-stream0-*.m4s"))
    if chunks == 0:
        return declared
    return chunks * segment


# ---------------------------------------------------------------------------
# 文件系统
# ---------------------------------------------------------------------------
def dir_size(path: Path) -> int:
    """递归统计目录大小；出错时忽略。"""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def unique_path(path: Path, taken: Iterable[Path] = ()) -> Path:
    """如果路径已被占用，则追加 `` (2)``、`` (3)`` …"""
    taken_set = {str(p) for p in taken}
    if not path.exists() and str(path) not in taken_set:
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    index = 2
    while True:
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists() and str(candidate) not in taken_set:
            return candidate
        index += 1


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
