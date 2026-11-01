import argparse
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import time
from dataclasses import asdict

from common import ROOT, load_config, operation_guard
from smarttube_adb import SmartTube, ManualOverride
from spotify_source import SpotifySource
from storage import Store
from youtube_match import AmbiguousMatch, most_viewed, resolve, score

LOG = logging.getLogger(__name__)


class TrackChanged(Exception):
    """An active Spotify selection changed while a search was in progress."""


class Bridge:
    def __init__(self, cfg, source, tv, store, dry_run=False):
        self.cfg, self.source, self.tv, self.store = cfg, source, tv, store
        self.dry_run = dry_run
        self.last_observed = None
        self.retry_at, self.failures = 0, 0
        self.last_done = None if dry_run else store.get_state("last_done")
        self.destination = (cfg["tv_target"], cfg["smarttube_package"])
        self.generation = None
        self.next_control_check = 0

    def step(self):
        try:
            if not self.dry_run:
                self.generation = self.tv.check_control()["generation"]
            return self._step()
        except TrackChanged:
            self.cancel_pending()
            LOG.info("STALE: Spotify changed or paused during resolution; pending work cancelled")
            return "stale"
        except ManualOverride:
            self.cancel_pending()
            return "manual_override"

    def cancel_pending(self):
        self.retry_at, self.failures = 0, 0
        self.next_control_check = 0

    def check_pending(self):
        if time.monotonic() >= self.next_control_check:
            # A foreground check alone cannot cancel an expensive search when
            # the user switches tracks without changing the active TV app.
            self.tv.check_control(self.generation)
            # The guard is also used outside an active track search (e.g. a
            # cancellation while probing the TV). Only a tracked search has an
            # expected Spotify id to compare against.
            if self.last_observed is not None:
                current = self.source.current()
                if current is None or current.id != self.last_observed:
                    raise TrackChanged()
            self.next_control_check = time.monotonic() + self.cfg["poll_seconds"]

    def wait(self, delay):
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            time.sleep(min(self.cfg["poll_seconds"], max(0, deadline - time.monotonic())))
            if not self.dry_run:
                try:
                    self.tv.check_control(self.generation)
                except ManualOverride:
                    self.cancel_pending()
                    return

    def _step(self):
        track = self.source.current()
        if not track:
            return "idle"
        if track.id != self.last_observed:
            self.last_observed = track.id
            self.retry_at, self.failures = 0, 0
            LOG.info("SPOTIFY %s | %s — %s | %.3fs | album=%s", track.id,
                     ", ".join(track.artists), track.title, track.duration, track.album)
        if not self.dry_run and self.store.was_handled(track.id, self.destination):
            LOG.info("SEND_BLOCKED_ALREADY_HANDLED track=%s", track.id)
            return "duplicate"
        if (self.last_done and self.last_done["outcome"] == "no_match" and self.last_done["track_id"] == track.id
                and tuple(self.last_done["destination"]) == self.destination
                and self.last_done.get("policy") == self.store.fingerprint(track, self.cfg)
                and time.time() - self.last_done["time"] < 86400):
            return "duplicate"
        if time.monotonic() < self.retry_at:
            return "retry_wait"
        try:
            return self.handle(track)
        except (RuntimeError, OSError, ValueError) as exc:
            if not self.dry_run:
                self.tv.check_control(self.generation)
            if getattr(exc, "retry_after", None):
                raise
            self.failures += 1
            delay = min(300, 15 * 2 ** min(self.failures - 1, 5))
            self.retry_at = time.monotonic() + delay
            LOG.error("FAILED %s; retry in %ss", exc, delay)
            return "error"

    def handle(self, track):
        hit, candidate = self.store.lookup(track, self.cfg)
        if hit:
            LOG.info("CACHE %s %s", track.id, candidate["id"] if candidate else "no reliable video")
            selected = score(track, candidate, self.cfg) if candidate else None
            # Only a verified official clip or a previous fallback is reused; an official audio is reconsidered.
            if selected and not (candidate.get("fallback") or (selected.accepted and selected.tier == 1)):
                hit = False
        if not hit:
            error = None
            try:
                with operation_guard(None if self.dry_run else self.check_pending):
                    selected, ranking = resolve(track, self.cfg)
            except AmbiguousMatch as exc:
                selected, ranking, error = None, exc.ranking, str(exc).removesuffix("; nothing sent")
            # The report is local, overwritten per search, and never published.
            (ROOT / "logs/last-match.json").write_text(json.dumps(
                {"track": asdict(track), **({"error": error} if error else {}), "ranking": ranking}, ensure_ascii=False, indent=2))
            if not selected or selected.tier != 1:
                LOG.info("NO VERIFIED CLIP (%s): checking conservative popularity fallback", error or (selected.kind if selected else "no match"))
                selected = most_viewed(track, ranking, self.cfg) or selected
            candidate = selected.candidate if selected else None
            if not self.dry_run:
                self.store.remember(track, self.cfg, candidate)
        if not candidate:
            LOG.warning("NO RELIABLE MATCH: %s — %s; nothing sent", track.artists[0], track.title)
            self.done(track, "no_match")
            return "no_match"
        LOG.info("SELECTED %s score=%s | %s", candidate["id"], selected.score, candidate["title"])
        if self.dry_run:
            LOG.info("DRY-RUN: no video launched, cache and playback state untouched")
            self.done(track, "dry_run")
            return "dry_run"
        self.tv.check_control(self.generation)
        latest = self.source.current()
        if not latest or latest.id != track.id:
            LOG.info("STALE: track changed or paused during resolution; send cancelled")
            return "stale"
        if not self.tv.send(candidate["id"], track_id=track.id, generation=self.generation):
            return "duplicate"
        self.done(track, "sent", candidate["id"])
        return "sent"

    def done(self, track, outcome, video_id=None):
        self.last_done = {"track_id": track.id, "destination": self.destination,
                          "outcome": outcome, "video_id": video_id, "time": time.time(),
                          "policy": self.store.fingerprint(track, self.cfg)}
        if not self.dry_run:
            self.store.set_state("last_done", self.last_done)


