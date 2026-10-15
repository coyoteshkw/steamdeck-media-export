"""扫描器测试：会话名解析、分组、clip.pb 元数据。"""

import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from steam_exporter import pb
from steam_exporter.scanner import (
    CLIP_DIR_RE,
    ClipMeta,
    SessionName,
    group_session_dirs,
)


class TestSessionName(unittest.TestCase):
    def test_clip_session(self):
        info = SessionName("fg_1030300_20250905_150551")
        self.assertEqual(info.prefix, "fg")
        self.assertEqual(info.game_id, "1030300")
        self.assertIsNone(info.cont)
        self.assertEqual(info.recorded_at.tzinfo, timezone.utc)
        self.assertEqual(info.recorded_at.strftime("%Y-%m-%d %H:%M:%S"), "2025-09-05 15:05:51")
        self.assertEqual(info.base, "fg_1030300_20250905_150551")

    def test_background_continuation(self):
        info = SessionName("bg_204360_20261014_155159_0")
        self.assertEqual(info.prefix, "bg")
        self.assertEqual(info.cont, 0)
        self.assertEqual(info.base, "bg_204360_20261014_155159")

    def test_non_steam_huge_gameid(self):
        info = SessionName("bg_13929737710253113344_20260520_065249")
        self.assertEqual(info.game_id, "13929737710253113344")

    def test_unmatched_name(self):
        info = SessionName("weird_dir")
        self.assertIsNone(info.game_id)


class TestGrouping(unittest.TestCase):
    def test_groups_and_orders_continuations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = [
                "bg_204360_20261014_155159_0",
                "bg_204360_20261014_155159",
                "bg_48700_20261014_150740",
            ]
            dirs = []
            for name in names:
                path = root / name
                path.mkdir()
                dirs.append(path)
            groups = group_session_dirs(dirs)
            self.assertEqual(len(groups), 2)
            # 找到含续段的那一组，验证 base 在续段之前
            target = next(g for g in groups if any("155159" in p.name for p in g))
            first = [p.name for p in target]
            self.assertEqual(first[0], "bg_204360_20261014_155159")
            self.assertEqual(first[1], "bg_204360_20261014_155159_0")


class TestClipDirRegex(unittest.TestCase):
    def test_match(self):
        match = CLIP_DIR_RE.match("clip_753640_20260905_092200")
        self.assertIsNotNone(match)
        self.assertEqual(match.group("gameid"), "753640")


class TestClipMeta(unittest.TestCase):
    def _build(self):
        video = pb.encode_field(1, b"fg_1030300_20250905_150551")
        video += pb.encode_field(3, 17355)
        nested = pb.encode_field(2, 1030300)
        nested += pb.encode_field(3, 1757079713)
        nested += pb.encode_field(5, video)
        data = pb.encode_field(1, nested)
        data += pb.encode_field(7, "Outer Wilds - 2026".encode())
        data += pb.encode_field(12, 1280)
        data += pb.encode_field(13, 800)
        return data

    def test_extract(self):
        meta = ClipMeta(pb.parse(self._build()))
        self.assertEqual(meta.game_id, "1030300")
        self.assertEqual(meta.session, "fg_1030300_20250905_150551")
        self.assertAlmostEqual(meta.duration_s, 17.355)
        self.assertEqual(meta.title, "Outer Wilds - 2026")
        self.assertEqual(meta.width, 1280)
        self.assertEqual(meta.height, 800)
        self.assertEqual(meta.recorded_at.year, 2025)


if __name__ == "__main__":
    unittest.main()
