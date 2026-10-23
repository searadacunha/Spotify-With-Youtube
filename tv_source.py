"""Spotify metadata read directly from the TV's Android media session."""
import hashlib
import html
import json
import logging
import re
import time
import urllib.request

from common import ROOT, Track
from smarttube_adb import SPOTIFY_TV, SmartTube
from youtube_match import norm

LOG = logging.getLogger(__name__)
TRACK_PAGE = "https://open.spotify.com/track/"


def page_metadata(page):
    """Artist and album from Spotify's public track page, in the TV's own format."""
    artist = re.search(r'<meta name="music:musician_description" content="([^"]+)"', page)
    description = re.search(r'<meta property="og:description" content="([^"]*)"', page)
    if not artist:
        raise ValueError("Spotify track page names no artist")
    # "Artist, Artist · Album · Song · 2018"
    parts = html.unescape(description[1]).split(" · ") if description else []
    return html.unescape(artist[1]), " · ".join(parts[1:-2])


def from_tv(sessions, lookup=None):
    if not isinstance(sessions, list) or not all(isinstance(s, dict) for s in sessions):
        raise ValueError("Invalid TV media session response")
    playing = [s for s in sessions if s.get("package") == SPOTIFY_TV
               and s.get("state") == 3]
    if len(playing) > 1:
        raise ValueError("Several active Spotify TV sessions: ambiguous playback")
    if not playing:
        return None
    data = playing[0]
    identifier = data.get("id") or ""
    if data.get("advertisement") or not re.fullmatch(r"spotify:track:[A-Za-z0-9]{22}", identifier):
        return None
    duration = data.get("duration_ms")
    if (type(duration) not in {int, float} or duration <= 0
            or not isinstance(data.get("title"), str)
            or not isinstance(data.get("artist"), str)):
        raise ValueError("Spotify TV did not provide complete music metadata")
    track_id, artist, album = identifier.split(":")[-1], data["artist"], data.get("album") or ""
    # Spotify TV's native video mode can repeat the title in every text field.
    base_title = re.sub(r"\s*(?:-\s*|\()radio edit\)?\s*$", "", data["title"], flags=re.I).strip()
    # Punctuation can differ between the fields ("… - REMIX" / "… [REMIX]").
    if norm(base_title) == norm(artist) == norm(album):
        found = lookup(track_id) if lookup else None
        if not found:
            # Idle, not an error: an error backs off up to 5 min and the next track would be missed.
            return None
        artist, album = found
    return Track(track_id, data["title"], (artist,), duration / 1000, album)


class TVSource:
    def __init__(self, cfg, tv=None):
        self.tv = tv if tv is not None else SmartTube(cfg)
        self.dex = ROOT / "android/classes.dex"
        self.ready = False
        self.pages = {}

    def current(self):
        return from_tv(self.sessions(), self.track_page)

    def track_page(self, track_id):
        # Polled every few seconds: one page read per track, a failed read retried after a minute.
        retry_at, found = self.pages.get(track_id, (0, None))
        if found or time.monotonic() < retry_at:
            return found
        try:
            with urllib.request.urlopen(TRACK_PAGE + track_id, timeout=10) as response:
                found = page_metadata(response.read().decode("utf-8", "replace"))
            LOG.info("Spotify video mode: artist read from the track page (%s)", found[0])
        except (OSError, ValueError) as exc:
            LOG.warning("Spotify video mode: track page unreadable (%s); retry in 60 s", exc)
        self.pages[track_id] = (time.monotonic() + 60, found)
        return found

    def sessions(self):
        try:
            if not self.ready:
                if not self.dex.is_file():
                    raise RuntimeError("TV media probe missing (android/classes.dex): run .venv/bin/python android/build.py")
                self.tv.prepare()
                digest = hashlib.sha256(self.dex.read_bytes()).hexdigest()[:16]
                self.remote = f"/data/local/tmp/spotify-smarttube-{digest}.dex"
                self.tv.adb("push", str(self.dex), self.remote)
                self.ready = True
                LOG.info("Spotify TV reader ready over ADB (%s)", self.tv.cfg["tv_target"])
            output = self.tv.adb("shell", f"CLASSPATH={self.remote}",
                                 "app_process", "/system/bin", "MediaProbe")
            sessions = json.loads(output)
            if not isinstance(sessions, list) or not all(isinstance(s, dict) for s in sessions):
                raise ValueError("Invalid TV media session response")
            return sessions
        except (RuntimeError, OSError, ValueError):
            self.ready = False
            raise
