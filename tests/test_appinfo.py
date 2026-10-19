"""appinfo.vdf（v41）解析测试：用于补齐「装过又卸载」的 Steam 游戏名。"""

import struct
import tempfile
import unittest
from pathlib import Path

from steam_exporter.steam import _parse_appinfo

POOL = ["appinfo", "appid", "common", "name"]


def build_appinfo(entries: list[tuple[int, str]]) -> bytes:
    """构造一个最小的 v41 appinfo.vdf。"""
    string_table = struct.pack("<I", len(POOL)) + b"".join(
        item.encode("utf-8") + b"\x00" for item in POOL
    )

    blobs = b""
    for appid, name in entries:
        body = (
            b"\x00"
            + struct.pack("<I", 0)  # appinfo
            + b"\x02"
            + struct.pack("<I", 1)
            + struct.pack("<i", appid)
            + b"\x00"
            + struct.pack("<I", 2)  # common
            + b"\x01"
            + struct.pack("<I", 3)  # name
            + name.encode("utf-8")
            + b"\x00"
            + b"\x08"  # end common
            + b"\x08"  # end appinfo
        )
        header = (
            struct.pack("<II", 2, 0)  # info state / last updated
            + struct.pack("<Q", 0)  # token
            + b"\x00" * 20  # sha1
            + struct.pack("<I", 0)  # change number
            + b"\x00" * 20  # binary sha1
        )
        blobs += struct.pack("<II", appid, len(header) + len(body)) + header + body

    blobs += struct.pack("<I", 0)  # 结束标记：appid == 0
    strtab_offset = 16 + len(blobs)
    return (
        struct.pack("<II", 0x07564429, 1)
        + struct.pack("<q", strtab_offset)
        + blobs
        + string_table
    )


class ParseAppinfoTests(unittest.TestCase):
    def test_reads_names(self) -> None:
        data = build_appinfo([(42, "Test Game"), (7, "Steam Client")])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appinfo.vdf"
            path.write_bytes(data)
            names = _parse_appinfo(path)
        self.assertEqual(names, {"42": "Test Game", "7": "Steam Client"})

    def test_missing_file_returns_empty(self) -> None:
        self.assertEqual(_parse_appinfo(Path("/nonexistent/appinfo.vdf")), {})

    def test_wrong_magic_returns_empty(self) -> None:
        data = build_appinfo([(1, "X")])
        data = b"\x00\x00\x00\x00" + data[4:]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appinfo.vdf"
            path.write_bytes(data)
            self.assertEqual(_parse_appinfo(path), {})

    def test_truncated_file_does_not_raise(self) -> None:
        data = build_appinfo([(1, "X")])[:30]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appinfo.vdf"
            path.write_bytes(data)
            self.assertEqual(_parse_appinfo(path), {})


if __name__ == "__main__":
    unittest.main()
