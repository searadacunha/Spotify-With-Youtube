"""Explicit playlist prewarming never touches the TV and avoids cache races."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from common import Track, load_config
from prewarm import load_export, prewarm_tracks
from storage import Store
from youtube_match import Match


class PlaylistWarmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "playlist.json"
        self.store = Store(Path(self.temp.name) / "cache.sqlite3")
        self.addCleanup(self.store.close)
        self.cfg = load_config()
        self.row = {"id": "A" * 22, "title": "Papaoutai",
                    "artists": ["Stromae"], "duration_ms": 232000, "album": "Racine carrée"}
        self.track = Track(self.row["id"], self.row["title"],
                           tuple(self.row["artists"]), 232, self.row["album"])
        self.candidate = {"id": "C" * 11, "title": "Stromae - Papaoutai",
                          "channel": "Stromae", "duration": 232, "view_count": 20}

    def test_explicit_json_only_and_deduplicated(self):
        self.path.write_text(json.dumps([self.row, self.row]))
        self.assertEqual(load_export(self.path), [self.track])
        for invalid in ([{**self.row, "access_token": "secret"}],
                        [{**self.row, "duration_ms": True}],
                        [{**self.row, "artists": "Stromae"}],
                        [{**self.row, "artists": []}],
                        [{**self.row, "album": {"title": "invalid"}}], [{}]):
            with self.subTest(invalid=invalid):
                self.path.write_text(json.dumps(invalid))
                with self.assertRaises(ValueError):
                    load_export(self.path)

    def test_preloaded_match_reused_without_network(self):
        match = Match(self.candidate, 120, "video", True, [], tier=1)
        resolver = Mock(return_value=(match, []))
        result = prewarm_tracks([self.track], self.cfg, self.store, resolver=resolver)
        self.assertEqual(result["cache_written"], 1)
        self.assertEqual(self.store.lookup(self.track, self.cfg)[1]["id"], self.candidate["id"])
        result = prewarm_tracks([self.track], self.cfg, self.store, resolver=resolver)
        self.assertEqual(result["already_cached"], 1)
        resolver.assert_called_once()

    def test_live_write_wins_over_speculative_prefetch(self):
        live = {**self.candidate, "id": "L" * 11}
        def live_write_during_resolution(track, cfg):
            self.store.remember(track, cfg, live)
            return Match(self.candidate, 120, "video", True, [], tier=1), []
        result = prewarm_tracks([self.track], self.cfg, self.store,
                               resolver=live_write_during_resolution)
        self.assertEqual(result["concurrent_skips"], 1)
        self.assertEqual(self.store.lookup(self.track, self.cfg)[1]["id"], live["id"])

    def test_transient_failure_is_not_negative_cached(self):
        resolver = Mock(side_effect=RuntimeError("temporary network failure"))
        result = prewarm_tracks([self.track], self.cfg, self.store, resolver=resolver)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(self.store.lookup(self.track, self.cfg), (False, None))

    def test_unrelated_result_is_negative_cached_not_sent(self):
        from youtube_match import AmbiguousMatch
        wrong = {**self.candidate, "title": "Stromae - Formidable"}
        resolver = Mock(side_effect=AmbiguousMatch("unreliable", [{"candidate": wrong}]))
        result = prewarm_tracks([self.track], self.cfg, self.store, resolver=resolver)
        self.assertEqual(result["no_match"], 1)
        self.assertEqual(self.store.lookup(self.track, self.cfg), (True, None))


if __name__ == "__main__":
    unittest.main()
