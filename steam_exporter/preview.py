"""录音预览：缩略图（抽帧）与 5 秒静音预览片。

只读需要的分片（Steam 录像 3 秒一片），绝不读整个文件；生成结果按需缓存，
带 TTL 与容量上限，重复查看零成本。
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

# 限制并发，避免同时抽多帧打满 CPU
_SEMAPHORE = threading.Semaphore(2)
FFMPEG = "ffmpeg"


def _run(cmd: list[str], timeout: float = 60.0) -> bool:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def pick_segment(source, seconds: int = 6):
    """挑一段用于预览：取分片最多的部分（避开极小的续段），并从中部取连续分片。

    返回 ``(init_path, [chunk, ...])``；无法生成（缺头或分片）时返回 None。
    """
    parts = []
    for part in source.parts:
        chunks = sorted(part.glob("chunk-stream0-*.m4s"))
        if chunks:
            parts.append((part, chunks))
    if not parts:
        return None
    part, chunks = max(parts, key=lambda item: len(item[1]))
    init = part / "init-stream0.m4s"
    if not init.is_file() or init.stat().st_size == 0:
        return None  # 头部损坏，放弃生成（前端会回退为无缩略图）

    need = max(1, int(round(seconds / 3.0)) + 1)
    need = min(need, len(chunks))
    start = max(0, (len(chunks) - need) // 2)
    selected = chunks[start : start + need]
    return init, selected


def _concat(init: Path, chunks: list[Path]) -> str:
    return "concat:" + "|".join(str(p) for p in [init, *chunks])


def generate_thumbnail(source, dest: Path) -> bool:
    """抽一帧缩略图（640 宽 JPEG）。"""
    segment = pick_segment(source, seconds=3)
    if segment is None:
        return False
    init, chunks = segment
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", _concat(init, chunks[:1]),
        "-frames:v", "1",
        "-vf", "scale=640:-2",
        "-q:v", "4",
        str(dest),
    ]
    with _SEMAPHORE:
        return _run(cmd)


def generate_preview(source, dest: Path, seconds: int = 5, width: int = 480) -> bool:
    """生成 5 秒、静音、低分辨率预览片。"""
    segment = pick_segment(source, seconds=seconds)
    if segment is None:
        return False
    init, chunks = segment
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", _concat(init, chunks),
        "-t", str(seconds),
        "-vf", f"scale={width}:-2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-an",
        "-movflags", "+faststart",
        str(dest),
    ]
    with _SEMAPHORE:
        return _run(cmd)


class PreviewCache:
    def __init__(
        self,
        root: Path,
        ttl: float = 7 * 86400.0,
        max_bytes: int = 300 * 1024**2,
    ) -> None:
        self.root = Path(root)
        self.ttl = ttl
        self.max_bytes = max_bytes
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @staticmethod
    def key(source, variant: str) -> str:
        raw = f"{source.user}|{source.source_id}|{variant}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def path_for(self, key: str, ext: str) -> Path:
        return self.root / f"{key}.{ext}"

    def has(self, key: str, ext: str) -> bool:
        path = self.path_for(key, ext)
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def touch(self, key: str, ext: str) -> None:
        try:
            os.utime(self.path_for(key, ext), None)
        except OSError:
            pass

    def stats(self) -> dict:
        total = 0
        count = 0
        for pattern in ("*.jpg", "*.mp4"):
            for path in self.root.glob(pattern):
                try:
                    total += path.stat().st_size
                    count += 1
                except OSError:
                    continue
        return {
            "root": str(self.root),
            "count": count,
            "bytes": total,
            "ttl": self.ttl,
            "max_bytes": self.max_bytes,
        }

    def clear(self) -> int:
        removed = 0
        for pattern in ("*.jpg", "*.mp4"):
            for path in self.root.glob(pattern):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def cleanup(self) -> int:
        removed = 0
        now = time.time()
        entries = []
        for pattern in ("*.jpg", "*.mp4"):
            for path in self.root.glob(pattern):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if self.ttl > 0 and now - stat.st_mtime > self.ttl:
                    try:
                        path.unlink()
                        removed += 1
                    except OSError:
                        pass
                    continue
                entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _m, size, _p in entries)
        entries.sort(key=lambda item: item[0])
        index = 0
        while total > self.max_bytes and index < len(entries):
            _mtime, size, path = entries[index]
            index += 1
            try:
                path.unlink()
                total -= size
                removed += 1
            except OSError:
                pass
        return removed
