"""命名模板与输出路径测试。"""

import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from steam_exporter.exporter import (
    MAX_CONCAT_ARG_LEN,
    ExportOptions,
    _find_donor_init,
    _init_video_size,
    _merge_stream_files,
    build_ffmpeg_args,
    build_limited_stream_input,
    build_stream_input,
    render_relative_path,
    resolve_output_path,
)
from steam_exporter.model import KIND_CLIP, Source


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + box_type + payload


def make_init(width: int, height: int) -> bytes:
    """构造一个带 moov/trak/tkhd 的假 init 段。"""
    tkhd = _box(
        b"tkhd",
        b"\x00" * 76
        + struct.pack(">I", width * 65536)
        + struct.pack(">I", height * 65536),
    )
    return _box(b"ftyp", b"isom") + _box(b"moov", _box(b"trak", tkhd))


def make_source(**overrides) -> Source:
    base = dict(
        kind=KIND_CLIP,
        source_id="clip_753640_20260905_092200",
        game_id="753640",
        app_id="753640",
        game="Outer Wilds",
        recorded_at=datetime(2026, 9, 5, 1, 22, 0, tzinfo=timezone.utc),
        parts=[],
        duration_s=45.0,
    )
    base.update(overrides)
    return Source(**base)


class TestRender(unittest.TestCase):
    def test_default_template(self):
        source = make_source()
        rel = render_relative_path(source, "{game}/{date} {time} - {kind_cn}.mp4")
        self.assertTrue(rel.endswith(".mp4"))
        self.assertIn("Outer Wilds/", rel)
        self.assertIn("剪辑", rel)

    def test_duration_token(self):
        rel = render_relative_path(
            make_source(duration_s=3725), "{duration}.mp4"
        )
        self.assertEqual(rel, "1h02m05s.mp4")

    def test_sanitizes_variable(self):
        rel = render_relative_path(make_source(game="A/B"), "{game}.mp4")
        self.assertEqual(rel, "A_B.mp4")

    def test_unknown_variable_raises(self):
        with self.assertRaises(KeyError):
            render_relative_path(make_source(), "{nope}.mp4")


class TestResolve(unittest.TestCase):
    def test_rejects_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            opts = ExportOptions(out_dir=Path(tmp), template="{game}/../../x.mp4")
            with self.assertRaises(ValueError):
                resolve_output_path(make_source(game="g"), opts, set())

    def test_collision_avoidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            opts = ExportOptions(out_dir=out, template="{game}.mp4")
            taken: set[str] = set()
            first, _ = resolve_output_path(make_source(), opts, taken)
            taken.add(str(first))
            second, _ = resolve_output_path(make_source(), opts, taken)
            self.assertNotEqual(first, second)
            self.assertIn("(2)", second.name)


