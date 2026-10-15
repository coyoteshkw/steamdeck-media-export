"""极简 protobuf wire 解析器。

只实现本项目需要的部分：解析 Steam 的 ``clip.pb`` / ``gamerecording.pb``。
不依赖 ``protobuf`` 库，避免额外安装。

protobuf wire type：
    0 varint,  1 64bit,  2 length-delimited,  5 32bit
"""

from __future__ import annotations

from typing import Any, Iterator

WT_VARINT = 0
WT_64BIT = 1
WT_LEN = 2
WT_32BIT = 5


class ProtobufError(ValueError):
    """protobuf 数据损坏或不支持。"""


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if i >= len(buf):
            raise ProtobufError("varint 越界")
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 70:
            raise ProtobufError("varint 过长")


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, Any]]:
    """逐个产出 ``(field_number, wire_type, value)``。

    length-delimited 的 value 是 ``bytes``，其余是 ``int``（64/32bit 为 bytes）。
    """
    i = 0
    n = len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wt = key >> 3, key & 7
        if field == 0:
            raise ProtobufError("field number 为 0")
        if wt == WT_VARINT:
            value, i = _read_varint(buf, i)
        elif wt == WT_LEN:
            length, i = _read_varint(buf, i)
            if i + length > n:
                raise ProtobufError("length-delimited 越界")
            value = buf[i : i + length]
            i += length
        elif wt == WT_64BIT:
            value = buf[i : i + 8]
            i += 8
        elif wt == WT_32BIT:
            value = buf[i : i + 4]
            i += 4
        else:
            raise ProtobufError(f"不支持的 wire type: {wt}")
        yield field, wt, value


def parse(buf: bytes) -> dict[int, list[Any]]:
    """解析为 ``{字段号: [值, ...]}``。"""
    out: dict[int, list[Any]] = {}
    for field, _wt, value in iter_fields(buf):
        out.setdefault(field, []).append(value)
    return out


def parse_submsg(buf: bytes) -> dict[int, list[Any]]:
    """尝试把一段 bytes 当作子消息解析；失败返回空 dict。"""
    try:
        return parse(buf)
    except ProtobufError:
        return {}


def first_int(msg: dict[int, list[Any]], field: int) -> int | None:
    values = msg.get(field)
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, int) else None


def first_bytes(msg: dict[int, list[Any]], field: int) -> bytes | None:
    values = msg.get(field)
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, (bytes, bytearray)) else None


def first_str(msg: dict[int, list[Any]], field: int) -> str | None:
    raw = first_bytes(msg, field)
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def first_msg(msg: dict[int, list[Any]], field: int) -> dict[int, list[Any]] | None:
    raw = first_bytes(msg, field)
    if raw is None:
        return None
    sub = parse_submsg(raw)
    return sub or None


# ---------------------------------------------------------------------------
# 编码（仅测试使用）
# ---------------------------------------------------------------------------
def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_field(field: int, value: int | bytes) -> bytes:
    """编码单个字段，便于测试构造 protobuf 数据。"""
    if isinstance(value, int):
        return _write_varint((field << 3) | WT_VARINT) + _write_varint(value)
    return _write_varint((field << 3) | WT_LEN) + _write_varint(len(value)) + bytes(value)
