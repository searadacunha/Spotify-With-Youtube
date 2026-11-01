"""Opt-in cache prewarming from an explicit playlist export; no TV or OAuth use."""
from __future__ import annotations

import json
import time
from pathlib import Path

from common import Track
from youtube_match import AmbiguousMatch, most_viewed, resolve

MAX_PLAYLIST_BYTES = 2_000_000
MAX_TRACKS = 500
FIELDS = {"id", "title", "artists", "duration_ms", "album"}


def load_export(path: Path) -> list[Track]:
    """Accept an intentionally provided metadata-only JSON list, never tokens."""
    if path.stat().st_size > MAX_PLAYLIST_BYTES:
        raise ValueError("Playlist export is too large (2 MB maximum)")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TRACKS:
        raise ValueError("Expected a JSON array of 1 to 500 tracks")
    tracks = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) - FIELDS:
            raise ValueError(f"Track {index}: unexpected metadata field")
        duration = row.get("duration_ms")
        artists = row.get("artists")
        album = row.get("album", "")
        if type(duration) is not int or duration <= 0:
            raise ValueError(f"Track {index}: duration_ms must be positive integer")
        if (type(artists) is not list or not artists or
                not all(isinstance(a, str) and a.strip() for a in artists)):
            raise ValueError(f"Track {index}: artists must be a nonempty list of names")
        if not isinstance(album, str):
            raise ValueError(f"Track {index}: album must be a string")
        try:
            track = Track(row["id"], row["title"], tuple(artists),
                          duration / 1000, album)
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError(f"Track {index}: invalid metadata ({type(exc).__name__})") from None
        if track.id not in seen:
            tracks.append(track)
            seen.add(track.id)
    return tracks


def prewarm_tracks(tracks: list[Track], cfg, store, *, resolver=resolve) -> dict:
    """Populate only the matching cache; never call Spotify transport or ADB."""
    start = time.monotonic()
    counters = {"total": len(tracks), "already_cached": 0, "cache_written": 0,
                "no_match": 0, "errors": 0, "concurrent_skips": 0}
    for track in tracks:
        hit, _ = store.lookup(track, cfg)
        if hit:
            counters["already_cached"] += 1
            continue
        try:
            try:
                selected, ranking = resolver(track, cfg)
            except AmbiguousMatch as exc:
                selected, ranking = None, exc.ranking
            if not selected or selected.tier != 1:
                selected = most_viewed(track, ranking, cfg) or selected
            candidate = selected.candidate if selected else None
            if store.remember_if_stale(track, cfg, candidate):
                counters["cache_written"] += 1
                if candidate is None:
                    counters["no_match"] += 1
            else:
                counters["concurrent_skips"] += 1
        except (RuntimeError, OSError, ValueError):
            # Do not transform transient network/matching failures into a
            # 24-hour negative cache; live playback must remain free to retry.
            counters["errors"] += 1
    counters["elapsed_seconds"] = round(time.monotonic() - start, 3)
    return counters


def prewarm_file(path: Path, cfg, store) -> dict:
    return prewarm_tracks(load_export(path), cfg, store)
