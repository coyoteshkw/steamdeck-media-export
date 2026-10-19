"""Steam 环境发现与游戏名解析。

包括：
- 找到 Steam 安装根目录（原生 / Flatpak）
- 解析 ``libraryfolders.vdf``（文本 VDF）找到所有库目录
- 读取 ``appmanifest_*.acf`` 得到已安装游戏名
- 解析 ``shortcuts.vdf``（二进制 VDF）得到非 Steam 快捷方式名
- 解析 ``loginusers.vdf`` 找到当前主账号
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# 路径发现
# ---------------------------------------------------------------------------
_HOME = Path.home()

STEAM_ROOT_CANDIDATES = [
    _HOME / ".local/share/Steam",
    _HOME / ".steam/steam",
    _HOME / ".steam/root",
    _HOME / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
    _HOME / ".var/app/com.valvesoftware.Steam/data/Steam",
]


def find_steam_roots() -> list[Path]:
    """返回所有存在的 Steam 根目录（去掉重复）。"""
    seen: list[Path] = []
    for candidate in STEAM_ROOT_CANDIDATES:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if candidate.exists() and resolved not in seen:
            seen.append(resolved)
    return seen


def default_steam_root() -> Path | None:
    roots = find_steam_roots()
    return roots[0] if roots else None


def find_userdata_dirs(steam_root: Path | None = None) -> list[Path]:
    """返回含有 ``gamerecordings`` 的用户目录（``.../userdata/<id>``）。"""
    roots = [steam_root] if steam_root else find_steam_roots()
    out: list[Path] = []
    for root in roots:
        userdata = root / "userdata"
        if not userdata.is_dir():
            continue
        for child in sorted(userdata.iterdir()):
            gamerecordings = child / "gamerecordings"
            if child.is_dir() and gamerecordings.is_dir():
                out.append(child)
    return out


# ---------------------------------------------------------------------------
# 文本 VDF
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[{}]')


def parse_vdf_text(text: str) -> dict:
    """解析 Valve 文本 VDF（keyvalues）为嵌套 dict。"""
    tokens = _TOKEN_RE.findall(text)
    pos = 0

    def parse_obj() -> dict:
        nonlocal pos
        out: dict = {}
        while pos < len(tokens):
            token = tokens[pos]
            if token == "}":
                pos += 1
                return out
            if token == "{":
                pos += 1
                continue
            key = _unquote(token)
            pos += 1
            if pos >= len(tokens):
                break
            if tokens[pos] == "{":
                pos += 1
                out[key] = parse_obj()
            else:
                out[key] = _unquote(tokens[pos])
                pos += 1
        return out

    return parse_obj()


def _unquote(token: str) -> str:
    if token.startswith('"') and token.endswith('"'):
        inner = token[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return token


# ---------------------------------------------------------------------------
# 二进制 VDF（shortcuts.vdf）
# ---------------------------------------------------------------------------
def parse_binary_vdf(buf: bytes, offset: int = 0) -> dict:
    """解析 Valve 二进制 VDF，返回嵌套 dict。

    类型标记：0x00 嵌套开始，0x01 字符串，0x02 int32，0x07 uint32，0x08 嵌套结束。
    """
    out: dict = {}
    i = offset

    def read_str(i: int) -> tuple[str, int]:
        end = buf.index(b"\x00", i)
        return buf[i:end].decode("utf-8", "replace"), end + 1

    while i < len(buf):
        token = buf[i]
        i += 1
        if token == 0x00:
            key, i = read_str(i)
            out[key], i = parse_binary_vdf(buf, i)
        elif token == 0x08:
            return out, i
        elif token == 0x01:
            key, i = read_str(i)
            value, i = read_str(i)
            out[key] = value
        elif token == 0x02:
            key, i = read_str(i)
            out[key] = struct.unpack_from("<i", buf, i)[0]
            i += 4
        elif token == 0x07:
            key, i = read_str(i)
            out[key] = struct.unpack_from("<I", buf, i)[0]
            i += 4
        else:
            # 未知类型，停止解析以避免死循环
            return out, i
    return out, i


# ---------------------------------------------------------------------------
# appinfo.vdf：卸载游戏的名字兜底
# ---------------------------------------------------------------------------
def _parse_appinfo(path: Path) -> dict[str, str]:
    """解析 ``appcache/appinfo.vdf``（v41），返回 ``{appid: name}``。

    `appmanifest_*.acf` 在游戏卸载后会被删掉，`shortcuts.vdf` 只覆盖非 Steam
    快捷方式；所以「装过又卸载」的 Steam 游戏名，本地只剩 appinfo.vdf 里有。

    v41 布局：magic/universe/字符串表偏移，然后是重复的
    ``appid(4) + size(4) + [infostate(4) lastupdated(4) token(8) sha1(20)
    changenumber(4) bsha1(20)] + binary VDF``。
    binary VDF 里 **键是字符串表索引（u32），字符串值则是内联的 null 结尾字符串**。

    只处理 v41（magic 0x07564429）；其它版本或解析异常直接返回空，不影响原有逻辑。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    if len(data) < 20:
        return {}
    try:
        magic = struct.unpack_from("<I", data, 0)[0]
        strtab = struct.unpack_from("<q", data, 8)[0]
    except struct.error:
        return {}
    if magic != 0x07564429 or not (20 <= strtab < len(data)):
        return {}

    try:
        count = struct.unpack_from("<I", data, strtab)[0]
        pool: list[str] = []
        i = strtab + 4
        for _ in range(count):
            end = data.index(b"\x00", i)
            pool.append(data[i:end].decode("utf-8", "replace"))
            i = end + 1
    except (struct.error, ValueError):
        return {}

    def read_vdf(buf: bytes, offset: int) -> tuple[dict, int]:
        out: dict = {}
        o = offset
        while o < len(buf):
            tag = buf[o]
            o += 1
            if tag == 0x08:  # end
                return out, o
            if tag == 0x00:  # 嵌套
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                if key >= len(pool):
                    return out, o
                out[pool[key]], o = read_vdf(buf, o)
            elif tag == 0x01:  # 字符串：键=索引，值=内联
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                if key >= len(pool):
                    return out, o
                end = buf.index(b"\x00", o)
                out[pool[key]] = buf[o:end].decode("utf-8", "replace")
                o = end + 1
            elif tag == 0x02:  # int32
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                out[pool[key]] = struct.unpack_from("<i", buf, o)[0]
                o += 4
            elif tag == 0x03:  # float32
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                out[pool[key]] = struct.unpack_from("<f", buf, o)[0]
                o += 4
            elif tag == 0x06:  # color(3)
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                out[pool[key]] = tuple(buf[o : o + 3])
                o += 3
            elif tag == 0x07:  # uint64
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                out[pool[key]] = struct.unpack_from("<Q", buf, o)[0]
                o += 8
            elif tag == 0x0A:  # int64
                key = struct.unpack_from("<I", buf, o)[0]
                o += 4
                out[pool[key]] = struct.unpack_from("<q", buf, o)[0]
                o += 8
            else:
                return out, o
        return out, o

    names: dict[str, str] = {}
    o = 16
    total = len(data)
    header_len = 4 + 4 + 8 + 20 + 4 + 20  # 条目内固定头部（62 之后才是 VDF）
    while o + 8 <= total:
        appid, size = struct.unpack_from("<II", data, o)
        if appid == 0:
            break
        end = o + 8 + size
        if end > total or size < header_len:
            break
        body = data[o + 8 + header_len : end]
        try:
            kv, _ = read_vdf(body, 0)
            name = kv.get("appinfo", {}).get("common", {}).get("name")
            if isinstance(name, str) and name:
                names[str(appid)] = name
        except (struct.error, ValueError, IndexError):
            pass
        o = end
    return names


