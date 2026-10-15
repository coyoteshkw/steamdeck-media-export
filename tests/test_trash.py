"""回收站与删除路径测试。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from steam_exporter.model import KIND_BACKGROUND, KIND_CLIP, Source
from steam_exporter.trash import Trash


def fake_source(kind=KIND_CLIP, source_id="clip_1", game="Game"):
    return SimpleNamespace(
        kind=kind,
        source_id=source_id,
        game=game,
        recorded_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        clip_dir=None,
        parts=[],
    )


class TestTrash(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.trash = Trash(self.root / "trash")

    def tearDown(self):
        self._tmp.cleanup()

    def _make_clip(self, name="clip_x"):
        clip = self.root / "gamerecordings" / "clips" / name
        (clip / "video").mkdir(parents=True)
        (clip / "clip.pb").write_bytes(b"x" * 100)
        (clip / "video" / "chunk.m4s").write_bytes(b"y" * 900)
        return clip

    def test_move_and_stats(self):
        clip = self._make_clip()
        entry = self.trash.move(fake_source(), [clip])
        self.assertIsNotNone(entry)
        self.assertFalse(clip.exists())
        self.assertEqual(entry["size"], 1000)
        stats = self.trash.stats()
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["bytes"], 1000)

    def test_restore(self):
        clip = self._make_clip()
        entry = self.trash.move(fake_source(), [clip])
        restored = self.trash.restore(entry["id"])
        self.assertEqual(restored, 1)
        self.assertTrue((clip / "clip.pb").is_file())
        self.assertEqual(self.trash.stats()["count"], 0)

    def test_restore_all(self):
        clip_a = self._make_clip("clip_a")
        clip_b = self._make_clip("clip_b")
        self.trash.move(fake_source(source_id="a"), [clip_a])
        self.trash.move(fake_source(source_id="b"), [clip_b])
        self.assertEqual(self.trash.stats()["count"], 2)
        self.trash.restore_all()
        self.assertTrue(clip_a.exists() and clip_b.exists())
        self.assertEqual(self.trash.stats()["count"], 0)

    def test_purge(self):
        clip = self._make_clip()
        entry = self.trash.move(fake_source(), [clip])
        freed = self.trash.purge(entry["id"])
        self.assertEqual(freed, 1000)
        self.assertEqual(self.trash.stats()["count"], 0)
        self.assertFalse((self.trash.files_dir / entry["id"]).exists())

    def test_purge_all(self):
        for name in ("a", "b"):
            self.trash.move(fake_source(source_id=name), [self._make_clip(f"clip_{name}")])
        freed = self.trash.purge_all()
        self.assertEqual(self.trash.stats()["count"], 0)
        self.assertGreater(freed, 0)

    def test_persist_index(self):
        clip = self._make_clip()
        self.trash.move(fake_source(), [clip])
        again = Trash(self.root / "trash")
        self.assertEqual(again.stats()["count"], 1)

    def test_delete_permanently(self):
        clip = self._make_clip()
        size = Trash.delete_permanently([clip])
        self.assertEqual(size, 1000)
        self.assertFalse(clip.exists())


class TestDeletePaths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.gamerecordings = self.root / "gamerecordings"
        (self.gamerecordings / "timelines").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clip_uses_clip_dir(self):
        from steam_exporter.server import _delete_paths

        clip = self.gamerecordings / "clips" / "clip_1"
        clip.mkdir(parents=True)
        source = Source(
            kind=KIND_CLIP,
            source_id="clip_1",
            game_id="1",
            app_id="1",
            game="G",
            recorded_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            parts=[],
            clip_dir=clip,
        )
        self.assertEqual(_delete_paths(source), [clip])

    def test_recording_includes_timeline(self):
        from steam_exporter.server import _delete_paths, _recording_timeline

        session = self.gamerecordings / "video" / "bg_1_20261001_120000"
        session.mkdir(parents=True)
        source = Source(
            kind=KIND_BACKGROUND,
            source_id="bg_1_20261001_120000",
            game_id="1",
            app_id="1",
            game="G",
            recorded_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            parts=[session],
        )
        timeline = self.gamerecordings / "timelines" / "timeline_1_20261001_120000.json"
        timeline.write_text(
            json.dumps({"daterecorded": str(int(source.recorded_at.timestamp()))}),
            encoding="utf-8",
        )
        self.assertEqual(_recording_timeline(source), timeline)
        paths = _delete_paths(source)
        self.assertIn(session, paths)
        self.assertIn(timeline, paths)


if __name__ == "__main__":
    unittest.main()
