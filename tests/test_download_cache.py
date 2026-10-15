"""下载缓存与 Range 解析测试。"""

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from steam_exporter.download_cache import DownloadCache
from steam_exporter.server import _parse_range


def source():
    return SimpleNamespace(user="u", source_id="clip_1")


def options(**overrides):
    base = dict(
        mode="compress",
        codec="libx264",
        crf=23,
        preset="veryfast",
        scale=None,
        trim=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDownloadCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cache = DownloadCache(self.root, ttl=60, max_bytes=100)

    def tearDown(self):
        self._tmp.cleanup()

    def test_key_depends_on_settings(self):
        first = DownloadCache.key(source(), options())
        second = DownloadCache.key(source(), options(preset="medium"))
        self.assertNotEqual(first, second)
        self.assertEqual(first, DownloadCache.key(source(), options()))

    def test_store_name_and_delete(self):
        key = "abc"
        self.assertFalse(self.cache.has(key))
        self.cache.path_for(key).write_bytes(b"x" * 10)
        self.assertTrue(self.cache.has(key))
        self.cache.store_name(key, "名字.mp4")
        self.assertEqual(self.cache.get_name(key), "名字.mp4")
        self.cache.delete(key)
        self.assertFalse(self.cache.has(key))
        self.assertIsNone(self.cache.get_name(key))

    def test_cleanup_expired(self):
        path = self.cache.path_for("old")
        path.write_bytes(b"x" * 10)
        old = time.time() - 3600
        os.utime(path, (old, old))
        removed = self.cache.cleanup()
        self.assertGreaterEqual(removed, 1)
        self.assertFalse(self.cache.has("old"))

    def test_cleanup_enforces_max_bytes(self):
        for index, key in enumerate(("a", "b", "c")):
            path = self.cache.path_for(key)
            path.write_bytes(b"x" * 60)
            stamp = time.time() - (3 - index)
            os.utime(path, (stamp, stamp))
        self.cache.cleanup()
        remaining = [key for key in ("a", "b", "c") if self.cache.has(key)]
        self.assertEqual(remaining, ["c"])  # 最旧的被 LRU 删除

    def test_clear_and_stats(self):
        self.cache.path_for("a").write_bytes(b"x" * 5)
        self.assertEqual(self.cache.stats()["count"], 1)
        self.cache.clear()
        self.assertEqual(self.cache.stats()["count"], 0)


class TestParseRange(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(_parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(_parse_range("bytes=500-", 1000), (500, 999))
        self.assertEqual(_parse_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(_parse_range("bytes=0-10000", 1000), (0, 999))

    def test_invalid(self):
        self.assertIsNone(_parse_range(None, 1000))
        self.assertIsNone(_parse_range("bytes=abc", 1000))
        self.assertIsNone(_parse_range("bytes=1000-", 1000))
        self.assertIsNone(_parse_range("bytes=5-3", 1000))


if __name__ == "__main__":
    unittest.main()