class TestBuildStreamInput(unittest.TestCase):
    def test_concatenates_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp)
            (part / "init-stream0.m4s").write_text("init")
            for i in (1, 2):
                (part / f"chunk-stream0-{i:05d}.m4s").write_text(f"c{i}")
            spec = build_stream_input([part], 0)
            self.assertTrue(spec.startswith("concat:"))
            files = spec.removeprefix("concat:").split("|")
            self.assertEqual(len(files), 3)
            self.assertTrue(files[0].endswith("init-stream0.m4s"))
            self.assertTrue(files[1].endswith("00001.m4s"))
            self.assertTrue(files[2].endswith("00002.m4s"))

    def test_missing_stream_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(build_stream_input([Path(tmp)], 1))

    def test_long_paths_use_short_symlinks(self):
        """长录像分片过多时应改用短符号链接，避免 Argument list too long。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / ("很长的目录名" * 8)
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            for i in range(2000):
                (part / f"chunk-stream0-{i:05d}.m4s").write_bytes(b"x")
            link_root = root / "links"
            spec = build_stream_input([part], 0, link_root)
            # 原始写法会超过内核上限
            raw = "concat:" + "|".join(
                str(p) for p in [part / "init-stream0.m4s"]
                + sorted(part.glob("chunk-stream0-*.m4s"))
            )
            self.assertGreater(len(raw), MAX_CONCAT_ARG_LEN)
            self.assertLessEqual(len(spec), MAX_CONCAT_ARG_LEN)
            # 引用的是符号链接目录里的短名
            first = spec.removeprefix("concat:").split("|")[0]
            self.assertIn("links", first)
            self.assertTrue(Path(first).is_symlink())

    def test_merge_stream_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for i in range(3):
                p = root / f"c{i}.m4s"
                p.write_bytes(bytes([i]))
                files.append(p)
            merged = Path(_merge_stream_files(files, root / "out", 0))
            self.assertTrue(merged.is_file())
            self.assertEqual(merged.read_bytes(), b"\x00\x01\x02")

    def test_limited_stream_input_caps_chunks(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "part"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            for i in range(1, 51):
                (part / f"chunk-stream0-{i:05d}.m4s").write_bytes(b"x")
            spec = build_limited_stream_input([part], 0, 30)
            files = spec.removeprefix("concat:").split("|")
            self.assertTrue(files[0].endswith("init-stream0.m4s"))
            self.assertLessEqual(len(files) - 1, 12)  # 30/3+2


class TestCorruptionRecovery(unittest.TestCase):
    def test_init_video_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "init.m4s"
            path.write_bytes(make_init(1280, 800))
            self.assertEqual(_init_video_size(path), (1280, 800))

    def test_zero_byte_init_and_chunks_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "part"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"")  # 空头
            (part / "chunk-stream0-00001.m4s").write_bytes(b"x")
            (part / "chunk-stream0-00002.m4s").write_bytes(b"")  # 空分片
            spec = build_stream_input([part], 0)
            files = spec.removeprefix("concat:").split("|")
            self.assertNotIn("init-stream0.m4s", spec)
            self.assertEqual(len(files), 1)

    def test_missing_init_uses_donor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            part = root / "clip" / "video" / "fg_1_20260101_000000"
            part.mkdir(parents=True)
            (part / "init-stream0.m4s").write_bytes(b"")
            (part / "chunk-stream0-00001.m4s").write_bytes(b"x")
            donor = root / "donor.m4s"
            donor.write_bytes(make_init(1280, 800))
            spec = build_stream_input([part], 0, donor_init=donor)
            first = spec.removeprefix("concat:").split("|")[0]
            self.assertEqual(Path(first), donor)

    def test_find_donor_matches_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "gamerecordings"
            good = root / "clips" / "clip_a" / "video" / "fg_x"
            good.mkdir(parents=True)
            (good / "init-stream0.m4s").write_bytes(make_init(1280, 800))
            broken = root / "clips" / "clip_b" / "video" / "fg_y"
            broken.mkdir(parents=True)
            (broken / "init-stream0.m4s").write_bytes(b"")
            source = Source(
                kind=KIND_CLIP,
                source_id="clip_b",
                game_id="1",
                app_id="1",
                game="g",
                recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                parts=[broken],
                width=1280,
                height=800,
            )
            self.assertEqual(_find_donor_init(source, 0), good / "init-stream0.m4s")

    def test_find_donor_rejects_wrong_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "gamerecordings"
            good = root / "clips" / "clip_a" / "video" / "fg_x"
            good.mkdir(parents=True)
            (good / "init-stream0.m4s").write_bytes(make_init(1280, 720))
            broken = root / "clips" / "clip_b" / "video" / "fg_y"
            broken.mkdir(parents=True)
            (broken / "init-stream0.m4s").write_bytes(b"")
            source = Source(
                kind=KIND_CLIP,
                source_id="clip_b",
                game_id="1",
                app_id="1",
                game="g",
                recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                parts=[broken],
                width=1280,
                height=800,
            )
            self.assertIsNone(_find_donor_init(source, 0))


class TestTrim(unittest.TestCase):
    def _source(self, part: Path, duration: float | None) -> Source:
        return Source(
            kind=KIND_CLIP,
            source_id="clip_x",
            game_id="1",
            app_id="1",
            game="g",
            recorded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            parts=[part],
            duration_s=duration,
        )

    def test_trim_adds_t(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "part"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            (part / "chunk-stream0-00001.m4s").write_bytes(b"x")
            opts = ExportOptions(out_dir=Path(tmp) / "out")
            args = build_ffmpeg_args(self._source(part, 45.0), opts, Path(tmp) / "o.mp4")
            self.assertIn("-t", args)
            self.assertEqual(args[args.index("-t") + 1], "45.000")

    def test_no_trim_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "part"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            (part / "chunk-stream0-00001.m4s").write_bytes(b"x")
            opts = ExportOptions(out_dir=Path(tmp) / "out", trim=False)
            args = build_ffmpeg_args(self._source(part, 45.0), opts, Path(tmp) / "o.mp4")
            self.assertNotIn("-t", args)

    def test_no_duration_no_trim(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "part"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            (part / "chunk-stream0-00001.m4s").write_bytes(b"x")
            opts = ExportOptions(out_dir=Path(tmp) / "out")
            args = build_ffmpeg_args(self._source(part, None), opts, Path(tmp) / "o.mp4")
            self.assertNotIn("-t", args)

    def test_streaming_uses_fragmented_pipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            part = Path(tmp) / "part"
            part.mkdir()
            (part / "init-stream0.m4s").write_bytes(b"init")
            (part / "chunk-stream0-00001.m4s").write_bytes(b"x")
            opts = ExportOptions(out_dir=Path(tmp) / "out")
            args = build_ffmpeg_args(self._source(part, 45.0), opts, None, streaming=True)
            self.assertIn("pipe:1", args)
            self.assertIn("frag_keyframe+empty_moov+default_base_moof", args)
            self.assertNotIn("-y", args)
            self.assertIn("-t", args)


if __name__ == "__main__":
    unittest.main()
