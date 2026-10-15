"""预览模块测试（选段与缓存键）。"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from steam_exporter.model import KIND_BACKGROUND, Source
from steam_exporter.preview import PreviewCache, pick_segment


def make_source(part: Path, user="u", source_id="bg_1"):
    return Source(
        kind=KIND_BACKGROUND,
        source_id=source_id,
        game_id="1",
        app_id="1",
        game="G",
        recorded_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        parts=[part],
        user=user,
    )


class TestPickSegment(unittest.TestCase):
    def _part(self, root, name="p", init=b"init", chunks=10):
        part = root / name
        part.mkdir()
        (part / "init-stream0.m4s").write_bytes(init)
        for i in range(1, chunks + 1):
            (part / f"chunk-stream0-{i:05d}.m4s").write_bytes(b"x")
        return part

    def test_picks_middle_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = self._part(Path(tmp))
            init, chunks = pick_segment(make_source(part), seconds=6)
            self.assertEqual(init.name, "init-stream0.m4s")
            self.assertEqual(len(chunks), 3)  # 6/3+1
            self.assertEqual(chunks[0].name, "chunk-stream0-00004.m4s")

    def test_missing_init_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = self._part(Path(tmp), init=b"")
            self.assertIsNone(pick_segment(make_source(part)))

    def test_no_chunks_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "p"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            self.assertIsNone(pick_segment(make_source(part)))

    def test_prefers_largest_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = self._part(root, "small", chunks=1)
            big = self._part(root, "big", chunks=20)
            source = make_source(small)
            source.parts = [small, big]
            init, chunks = pick_segment(source, seconds=6)
            self.assertEqual(init.parent.name, "big")


class TestPreviewCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = PreviewCache(Path(self._tmp.name), ttl=100, max_bytes=1000)

    def tearDown(self):
        self._tmp.cleanup()

    def test_key_varies(self):
        source = make_source(Path("/tmp"))
        self.assertNotEqual(
            PreviewCache.key(source, "thumb"), PreviewCache.key(source, "preview")
        )
        other = make_source(Path("/tmp"), source_id="bg_2")
        self.assertNotEqual(
            PreviewCache.key(source, "thumb"), PreviewCache.key(other, "thumb")
        )

    def test_has_and_stats(self):
        source = make_source(Path("/tmp"))
        key = PreviewCache.key(source, "thumb")
        self.assertFalse(self.cache.has(key, "jpg"))
        self.cache.path_for(key, "jpg").write_bytes(b"x" * 50)
        self.assertTrue(self.cache.has(key, "jpg"))
        self.assertEqual(self.cache.stats()["count"], 1)


if __name__ == "__main__":
    unittest.main()
