"""选择逻辑（library）测试。"""

import unittest
from datetime import date, datetime, timezone

from steam_exporter.library import Filters, filter_sources, sort_sources
from steam_exporter.model import KIND_BACKGROUND, KIND_CLIP, Source


def make(source_id, kind, game, year, month, day, size=100, duration=10, user="u"):
    return Source(
        kind=kind,
        source_id=source_id,
        game_id="1",
        app_id="1",
        game=game,
        recorded_at=datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc),
        parts=[],
        size_bytes=size,
        duration_s=duration,
        user=user,
    )


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.items = [
            make("a", KIND_CLIP, "Outer Wilds", 2026, 9, 1),
            make("b", KIND_BACKGROUND, "Outer Wilds", 2026, 10, 1),
            make("c", KIND_CLIP, "Celeste", 2025, 1, 1),
        ]

    def test_type(self):
        self.assertEqual(
            [s.source_id for s in filter_sources(self.items, Filters(type="clip"))],
            ["a", "c"],
        )
        self.assertEqual(
            [s.source_id for s in filter_sources(self.items, Filters(type="background"))],
            ["b"],
        )
        self.assertEqual(
            [s.source_id for s in filter_sources(self.items, Filters(type="recording"))],
            ["b"],
        )

    def test_game_case_insensitive(self):
        result = filter_sources(self.items, Filters(games=["celeste"]))
        self.assertEqual([s.source_id for s in result], ["c"])

    def test_date_range(self):
        result = filter_sources(self.items, Filters(since=date(2026, 1, 1)))
        self.assertEqual({s.source_id for s in result}, {"a", "b"})
        result = filter_sources(self.items, Filters(until=date(2025, 12, 31)))
        self.assertEqual({s.source_id for s in result}, {"c"})

    def test_ids_substring(self):
        result = filter_sources(self.items, Filters(ids=["a", "ZZZ"]))
        self.assertEqual([s.source_id for s in result], ["a"])

    def test_is_active(self):
        self.assertFalse(Filters().is_active())
        self.assertTrue(Filters(type="clip").is_active())
        self.assertTrue(Filters(games=["x"]).is_active())
        self.assertTrue(Filters(ids=["a"]).is_active())


class TestSort(unittest.TestCase):
    def test_sort(self):
        items = [
            make("a", KIND_CLIP, "g", 2026, 9, 1),
            make("b", KIND_CLIP, "g", 2026, 10, 1),
            make("c", KIND_CLIP, "g", 2025, 1, 1),
        ]
        asc = [s.source_id for s in sort_sources(items, newest_first=False)]
        self.assertEqual(asc, ["c", "a", "b"])
        desc = [s.source_id for s in sort_sources(items, newest_first=True)]
        self.assertEqual(desc, ["b", "a", "c"])


class TestSourceDict(unittest.TestCase):
    def test_to_dict(self):
        source = make("clip_x", KIND_CLIP, "Outer Wilds", 2026, 9, 1)
        data = source.to_dict()
        self.assertEqual(data["id"], "clip_x")
        self.assertEqual(data["kind"], KIND_CLIP)
        self.assertEqual(data["kind_cn"], "剪辑")
        self.assertEqual(data["game"], "Outer Wilds")
        self.assertIn("recorded_at", data)
        self.assertNotIn("parts", data)
        self.assertIn("parts", source.to_dict(include_parts=True))


if __name__ == "__main__":
    unittest.main()