def setup_logging(console=True):
    (ROOT / "logs").mkdir(exist_ok=True, mode=0o700)
    handlers = [RotatingFileHandler(ROOT / "logs/bridge.log", maxBytes=2_000_000,
                                    backupCount=4, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("spotipy").setLevel(logging.CRITICAL)


def main():
    parser = argparse.ArgumentParser(description="Spotify → SmartTube")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--service", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    (ROOT / "cache").mkdir(exist_ok=True, mode=0o700)
    setup_logging(not args.service)
    cfg = load_config()
    if not (ROOT / "config.json").exists():
        LOG.warning("config.json not found: using config.example.json defaults (placeholder tv_target); cp config.example.json config.json")
    lock = None
    if not args.dry_run:
        lock = (ROOT / "cache/bridge.lock").open("a+")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.error("Another instance is already running. ./smarttube stop before a real run.")
            return 1
    source = SpotifySource(cfg)
    store = Store(ROOT / "cache/matches.sqlite3")
    tv = SmartTube(cfg, store)
    if not args.dry_run:
        try:
            tv.enter_override("Service started; Spotify in front or resume required")
        except ManualOverride:
            pass
    bridge = Bridge(cfg, source, tv, store, args.dry_run)
    LOG.info("START pid=%s source=%s dry_run=%s target=%s", os.getpid(), source.name, args.dry_run, cfg["tv_target"])
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    status, source_failures = "idle", 0
    previous_status, last_heartbeat = None, 0
    try:
        while True:
            wait = cfg["poll_seconds"]
            try:
                status = bridge.step()
                source_failures = 0
                if status == "stale":
                    wait = 0  # read the new Spotify selection on the next loop
            except Exception as exc:
                source_failures += 1
                wait = max(getattr(exc, "retry_after", 0), min(300, 15 * 2 ** min(source_failures - 1, 5)))
                LOG.error("Source unavailable (%s): %s; retry in %ss", type(exc).__name__,
                          str(exc)[:400] if source.name in {"macos", "tv"} or isinstance(exc, RuntimeError) else "API error (private details withheld)", wait)
                status = "error"
            if not args.dry_run and (status != previous_status or time.monotonic() - last_heartbeat >= 30):
                store.set_state("runtime", {"pid": os.getpid(), "source": source.name,
                                "status": status, "time": time.time()})
                last_heartbeat = time.monotonic()
            if status == "idle" and previous_status != "idle":
                LOG.info("IDLE: Spotify closed, paused, or not playing a music track")
            previous_status = status
            if args.once:
                if status == "idle":
                    LOG.info("No Spotify track playing")
                return 1 if status in {"error", "stale", "manual_override"} else 0
            bridge.wait(wait)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()
        if lock:
            lock.close()
        LOG.info("STOP")


if __name__ == "__main__":
    sys.exit(main())