# ---------------------------------------------------------------------------
# 游戏名解析
# ---------------------------------------------------------------------------
@dataclass
class NameResolver:
    """把 game_id / app_id 映射成游戏名。"""

    by_appid: dict[str, str] = field(default_factory=dict)
    by_gameid: dict[str, str] = field(default_factory=dict)

    def add_appid(self, appid: int | str, name: str) -> None:
        if name:
            self.by_appid[str(appid)] = name

    def name_for(self, game_id: str) -> str | None:
        if game_id in self.by_gameid:
            return self.by_gameid[game_id]
        appid = appid_from_game_id(game_id)
        if appid is not None and appid in self.by_appid:
            return self.by_appid[appid]
        return None

    def load(self, steam_root: Path | None = None) -> "NameResolver":
        roots = [steam_root] if steam_root else find_steam_roots()
        # 先用 appinfo.vdf 垫底（能补齐「装过又卸载」的 Steam 游戏名），
        # 后面的 shortcuts / appmanifest 会覆盖它（更权威）。
        for root in roots:
            for appid, name in _parse_appinfo(root / "appcache" / "appinfo.vdf").items():
                self.by_appid.setdefault(str(appid), name)
        for root in roots:
            self._load_shortcuts(root)
            for steamapps in find_steamapps_dirs(root):
                self._load_appmanifests(steamapps)
        # 非 Steam 快捷方式的 gameid 形如 (appid << 32) | 0x02000000
        for appid, name in list(self.by_appid.items()):
            try:
                numeric = int(appid)
            except ValueError:
                continue
            if 0 < numeric < (1 << 32):
                self.by_gameid[str((numeric << 32) | 0x02000000)] = name
        return self

    def _load_appmanifests(self, steamapps: Path) -> None:
        for acf in steamapps.glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            match = re.search(r'"name"\s+"((?:[^"\\]|\\.)*)"', text)
            if not match:
                continue
            name = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
            appid = acf.stem.removeprefix("appmanifest_")
            self.add_appid(appid, name)

    def _load_shortcuts(self, steam_root: Path) -> None:
        userdata = steam_root / "userdata"
        if not userdata.is_dir():
            return
        for user in userdata.iterdir():
            shortcut_file = user / "config" / "shortcuts.vdf"
            if not shortcut_file.is_file():
                continue
            try:
                data = shortcut_file.read_bytes()
            except OSError:
                continue
            try:
                parsed, _ = parse_binary_vdf(data)
            except (ValueError, IndexError):
                continue
            shortcuts = parsed.get("shortcuts", {})
            if not isinstance(shortcuts, dict):
                continue
            for entry in shortcuts.values():
                if not isinstance(entry, dict):
                    continue
                appid = entry.get("appid")
                name = entry.get("AppName")
                if isinstance(appid, int) and name:
                    self.add_appid(appid & 0xFFFFFFFF, str(name))


