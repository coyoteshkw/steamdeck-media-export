"""protobuf 解析测试。"""

import unittest

from steam_exporter import pb


class TestPb(unittest.TestCase):
    def test_varint_roundtrip(self):
        data = b"".join(
            [
                pb.encode_field(2, 1030300),
                pb.encode_field(3, 1757079713),
            ]
        )
        msg = pb.parse(data)
        self.assertEqual(pb.first_int(msg, 2), 1030300)
        self.assertEqual(pb.first_int(msg, 3), 1757079713)

    def test_string_field(self):
        data = pb.encode_field(7, "Outer Wilds - 2026".encode("utf-8"))
        msg = pb.parse(data)
        self.assertEqual(pb.first_str(msg, 7), "Outer Wilds - 2026")

    def test_nested_message(self):
        inner = pb.encode_field(1, "fg_1030300_20250905_150551".encode())
        inner += pb.encode_field(3, 17355)
        data = pb.encode_field(1, inner)
        msg = pb.parse(data)
        sub = pb.first_msg(msg, 1)
        self.assertIsNotNone(sub)
        self.assertEqual(pb.first_str(sub, 1), "fg_1030300_20250905_150551")
        self.assertEqual(pb.first_int(sub, 3), 17355)

    def test_truncated_raises(self):
        with self.assertRaises(pb.ProtobufError):
            pb.parse(b"\x0a\x05ab")  # 声称 5 字节，实际不足

    def test_missing_field_returns_none(self):
        msg = pb.parse(pb.encode_field(1, 42))
        self.assertIsNone(pb.first_int(msg, 99))
        self.assertIsNone(pb.first_str(msg, 99))


if __name__ == "__main__":
    unittest.main()
