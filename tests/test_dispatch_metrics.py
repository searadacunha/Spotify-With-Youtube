"""Dispatch telemetry is not a claim of real Android playback latency."""
import importlib.util
import json
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "scripts/analyze_dispatch_logs.py"
spec = importlib.util.spec_from_file_location("analyze_dispatch_logs", PATH)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def event(seconds, source="cache"):
    return "2026-10-28 INFO DISPATCH_METRIC " + json.dumps({
        "schema": 1, "event": "android_intent_sent",
        "seconds_from_observation": seconds, "source": source,
        "playback_on_tv_confirmed": False,
    }) + "\n"


class DispatchMetricsTests(unittest.TestCase):
    def test_latency_summary_and_cache_hits(self):
        lines = ["an unrelated log entry\n", event(0.7),
                 event(1.2, "search"), event(2.8), event(0.4)]
        rows, invalid = metrics.read_events(lines)
        stats = metrics.report(rows, invalid)
        self.assertEqual(stats["samples"], 4)
        self.assertEqual(stats["cache_sends"], 3)
        self.assertEqual(stats["search_sends"], 1)
        self.assertEqual(stats["p50_seconds"], 0.95)
        self.assertEqual(stats["p95_seconds"], 2.8)
        self.assertIn("not_measured", stats)

    def test_rejects_false_playback_claims_and_malformed_metrics(self):
        invalids = [
            event(-1), event(float("nan")), event(True), event(1).replace("false", "true"),
            "INFO DISPATCH_METRIC {}\n",
            "INFO DISPATCH_METRIC invalid json\n",
        ]
        rows, invalid = metrics.read_events(invalids)
        self.assertEqual(rows, [])
        self.assertEqual(invalid, len(invalids))
        self.assertIsNone(metrics.report(rows, invalid)["p50_seconds"])


if __name__ == "__main__":
    unittest.main()