def find_steamapps_dirs(steam_root: Path) -> list[Path]:
    """根据 ``libraryfolders.vdf`` 找到所有 ``steamapps`` 目录。"""
    dirs: list[Path] = []
    primary = steam_root / "steamapps"
    if primary.is_dir():
        dirs.append(primary)
    vdf = primary / "libraryfolders.vdf"
    if vdf.is_file():
        try:
            parsed = parse_vdf_text(vdf.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            parsed = {}
        folders = parsed.get("libraryfolders", {})
        if isinstance(folders, dict):
            for value in folders.values():
                if isinstance(value, dict) and value.get("path"):
                    candidate = Path(str(value["path"])) / "steamapps"
                    if candidate.is_dir() and candidate not in dirs:
                        dirs.append(candidate)
    return dirs


def appid_from_game_id(game_id: str) -> str | None:
    """从 game_id 推出 appid。

    - 普通 Steam 游戏：game_id 就是 appid。
    - 非 Steam 快捷方式：低 32 位为 ``0x02000000``，appid = game_id >> 32。
    """
    try:
        value = int(game_id)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value < (1 << 32):
        return str(value)
    if (value & 0xFFFFFFFF) == 0x02000000:
        return str(value >> 32)
    return str(value >> 32)


def primary_user(steam_root: Path | None = None) -> str | None:
    """返回主账号的 **32 位 account id**（与 userdata 目录名一致）。

    ``loginusers.vdf`` 里的键是 64 位 SteamID，优先看 ``MostRecent``，
    其次 ``AutoLogin``，再其次最新的 ``timestamp``；最后换算成低 32 位。
    """
    roots = [steam_root] if steam_root else find_steam_roots()
    for root in roots:
        vdf = root / "config" / "loginusers.vdf"
        if not vdf.is_file():
            continue
        try:
            parsed = parse_vdf_text(vdf.read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            continue
        users = parsed.get("users", {})
        if not isinstance(users, dict) or not users:
            continue
        candidates = []
        for steamid, info in users.items():
            if not isinstance(info, dict):
                continue
            most = str(info.get("MostRecent", "0")) == "1"
            auto = str(info.get("AutoLogin", "0")) == "1"
            try:
                timestamp = int(info.get("timestamp", "0") or 0)
            except (TypeError, ValueError):
                timestamp = 0
            candidates.append((most, auto, timestamp, str(steamid)))
        if not candidates:
            continue
        candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
        steamid = candidates[0][3]
        try:
            return str(int(steamid) & 0xFFFFFFFF)
        except ValueError:
            return steamid
    return None


def iter_game_appids(game_id: str) -> Iterator[str]:
    appid = appid_from_game_id(game_id)
    if appid:
        yield appid
