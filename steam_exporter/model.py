"""数据模型：一条可导出的录制记录。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

KIND_CLIP = "clip"
KIND_BACKGROUND = "background"
KIND_FOREGROUND = "foreground"

KIND_LABELS_CN = {
    KIND_CLIP: "剪辑",
    KIND_BACKGROUND: "后台录像",
    KIND_FOREGROUND: "录像",
}


@dataclass
class Source:
    """一条可导出的录制。

    ``parts`` 是构成这条录制的视频会话目录，按播放顺序排列。普通剪辑只有
    一个目录；后台录像被 Steam 续写到 ``_0``、``_1`` … 时会有多个目录。
    """

    kind: str
    source_id: str
    game_id: str
    app_id: str
    game: str
    recorded_at: datetime
    parts: list[Path]
    size_bytes: int = 0
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    title: str | None = None
    thumbnail: Path | None = None
    timeline: Path | None = None
    clip_dir: Path | None = None
    user: str = ""
    playable: bool = True

    @property
    def kind_cn(self) -> str:
        return KIND_LABELS_CN.get(self.kind, self.kind)

    @property
    def display_name(self) -> str:
        return self.title or f"{self.game} {self.recorded_at:%Y-%m-%d %H:%M:%S}"

    def describe(self) -> str:
        bits = [
            self.kind_cn,
            self.game,
            self.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        ]
        from .util import human_duration, human_size

        bits.append(human_duration(self.duration_s))
        bits.append(human_size(self.size_bytes))
        return "  ".join(bits)

    def to_dict(self, *, include_parts: bool = False) -> dict:
        """序列化为 JSON 友好 dict（供网页接口使用）。"""
        data = {
            "id": self.source_id,
            "kind": self.kind,
            "kind_cn": self.kind_cn,
            "game": self.game,
            "appid": self.app_id,
            "gameid": self.game_id,
            "recorded_at": self.recorded_at.astimezone().isoformat(),
            "recorded_display": self.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": self.duration_s,
            "size": self.size_bytes,
            "title": self.title,
            "user": self.user,
            "playable": self.playable,
        }
        if include_parts:
            data["parts"] = [str(p) for p in self.parts]
        return data
