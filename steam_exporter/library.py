"""录制选择逻辑：账号选择、扫描、过滤。

命令行与网页服务共用这一层，避免逻辑重复。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .model import (
    KIND_BACKGROUND,
    KIND_CLIP,
    KIND_FOREGROUND,
    Source,
)
from .scanner import scan_user
from .steam import NameResolver, find_userdata_dirs, primary_user

TYPE_CLIP = "clip"
TYPE_BACKGROUND = "background"
TYPE_FOREGROUND = "foreground"
TYPE_RECORDING = "recording"
TYPE_ALL = "all"

TYPE_CHOICES = [TYPE_ALL, TYPE_CLIP, TYPE_BACKGROUND, TYPE_FOREGROUND, TYPE_RECORDING]


@dataclass
class Filters:
    """录制筛选条件。同一字段内的多个值是「或」，不同字段之间是「且」。"""

    type: str = TYPE_ALL
    games: list[str] = field(default_factory=list)
    since: date | None = None
    until: date | None = None
    ids: list[str] = field(default_factory=list)

    def is_active(self) -> bool:
        return bool(
            self.games
            or self.since
            or self.until
            or self.ids
            or self.type in (TYPE_CLIP, TYPE_BACKGROUND, TYPE_FOREGROUND, TYPE_RECORDING)
        )


def build_resolver() -> NameResolver:
    return NameResolver().load()


# ---------------------------------------------------------------------------
# 账号
# ---------------------------------------------------------------------------
def _userdata_size_proxy(path: Path) -> int:
    clips = path / "gamerecordings" / "clips"
    try:
        return sum(1 for _ in clips.iterdir()) if clips.is_dir() else 0
    except OSError:
        return 0


def choose_userdata(user: str | None = "auto") -> list[Path]:
    dirs = find_userdata_dirs()
    if not dirs:
        return []
    wanted = user or "auto"
    if wanted in (None, "auto"):
        target = primary_user()
        if target:
            for d in dirs:
                if d.name == target:
                    return [d]
        return [max(dirs, key=_userdata_size_proxy)]
    if wanted == "all":
        return dirs
    matched = [d for d in dirs if d.name == wanted]
    if not matched:
        matched = [d for d in dirs if wanted in d.name]
    return matched


def collect_sources(user: str | None = "auto", resolver: NameResolver | None = None) -> list[Source]:
    resolver = resolver or build_resolver()
    sources: list[Source] = []
    for userdata in choose_userdata(user):
        sources.extend(
            scan_user(userdata / "gamerecordings", resolver, userdata.name)
        )
    return sources


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------
def filter_sources(sources: list[Source], filters: Filters) -> list[Source]:
    result = sources

    if filters.type == TYPE_CLIP:
        result = [s for s in result if s.kind == KIND_CLIP]
    elif filters.type == TYPE_BACKGROUND:
        result = [s for s in result if s.kind == KIND_BACKGROUND]
    elif filters.type == TYPE_FOREGROUND:
        result = [s for s in result if s.kind == KIND_FOREGROUND]
    elif filters.type == TYPE_RECORDING:
        result = [s for s in result if s.kind in (KIND_BACKGROUND, KIND_FOREGROUND)]

    if filters.games:
        lowered = [g.lower() for g in filters.games]
        result = [s for s in result if any(g in s.game.lower() for g in lowered)]

    if filters.since:
        result = [s for s in result if s.recorded_at.astimezone().date() >= filters.since]
    if filters.until:
        result = [s for s in result if s.recorded_at.astimezone().date() <= filters.until]

    if filters.ids:
        result = [
            s
            for s in result
            if any(token == s.source_id or token in s.source_id for token in filters.ids)
        ]

    return result


def sort_sources(sources: list[Source], *, newest_first: bool = True) -> list[Source]:
    return sorted(sources, key=lambda s: s.recorded_at, reverse=newest_first)
