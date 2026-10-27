import hashlib
import json
import math
import sqlite3
import time
from dataclasses import asdict

from youtube_match import SCORING_VERSION


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS matches (track_id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT, expires REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, payload TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS handled_tracks (track_id TEXT, target TEXT, package TEXT, outcome TEXT, video_id TEXT, time REAL, PRIMARY KEY (track_id, target, package))")
        self.db.commit()
        previous = self.get_state("last_done")
        # Older installs may contain incomplete state snapshots. Migrate only
        # complete confirmed sends; a malformed record must not crash startup.
        destination = previous.get("destination") if isinstance(previous, dict) else None
        when = previous.get("time") if isinstance(previous, dict) else None
        if (isinstance(previous, dict) and previous.get("outcome") == "sent"
                and isinstance(previous.get("track_id"), str)
                and isinstance(destination, (list, tuple)) and len(destination) == 2
                and all(isinstance(part, str) and part for part in destination)
                and type(when) in (int, float) and math.isfinite(when)):
            self.record_handled(previous["track_id"], destination,
                                previous.get("video_id"), "sent", when)

    @staticmethod
    def fingerprint(track, cfg):
        policy = {k: cfg[k] for k in ("min_score", "prefer_official", "allow_audio_fallback",
                   "max_duration_delta_seconds", "max_duration_delta_ratio", "search_results")}
        # Spotify TV reports the same track 1 ms longer or shorter from one day to the next.
        raw = json.dumps([SCORING_VERSION, {**asdict(track), "duration": round(track.duration)}, policy], sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def lookup(self, track, cfg):
        row = self.db.execute("SELECT fingerprint, payload, expires FROM matches WHERE track_id=?", (track.id,)).fetchone()
        if not row or row[2] < time.time():
            return False, None
        candidate = json.loads(row[1])
        if row[0] != self.fingerprint(track, cfg):
            return False, None
        return True, candidate

    def remember(self, track, cfg, candidate):
        # A fallback is retried the next day: an official clip may have been missed by a transient error.
        ttl = 90 * 86400 if candidate and not candidate.get("fallback") else 86400
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO matches VALUES (?, ?, ?, ?)",
                            (track.id, self.fingerprint(track, cfg), json.dumps(candidate), time.time() + ttl))

    def get_state(self, name):
        row = self.db.execute("SELECT payload FROM state WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, name, data):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (name, json.dumps(data)))

    def was_handled(self, track_id, destination):
        # Only the latest track sent to this TV is blocked: a song played again later gets its clip again.
        row = self.db.execute("SELECT track_id FROM handled_tracks WHERE target=? AND package=? ORDER BY time DESC LIMIT 1",
                              tuple(destination)).fetchone()
        return row is not None and row[0] == track_id

    def record_handled(self, track_id, destination, video_id, outcome="attempted", when=None):
        # Record before dispatch: an uncertain delivery must never become an automatic retry.
        # shortcut: check-then-write is not atomic across processes; the bridge.lock flock keeps a single sender.
        if self.was_handled(track_id, destination):
            return False
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO handled_tracks VALUES (?, ?, ?, ?, ?, ?)",
                            (track_id, *destination, outcome, video_id, time.time() if when is None else when))
        return True

    def confirm_handled(self, track_id, destination):
        with self.db:
            self.db.execute("UPDATE handled_tracks SET outcome='sent' WHERE track_id=? AND target=? AND package=?",
                            (track_id, *destination))

    def replace_control(self, previous, current):
        """Do not overwrite a newer explicit resume issued by another process."""
        with self.db:
            if previous is None:
                result = self.db.execute("INSERT OR IGNORE INTO state VALUES ('tv_control', ?)", (json.dumps(current),))
            else:
                result = self.db.execute("UPDATE state SET payload=? WHERE name='tv_control' AND payload=?",
                                         (json.dumps(current), json.dumps(previous)))
        return result.rowcount == 1

    def close(self):
        self.db.close()
