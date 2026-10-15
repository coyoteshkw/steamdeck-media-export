"""网页后端（server）测试：游戏图标查找。"""

import tempfile
import unittest
from pathlib import Path

from steam_exporter import server


class TestFindGameArt(unittest.TestCase):
    def setUp(self):
        server._ART_CACHE.clear()

    def _with_root(self, root: Path):
        original = server.find_steam_roots
        server.find_steam_roots = lambda: [root]
        self.addCleanup(lambda: setattr(server, "find_steam_roots", original))

    def test_prefers_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appdir = root / "appcache" / "librarycache" / "123"
            (appdir / "abc").mkdir(parents=True)
            (appdir / "abc" / "library_hero.jpg").write_bytes(b"hero")
            (appdir / "abc" / "library_header.jpg").write_bytes(b"header")
            self._with_root(root)
            self.assertEqual(
                server.find_game_art("123"), appdir / "abc" / "library_header.jpg"
            )

    def test_falls_back_to_portrait_then_logo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appdir = root / "appcache" / "librarycache" / "55"
            appdir.mkdir(parents=True)
            (appdir / "logo.png").write_bytes(b"logo")
            self._with_root(root)
            self.assertEqual(server.find_game_art("55"), appdir / "logo.png")

    def test_grid_fallback_for_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            grid = root / "userdata" / "331943233" / "config" / "grid"
            grid.mkdir(parents=True)
            (grid / "900p.png").write_bytes(b"grid")
            self._with_root(root)
            self.assertEqual(server.find_game_art("900"), grid / "900p.png")

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._with_root(Path(tmp))
            self.assertIsNone(server.find_game_art("777"))

    def test_rejects_non_numeric(self):
        self.assertIsNone(server.find_game_art("abc"))
        self.assertIsNone(server.find_game_art(""))


if __name__ == "__main__":
    unittest.main()
