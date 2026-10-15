"""扫描 Steam ``gamerecordings`` 目录，产出可导出的 :class:`~.model.Source` 列表。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from . import pb
from .model import KIND_BACKGROUND, KIND_CLIP, KIND_FOREGROUND, Source
from .steam import NameResolver, appid_from_game_id
from .util import dir_size, session_duration_seconds

# 会话目录名：<prefix>_<gameid>_<YYYYMMDD>_<HHMMSS>[_<续段号>]
SESSION_RE = re.compile(
    r"^(?P<prefix>fg|bg)_(?P<gameid>\d+)_(?P<date>\d{8})_(?P<time>\d{6})"
    r"(?:_(?P<cont>\d+))?$"
)
CLIP_DIR_RE = re.compile(r"^clip_(?P<gameid>\d+)_(?P<date>\d{8})_(?P<time>\d{6})$")


# ---------------------------------------------------------------------------
# clip.pb
# ---------------------------------------------------------------------------
class ClipMeta:
    def __init__(self, msg: dict[int, list]) -> None:
        self.game_id: str | None = None
        self.recorded_at: datetime | None = None
        self.title: str | None = None
        self.session: str | None = None
        self.duration_s: float | None = None
        self.width: int | None = None
        self.height: int | None = None

        # 嵌套消息：字段 1
        nested = pb.first_msg(msg, 1)
        if nested:
            game_id = pb.first_int(nested, 2)
            if game_id is not None:
                self.game_id = str(game_id)
            ts = pb.first_int(nested, 3)
            if ts is not None:
                self.recorded_at = datetime.fromtimestamp(ts, tz=timezone.utc)
            video = pb.first_msg(nested, 5)
            if video:
                self.session = pb.first_str(video, 1)
                duration_ms = pb.first_int(video, 3)
                if duration_ms is not None:
                    self.duration_s = duration_ms / 1000.0

        top_game_id = pb.first_int(msg, 4)
        if self.game_id is None and top_game_id is not None:
            self.game_id = str(top_game_id)

        self.title = pb.first_str(msg, 7)
        self.width = pb.first_int(msg, 12)
        self.height = pb.first_int(msg, 13)


def parse_clip_pb(path: Path) -> ClipMeta | None:
    try:
        msg = pb.parse(path.read_bytes())
    except (OSError, pb.ProtobufError):
        return None
    return ClipMeta(msg)


# ---------------------------------------------------------------------------
# 会话名解析
# ---------------------------------------------------------------------------
class SessionName:
    def __init__(self, name: str) -> None:
        self.raw = name
        self.prefix: str | None = None
        self.game_id: str | None = None
        self.recorded_at: datetime | None = None
        self.cont: int | None = None
        self.base: str = name
        match = SESSION_RE.match(name)
        if match:
            self.prefix = match.group("prefix")
            self.game_id = match.group("gameid")
            stamp = datetime.strptime(
                match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone.utc)
            self.recorded_at = stamp
            cont = match.group("cont")
            self.cont = int(cont) if cont is not None else None
            self.base = (
                f"{self.prefix}_{self.game_id}_{match.group('date')}_{match.group('time')}"
            )

    @property
    def kind(self) -> str:
        if self.prefix == "bg":
            return KIND_BACKGROUND
        if self.prefix == "fg":
            return KIND_FOREGROUND
        return KIND_FOREGROUND


def group_session_dirs(session_dirs: list[Path]) -> list[list[Path]]:
    """把会话目录按 base 分组，并排好续段顺序。

    返回 ``[[part1, part2, ...], ...]``，每组按录制时间排序。
    """
    groups: dict[str, dict] = {}
    for path in session_dirs:
        info = SessionName(path.name)
        group = groups.setdefault(
            info.base,
            {"info": info, "parts": [], "times": []},
        )
        group["parts"].append((info, path))

    result: list[list[Path]] = []
    for base in sorted(
        groups, key=lambda b: groups[b]["info"].recorded_at or datetime.min.replace(tzinfo=timezone.utc)
    ):
        entries = groups[base]["parts"]
        # 无续段（cont is None）排最前，然后按续段号
        entries.sort(key=lambda pair: (pair[0].cont is not None, pair[0].cont or 0))
        result.append([path for _info, path in entries])
    return result


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def _make_source(
    *,
    kind: str,
    source_id: str,
    game_id: str,
    recorded_at: datetime,
    parts: list[Path],
    resolver: NameResolver,
    user: str,
    title: str | None = None,
    duration_s: float | None = None,
    width: int | None = None,
    height: int | None = None,
    thumbnail: Path | None = None,
    timeline: Path | None = None,
    clip_dir: Path | None = None,
) -> Source:
    app_id = appid_from_game_id(game_id) or game_id
    game = resolver.name_for(game_id) or f"未知应用 {app_id}"
    size = sum(dir_size(part) for part in parts)
    playable = any(
        (part / "init-stream0.m4s").is_file()
        and (part / "init-stream0.m4s").stat().st_size > 0
        for part in parts
    )
    return Source(
        kind=kind,
        source_id=source_id,
        game_id=game_id,
        app_id=app_id,
        game=game,
        recorded_at=recorded_at,
        parts=parts,
        size_bytes=size,
        duration_s=duration_s,
        width=width,
        height=height,
        title=title,
        thumbnail=thumbnail,
        timeline=timeline,
        clip_dir=clip_dir,
        user=user,
        playable=playable,
    )


def scan_clips(gamerecordings: Path, resolver: NameResolver, user: str = "") -> list[Source]:
    clips_root = gamerecordings / "clips"
    if not clips_root.is_dir():
        return []
    sources: list[Source] = []
    for clip_dir in sorted(clips_root.iterdir()):
        if not clip_dir.is_dir():
            continue
        meta = parse_clip_pb(clip_dir / "clip.pb") if (clip_dir / "clip.pb").is_file() else None

        video_root = clip_dir / "video"
        parts = sorted([d for d in video_root.iterdir() if d.is_dir()]) if video_root.is_dir() else []
        if meta and meta.session:
            preferred = video_root / meta.session
            if preferred.is_dir():
                parts = [preferred] + [p for p in parts if p != preferred]

        # game_id / recorded_at：
        # recorded_at 优先用目录名——它是“剪辑被保存的时刻”（UTC）；
        # clip.pb 里的 daterecorded 其实是源录像会话的开始时间，不是剪辑时刻。
        match = CLIP_DIR_RE.match(clip_dir.name)
        game_id = meta.game_id if meta and meta.game_id else None
        recorded_at = None
        if match:
            recorded_at = datetime.strptime(
                match.group("date") + match.group("time"), "%Y%m%d%H%M%S"
            ).replace(tzinfo=timezone.utc)
        if game_id is None and match:
            game_id = match.group("gameid")
        if recorded_at is None and meta:
            recorded_at = meta.recorded_at
        if game_id is None:
            continue

        duration = meta.duration_s if meta else None
        if duration is None and parts:
            duration = session_duration_seconds(parts[0])

        timeline = None
        tl_root = clip_dir / "timelines"
        if tl_root.is_dir():
            files = sorted(tl_root.glob("*.json"))
            timeline = files[0] if files else None

        sources.append(
            _make_source(
                kind=KIND_CLIP,
                source_id=clip_dir.name,
                game_id=game_id,
                recorded_at=recorded_at,
                parts=parts,
                resolver=resolver,
                user=user,
                title=meta.title if meta else None,
                duration_s=duration,
                width=meta.width if meta else None,
                height=meta.height if meta else None,
                thumbnail=(clip_dir / "thumbnail.jpg")
                if (clip_dir / "thumbnail.jpg").is_file()
                else None,
                timeline=timeline,
                clip_dir=clip_dir,
            )
        )
    return sources


def scan_recordings(
    gamerecordings: Path, resolver: NameResolver, user: str = ""
) -> list[Source]:
    video_root = gamerecordings / "video"
    if not video_root.is_dir():
        return []
    session_dirs = sorted([d for d in video_root.iterdir() if d.is_dir()])
    groups = group_session_dirs(session_dirs)
    sources: list[Source] = []
    for parts in groups:
        info = SessionName(parts[0].name)
        if info.game_id is None or info.recorded_at is None:
            continue
        # 续段可能跨目录，取所有该组内 game_id 一致（以第一个为准）
        duration = 0.0
        got_duration = False
        for part in parts:
            value = session_duration_seconds(part)
            if value is not None:
                duration += value
                got_duration = True
        sources.append(
            _make_source(
                kind=info.kind,
                source_id=info.base,
                game_id=info.game_id,
                recorded_at=info.recorded_at,
                parts=parts,
                resolver=resolver,
                user=user,
                duration_s=duration if got_duration else None,
            )
        )
    return sources


def scan_user(gamerecordings: Path, resolver: NameResolver, user: str = "") -> list[Source]:
    """扫描一个用户的全部剪辑与录像。"""
    label = user or gamerecordings.parent.name
    return scan_clips(gamerecordings, resolver, label) + scan_recordings(
        gamerecordings, resolver, label
    )
