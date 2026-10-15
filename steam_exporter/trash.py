"""回收站：删除录制时先把文件移到这里，可恢复、可彻底清空。

放在与 Steam userdata 同一文件系统下，移动是瞬时重命名，不会真正复制数据。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path


def dir_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    else:
        try:
            total = path.stat().st_size
        except OSError:
            total = 0
    return total


class Trash:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.index_path = self.root / "index.json"
        try:
            self.files_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._entries: list[dict] = self._load()

    # -- 持久化 --
    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save(self) -> None:
        try:
            self.index_path.write_text(
                json.dumps(self._entries, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # -- 移入 / 永久删除 --
    def move(self, source, paths: list[Path]) -> dict | None:
        """把若干路径移入回收站，返回一条记录。"""
        existing = [p for p in paths if p.exists()]
        if not existing:
            return None
        entry_id = uuid.uuid4().hex[:12]
        dest = self.files_dir / entry_id
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        moved: list[dict] = []
        size = 0
        for path in existing:
            target = dest / path.name
            index = 1
            while target.exists():
                target = dest / f"{path.name}.{index}"
                index += 1
            try:
                size += dir_size(path)
                shutil.move(str(path), str(target))
                moved.append({"original": str(path), "trashed": str(target)})
            except OSError:
                continue
        if not moved:
            shutil.rmtree(dest, ignore_errors=True)
            return None
        entry = {
            "id": entry_id,
            "kind": source.kind,
            "game": source.game,
            "source_id": source.source_id,
            "recorded_at": source.recorded_at.isoformat(),
            "deleted_at": time.time(),
            "size": size,
            "items": moved,
        }
        self._entries.append(entry)
        self._save()
        return entry

    @staticmethod
    def delete_permanently(paths: list[Path]) -> int:
        size = 0
        for path in paths:
            if not path.exists():
                continue
            size += dir_size(path)
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
            except OSError:
                pass
        return size

    # -- 查询 --
    def entries(self) -> list[dict]:
        return list(self._entries)

    def stats(self) -> dict:
        return {
            "root": str(self.root),
            "count": len(self._entries),
            "bytes": sum(int(e.get("size", 0)) for e in self._entries),
        }

    def _entry(self, entry_id: str) -> dict | None:
        return next((e for e in self._entries if e["id"] == entry_id), None)

    # -- 恢复 / 清空 --
    def restore(self, entry_id: str) -> int:
        entry = self._entry(entry_id)
        if entry is None:
            return 0
        restored = 0
        for item in entry.get("items", []):
            original = Path(item["original"])
            trashed = Path(item["trashed"])
            if not trashed.exists():
                continue
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                if original.exists():
                    continue
                shutil.move(str(trashed), str(original))
                restored += 1
            except OSError:
                continue
        shutil.rmtree(self.files_dir / entry_id, ignore_errors=True)
        self._entries = [e for e in self._entries if e["id"] != entry_id]
        self._save()
        return restored

    def restore_all(self) -> int:
        return sum(self.restore(e["id"]) for e in list(self._entries))

    def purge(self, entry_id: str) -> int:
        entry = self._entry(entry_id)
        if entry is None:
            return 0
        size = int(entry.get("size", 0))
        shutil.rmtree(self.files_dir / entry_id, ignore_errors=True)
        self._entries = [e for e in self._entries if e["id"] != entry_id]
        self._save()
        return size

    def purge_all(self) -> int:
        total = sum(int(e.get("size", 0)) for e in self._entries)
        shutil.rmtree(self.files_dir, ignore_errors=True)
        try:
            self.files_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._entries = []
        self._save()
        return total
