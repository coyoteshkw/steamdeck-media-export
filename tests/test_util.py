"""工具函数测试。"""

import tempfile
import unittest
from pathlib import Path

from steam_exporter import util


class TestFormat(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(util.human_size(0), "0 B")
        self.assertEqual(util.human_size(512), "512 B")
        self.assertEqual(util.human_size(1536), "1.5 KB")
        self.assertEqual(util.human_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(util.human_size(None), "-")

    def test_human_duration(self):
        self.assertEqual(util.human_duration(0), "0:00")
        self.assertEqual(util.human_duration(63), "1:03")
        self.assertEqual(util.human_duration(3723), "1:02:03")
        self.assertEqual(util.human_duration(None), "-")

    def test_iso8601_duration(self):
        self.assertAlmostEqual(util.parse_iso8601_duration("PT17.355S"), 17.355)
        self.assertAlmostEqual(util.parse_iso8601_duration("PT12M48.31S"), 768.31)
        self.assertAlmostEqual(util.parse_iso8601_duration("PT1H0M0.0S"), 3600)
        self.assertIsNone(util.parse_iso8601_duration("nonsense"))

    def test_parse_out_time(self):
        self.assertAlmostEqual(util.parse_out_time("00:00:12.500000"), 12.5)
        self.assertAlmostEqual(util.parse_out_time("01:02:03.000000"), 3723.0)
        self.assertIsNone(util.parse_out_time("N/A"))
        self.assertIsNone(util.parse_out_time("bogus"))


class TestSanitize(unittest.TestCase):
    def test_removes_path_separators(self):
        self.assertEqual(util.sanitize_value("a/b"), "a_b")
        self.assertEqual(util.sanitize_value("a:b*c?"), "a_b_c_")

    def test_keeps_normal_text(self):
        self.assertEqual(util.sanitize_value("Outer Wilds 2"), "Outer Wilds 2")
        self.assertEqual(util.sanitize_component("Öoo"), "Öoo")


class TestSessionDuration(unittest.TestCase):
    def test_declared_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp)
            (part / "session.mpd").write_text(
                '<MPD mediaPresentationDuration="PT17.355S">'
                '<SegmentTemplate timescale="1000000" duration="3000000"/></MPD>'
            )
            self.assertAlmostEqual(util.session_duration_seconds(part), 17.355)

    def test_rolling_buffer_estimate(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp)
            (part / "session.mpd").write_text(
                '<MPD mediaPresentationDuration="PT0.0S">'
                '<SegmentTemplate timescale="1000000" duration="3000000"/></MPD>'
            )
            for i in range(10):
                (part / f"chunk-stream0-{i:05d}.m4s").write_bytes(b"x")
            self.assertAlmostEqual(util.session_duration_seconds(part), 30.0)


class TestUniquePath(unittest.TestCase):
    def test_appends_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.mp4"
            target.write_text("x")
            self.assertEqual(util.unique_path(target).name, "a (2).mp4")

    def test_nofile_limit(self):
        self.assertGreater(util.nofile_limit(), 0)

    def test_raise_nofile_limit_is_safe(self):
        util.raise_nofile_limit()  # 不应抛异常
        self.assertGreater(util.nofile_limit(), 0)


if __name__ == "__main__":
    unittest.main()
