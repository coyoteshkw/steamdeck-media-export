"""下载到本机的临时缓存。

压缩下载时先把结果编码到这里的临时文件，再以普通 MP4（支持 Range 续传）
提供给浏览器；**不进导出目录、不写导出清单**，并按时长/容量自动清理。
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path


class DownloadCache:
    def __init__(
        self,
        root: Path,
        ttl: float = 1800.0,
        max_bytes: int = 5 * 1024**3,
    ) -> None:
        self.root = Path(root)
        self.ttl = ttl
        self.max_bytes = max_bytes
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # -- 键与路径 --
    @staticmethod
    def key(source, options) -> str:
        """按「录制 + 导出设置」生成缓存键。"""
        parts = [
            source.user,
            source.source_id,
            options.mode,
            options.codec,
            str(options.crf),
            options.preset or "",
            options.scale or "",
            "1" if options.trim else "0",
        ]
        digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
        return digest[:16]

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.mp4"

    def temp_for(self, key: str) -> Path:
        return self.root / f"{key}.tmp.mp4"

    def _name_path(self, key: str) -> Path:
        return self.root / f"{key}.name"

    # -- 读写 --
    def has(self, key: str) -> bool:
        path = self.path_for(key)
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    def touch(self, key: str) -> None:
        try:
            os.utime(self.path_for(key), None)
        except OSError:
            pass

    def store_name(self, key: str, filename: str) -> None:
        try:
            self._name_path(key).write_text(filename, encoding="utf-8")
        except OSError:
            pass

    def get_name(self, key: str) -> str | None:
        try:
            return self._name_path(key).read_text(encoding="utf-8")
        except OSError:
            return None

    def delete(self, key: str) -> None:
        for path in (self.path_for(key), self.temp_for(key), self._name_path(key)):
            try:
                path.unlink()
            except OSError:
                pass

    def clear(self) -> int:
        removed = 0
        for pattern in ("*.mp4", "*.name"):
            for path in self.root.glob(pattern):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def stats(self) -> dict:
        total = 0
        count = 0
        for path in self.root.glob("*.mp4"):
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

    def cleanup(self) -> int:
        """删除过期文件，并按最旧优先把总容量压到上限内。"""
        removed = 0
        now = time.time()
        entries = []
        for path in self.root.glob("*.mp4"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if self.ttl > 0 and now - stat.st_mtime > self.ttl:
                self.delete(path.name[: -len(".mp4")])
                removed += 1
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _m, size, _p in entries)
        entries.sort(key=lambda item: item[0])  # 最旧优先
        index = 0
        while total > self.max_bytes and index < len(entries):
            _mtime, size, path = entries[index]
            index += 1
            self.delete(path.name[: -len(".mp4")])
            total -= size
            removed += 1
        return removed
