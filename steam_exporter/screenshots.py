"""扫描 Steam 截图（``userdata/<id>/760``），产出可浏览 / 下载的截图列表。

数据来源与录像不同：截图放在 ``760/remote/<gameid>/screenshots/`` 下，
Steam 已经为每张图生成好缩略图（``thumbnails/``）；元数据在
``760/screenshots.vdf``（Valve 文本 VDF）。以**文件系统为准**，vdf 只做增强
（精确时间、尺寸、标题），读到半截或缺失时用文件名时间戳兜底。

只依赖标准库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .steam import (
    NameResolver,
    appid_from_game_id,
    find_steam_roots,
    parse_vdf_text,
    primary_user,
)

SHOT_SUFFIXES = {".jpg", ".jpeg", ".png"}
KIND_SCREENSHOT = "screenshot"

# 文件名：<YYYYMMDD><HHMMSS>_<序号>.jpg（时间为 UTC）
_NAME_RE = re.compile(r"^(?P<date>\d{8})(?P<time>\d{6})")


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Screenshot:
    """一张截图。

    提供 ``kind`` / ``source_id`` / ``game`` / ``recorded_at`` 属性，以便直接
    复用 :class:`~.trash.Trash` 的删除逻辑。
    """

    id: str
    game_id: str
    app_id: str
    game: str
    name: str
    path: Path
    thumbnail: Path | None
    size_bytes: int
    width: int | None
    height: int | None
    recorded_at: datetime
    caption: str | None = None
    user: str = ""

    @property
    def kind(self) -> str:
        return KIND_SCREENSHOT

    @property
    def source_id(self) -> str:
        return self.id

    @property
    def title(self) -> str | None:
        return self.caption

    @property
    def recorded_display(self) -> str:
        return self.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def delete_paths(self) -> list[Path]:
        """删除时一并移入回收站的路径：原图 + 缩略图。"""
        paths = [self.path]
        if self.thumbnail is not None:
            paths.append(self.thumbnail)
        return paths

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": KIND_SCREENSHOT,
            "game": self.game,
            "appid": self.app_id,
            "gameid": self.game_id,
            "name": self.name,
            "size": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "recorded_at": self.recorded_at.astimezone().isoformat(),
            "recorded_display": self.recorded_display,
            "caption": self.caption,
            "has_thumb": self.thumbnail is not None,
            "user": self.user,
        }


# ---------------------------------------------------------------------------
# vdf 元数据
# ---------------------------------------------------------------------------
def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_metadata(vdf_path: Path) -> dict[tuple[str, str], dict]:
    """解析 ``screenshots.vdf``，返回 ``{(gameid, 文件名): 元数据}``。"""
    out: dict[tuple[str, str], dict] = {}
    if not vdf_path.is_file():
        return out
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    try:
        parsed = parse_vdf_text(text)
    except Exception:  # noqa: BLE001 - vdf 可能正被 Steam 写入，解析失败就当作没有
        return out
    shots = parsed.get("screenshots")
    if not isinstance(shots, dict):
        return out
    for game_id, records in shots.items():
        if not isinstance(records, dict):
            continue
        for record in records.values():
            if not isinstance(record, dict):
                continue
            filename = record.get("filename")
            if not filename:
                continue
            name = Path(str(filename)).name
            if not name:
                continue
            out[(str(game_id), name)] = {
                "creation": _to_int(record.get("creation")),
                "width": _to_int(record.get("width")),
                "height": _to_int(record.get("height")),
                "caption": record.get("caption") or None,
            }
    return out


# ---------------------------------------------------------------------------
# 账号发现
# ---------------------------------------------------------------------------
def find_screenshot_userdata_dirs() -> list[Path]:
    """返回含有 ``760/remote`` 的用户目录。"""
    out: list[Path] = []
    for root in find_steam_roots():
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for child in sorted(userdata.iterdir()):
            if child.is_dir() and (child / "760" / "remote").is_dir():
                out.append(child)
    return out


def _shot_count_proxy(userdata: Path) -> int:
    remote = userdata / "760" / "remote"
    total = 0
    try:
        for game_dir in remote.iterdir():
            shots = game_dir / "screenshots"
            if shots.is_dir():
                total += sum(1 for p in shots.iterdir() if p.is_file())
    except OSError:
        return total
    return total


def choose_screenshot_userdata(user: str | None = "auto") -> list[Path]:
    dirs = find_screenshot_userdata_dirs()
    if not dirs:
        return []
    wanted = user or "auto"
    if wanted in ("auto",):
        target = primary_user()
        if target:
            for d in dirs:
                if d.name == target:
                    return [d]
        return [max(dirs, key=_shot_count_proxy)]
    if wanted == "all":
        return dirs
    matched = [d for d in dirs if d.name == wanted]
    if not matched:
        matched = [d for d in dirs if wanted in d.name]
    return matched


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def _time_from_name(name: str) -> datetime | None:
    match = _NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(
            match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _scan_user(remote: Path, meta: dict, resolver: NameResolver, user: str) -> list[Screenshot]:
    out: list[Screenshot] = []
    if not remote.is_dir():
        return out
    for game_dir in sorted(remote.iterdir()):
        shots_dir = game_dir / "screenshots"
        if not shots_dir.is_dir():
            continue
        game_id = game_dir.name
        app_id = appid_from_game_id(game_id) or game_id
        game = resolver.name_for(game_id) or f"未知应用 {app_id}"
        thumb_dir = shots_dir / "thumbnails"
        for path in sorted(shots_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in SHOT_SUFFIXES:
                continue
            info = meta.get((game_id, path.name), {})
            recorded = None
            if info.get("creation"):
                try:
                    recorded = datetime.fromtimestamp(info["creation"], tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    recorded = None
            if recorded is None:
                recorded = _time_from_name(path.name)
            if recorded is None:
                try:
                    recorded = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                except OSError:
                    recorded = datetime.fromtimestamp(0, tz=timezone.utc)
            thumb = thumb_dir / path.name
            out.append(
                Screenshot(
                    id=f"{game_id}:{path.name}",
                    game_id=game_id,
                    app_id=app_id,
                    game=game,
                    name=path.name,
                    path=path,
                    thumbnail=thumb if thumb.is_file() else None,
                    size_bytes=_safe_size(path),
                    width=info.get("width"),
                    height=info.get("height"),
                    recorded_at=recorded,
                    caption=info.get("caption"),
                    user=user,
                )
            )
    return out


def scan_screenshots(
    user: str | None = "auto",
    resolver: NameResolver | None = None,
    *,
    metadata: dict | None = None,
) -> list[Screenshot]:
    resolver = resolver or NameResolver().load()
    out: list[Screenshot] = []
    for userdata in choose_screenshot_userdata(user):
        root = userdata / "760"
        remote = root / "remote"
        meta = metadata if metadata is not None else load_metadata(root / "screenshots.vdf")
        out.extend(_scan_user(remote, meta, resolver, userdata.name))
    return out


# ---------------------------------------------------------------------------
# 过滤与分组
# ---------------------------------------------------------------------------
@dataclass
class ShotFilters:
    """截图筛选条件。"""

    game_ids: list[str] = None  # type: ignore[assignment]
    since: date | None = None
    until: date | None = None
    q: str = ""
    caption_only: bool = False

    def __post_init__(self) -> None:
        if self.game_ids is None:
            self.game_ids = []

    def is_active(self) -> bool:
        return bool(self.game_ids or self.since or self.until or self.q or self.caption_only)


def filter_screenshots(shots: list[Screenshot], filters: ShotFilters) -> list[Screenshot]:
    result = shots
    if filters.game_ids:
        wanted = set(filters.game_ids)
        result = [s for s in result if s.game_id in wanted]
    if filters.since:
        result = [s for s in result if s.recorded_at.astimezone().date() >= filters.since]
    if filters.until:
        result = [s for s in result if s.recorded_at.astimezone().date() <= filters.until]
    if filters.caption_only:
        result = [s for s in result if s.caption]
    if filters.q:
        needle = filters.q.lower()
        result = [
            s
            for s in result
            if needle in s.game.lower() or (s.caption and needle in s.caption.lower())
        ]
    return result


def sort_screenshots(shots: list[Screenshot], *, newest_first: bool = True) -> list[Screenshot]:
    return sorted(shots, key=lambda s: s.recorded_at, reverse=newest_first)


def group_screenshots(shots: list[Screenshot]) -> list[dict]:
    """按游戏分组，返回游戏卡片数据（含张数、体积、最新时间、样张 id）。"""
    groups: dict[str, dict] = {}
    for shot in shots:
        group = groups.get(shot.game_id)
        if group is None:
            group = {
                "gameid": shot.game_id,
                "appid": shot.app_id,
                "game": shot.game,
                "count": 0,
                "bytes": 0,
                "latest": 0.0,
                "latest_display": "",
                "samples": [],
            }
            groups[shot.game_id] = group
        group["count"] += 1
        group["bytes"] += shot.size_bytes
        stamp = shot.recorded_at.timestamp()
        if stamp > group["latest"]:
            group["latest"] = stamp
            group["latest_display"] = shot.recorded_display
        if len(group["samples"]) < 4:
            group["samples"].append(shot.id)
    result = list(groups.values())
    result.sort(key=lambda g: g["latest"], reverse=True)
    return result
