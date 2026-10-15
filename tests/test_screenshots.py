"""截图功能测试：扫描、vdf 元数据、过滤分组、回收站集成。"""

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import steam_exporter.screenshots as S
from steam_exporter.screenshots import (
    Screenshot,
    ShotFilters,
    filter_screenshots,
    group_screenshots,
    load_metadata,
    scan_screenshots,
    sort_screenshots,
)
from steam_exporter.steam import NameResolver
from steam_exporter.trash import Trash

VDF = """
"screenshots"
{
	"1030300"
	{
		"0"
		{
			"type"		"1"
			"filename"		"1030300/screenshots/20251218045342_1.jpg"
			"thumbnail"		"1030300/screenshots/thumbnails/20251218045342_1.jpg"
			"width"		"1280"
			"height"		"800"
			"gameid"		"1030300"
			"creation"		"1766033622"
			"caption"		"漂亮的日落"
		}
	}
}
"""


class ShotFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.userdata = self.root / "userdata" / "331943233"
        shots = self.userdata / "760" / "remote" / "1030300" / "screenshots"
        shots.mkdir(parents=True)
        (shots / "20251218045342_1.jpg").write_bytes(b"x" * 100)
        (shots / "20250101000000_1.jpg").write_bytes(b"y" * 50)
        thumb_dir = shots / "thumbnails"
        thumb_dir.mkdir()
        (thumb_dir / "20251218045342_1.jpg").write_bytes(b"t" * 10)
        shortcut = (
            self.userdata / "760" / "remote" / "13765976036095295488" / "screenshots"
        )
        shortcut.mkdir(parents=True)
        (shortcut / "20260101000000_1.png").write_bytes(b"z" * 20)
        (self.userdata / "760" / "screenshots.vdf").write_text(VDF, encoding="utf-8")

        self._orig_roots = S.find_steam_roots
        self._orig_primary = S.primary_user
        S.find_steam_roots = lambda: [self.root]  # type: ignore[assignment]
        S.primary_user = lambda root=None: "331943233"  # type: ignore[assignment]
        self.resolver = NameResolver()
        self.resolver.add_appid("1030300", "测试游戏")

    def tearDown(self):
        S.find_steam_roots = self._orig_roots  # type: ignore[assignment]
        S.primary_user = self._orig_primary  # type: ignore[assignment]
        self.tmp.cleanup()

    def scan(self):
        return scan_screenshots("auto", self.resolver)


class TestScan(ShotFixture):
    def test_scan_counts_and_games(self):
        shots = self.scan()
        self.assertEqual(len(shots), 3)
        self.assertEqual({s.game_id for s in shots}, {"1030300", "13765976036095295488"})

    def test_vdf_metadata_applied(self):
        by_name = {s.name: s for s in self.scan()}
        shot = by_name["20251218045342_1.jpg"]
        self.assertEqual(shot.caption, "漂亮的日落")
        self.assertEqual(shot.width, 1280)
        self.assertEqual(shot.height, 800)
        self.assertEqual(
            shot.recorded_at, datetime.fromtimestamp(1766033622, tz=timezone.utc)
        )
        self.assertIsNotNone(shot.thumbnail)
        self.assertTrue(shot.thumbnail.is_file())

    def test_filename_time_fallback(self):
        by_name = {s.name: s for s in self.scan()}
        shot = by_name["20250101000000_1.jpg"]
        self.assertIsNone(shot.caption)
        self.assertIsNone(shot.thumbnail)
        self.assertEqual(
            shot.recorded_at, datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        )

    def test_png_and_shortcut_name(self):
        by_name = {s.name: s for s in self.scan()}
        shot = by_name["20260101000000_1.png"]
        self.assertTrue(shot.game.startswith("未知应用"))

    def test_delete_paths(self):
        by_name = {s.name: s for s in self.scan()}
        with_thumb = by_name["20251218045342_1.jpg"].delete_paths()
        without = by_name["20250101000000_1.jpg"].delete_paths()
        self.assertEqual(len(with_thumb), 2)
        self.assertEqual(len(without), 1)


class TestMetadata(ShotFixture):
    def test_load_metadata(self):
        meta = load_metadata(self.userdata / "760" / "screenshots.vdf")
        self.assertIn(("1030300", "20251218045342_1.jpg"), meta)
        self.assertEqual(meta[("1030300", "20251218045342_1.jpg")]["caption"], "漂亮的日落")

    def test_missing_vdf(self):
        self.assertEqual(load_metadata(self.root / "nope.vdf"), {})


class TestFilterGroup(ShotFixture):
    def test_filter_by_game_and_caption(self):
        shots = self.scan()
        self.assertEqual(len(filter_screenshots(shots, ShotFilters(game_ids=["1030300"]))), 2)
        self.assertEqual(len(filter_screenshots(shots, ShotFilters(caption_only=True))), 1)
        self.assertEqual(len(filter_screenshots(shots, ShotFilters(q="测试"))), 2)
        self.assertEqual(len(filter_screenshots(shots, ShotFilters(q="日落"))), 1)

    def test_filter_by_date(self):
        shots = self.scan()
        recent = filter_screenshots(shots, ShotFilters(since=date(2025, 12, 1)))
        self.assertEqual(
            {s.name for s in recent},
            {"20251218045342_1.jpg", "20260101000000_1.png"},
        )
        earlier = filter_screenshots(shots, ShotFilters(until=date(2025, 6, 1)))
        self.assertEqual([s.name for s in earlier], ["20250101000000_1.jpg"])

    def test_group(self):
        groups = group_screenshots(self.scan())
        self.assertEqual(len(groups), 2)
        counts = {g["gameid"]: g["count"] for g in groups}
        self.assertEqual(counts["1030300"], 2)

    def test_sort(self):
        shots = sort_screenshots(self.scan())
        stamps = [s.recorded_at for s in shots]
        self.assertEqual(stamps, sorted(stamps, reverse=True))


class TestTrashIntegration(ShotFixture):
    def test_move_and_restore(self):
        shot = next(s for s in self.scan() if s.thumbnail is not None)
        original = shot.path
        thumb = shot.thumbnail
        trash = Trash(self.root / "trash")
        entry = trash.move(shot, shot.delete_paths())
        self.assertIsNotNone(entry)
        self.assertFalse(original.exists())
        self.assertFalse(thumb.exists())
        self.assertGreater(entry["size"], 0)
        self.assertEqual(trash.stats()["count"], 1)

        trash.restore(entry["id"])
        self.assertTrue(original.exists())
        self.assertTrue(thumb.exists())


if __name__ == "__main__":
    unittest.main()
