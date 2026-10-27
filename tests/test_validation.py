"""Validation and legacy database recovery regression tests."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from common import Track
from storage import Store


class ValidationTests(unittest.TestCase):
    def test_track_rejects_incomplete_or_malformed_metadata(self):
        valid = ("A" * 22, "Track", ("Artist",), 180)
        for values in (
            (None, *valid[1:]),
            (valid[0], None, valid[2], valid[3]),
            (valid[0], valid[1], (None,), valid[3]),
            (valid[0], valid[1], valid[2], "180"),
            (valid[0], valid[1], valid[2], True),
            (valid[0], valid[1], valid[2], float("nan")),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Track(*values)

    def test_incomplete_legacy_last_done_does_not_prevent_startup(self):
        snapshots = [
            "invalid legacy payload",
            {"outcome": "sent", "track_id": "A" * 22},
            {"outcome": "sent", "track_id": "A" * 22,
             "destination": "not a destination", "time": 1},
            {"outcome": "sent", "track_id": "A" * 22,
             "destination": ["tv", "smarttube"], "time": float("nan")},
        ]
        for snapshot in snapshots:
            with self.subTest(snapshot=snapshot), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "legacy.sqlite3"
                with sqlite3.connect(path) as db:
                    db.execute("CREATE TABLE state (name TEXT PRIMARY KEY, payload TEXT)")
                    db.execute("INSERT INTO state VALUES ('last_done', ?)",
                               (json.dumps(snapshot),))
                store = Store(path)
                try:
                    self.assertFalse(store.was_handled("A" * 22, ("tv", "smarttube")))
                finally:
                    store.close()


if __name__ == "__main__":
    unittest.main()
