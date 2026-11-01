#!/usr/bin/env python3
"""Describe observed-track -> Android-intent latency from local logs.

No hardware calls, no network access; dispatch is not confirmed video playback.
Only completed, explicitly logged sends enter the sample.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

MARKER = "DISPATCH_METRIC "


def read_events(lines) -> tuple[list[dict], int]:
    accepted, invalid = [], 0
    for line in lines:
        if MARKER not in line:
            continue
        try:
            row = json.loads(line.split(MARKER, 1)[1].strip())
            value = row["seconds_from_observation"]
            if (not isinstance(row, dict) or row.get("schema") != 1
                    or row.get("event") != "android_intent_sent"
                    or row.get("source") not in ("cache", "search")
                    or row.get("playback_on_tv_confirmed") is not False
                    or type(value) not in (int, float) or not math.isfinite(value)
                    or value < 0):
                raise ValueError("Malformed metric")
        except (ValueError, TypeError, KeyError, AttributeError):
            invalid += 1
            continue
        accepted.append(row)
    return accepted, invalid


def report(events: list[dict], invalid: int = 0) -> dict:
    values = sorted(event["seconds_from_observation"] for event in events)
    return {
        "measurement": "elapsed seconds from first observed Spotify track to confirmed ADB intent",
        "not_measured": "actual Android video/audio playback start, user skip latency, failed or suppressed sends",
        "samples": len(values),
        "malformed_records": invalid,
        "cache_sends": sum(event["source"] == "cache" for event in events),
        "search_sends": sum(event["source"] == "search" for event in events),
        "p50_seconds": statistics.median(values) if values else None,
        "p95_seconds": values[max(0, math.ceil(.95 * len(values)) - 1)] if values else None,
        "max_seconds": max(values) if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Local logs/bridge.log path")
    args = parser.parse_args()
    with args.log.open(encoding="utf-8", errors="replace") as stream:
        events, invalid = read_events(stream)
    print(json.dumps(report(events, invalid), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
