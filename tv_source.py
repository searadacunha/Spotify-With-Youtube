"""Spotify metadata read directly from the TV's Android media session."""
import hashlib
import json
import logging
import re

from common import ROOT, Track
from smarttube_adb import SPOTIFY_TV, SmartTube

LOG = logging.getLogger(__name__)


def from_tv(sessions):
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
    # Spotify TV's native video mode can repeat the title in every text field.
    base_title = re.sub(r"\s*(?:-\s*|\()radio edit\)?\s*$", "", data["title"], flags=re.I).strip()
    if base_title.casefold() == data["artist"].casefold() == (data.get("album") or "").casefold():
        raise ValueError("Spotify TV reports the title as artist and album: switch Spotify TV to audio mode")
    return Track(identifier.split(":")[-1], data["title"], (data["artist"],),
                 duration / 1000, data.get("album") or "")


class TVSource:
    def __init__(self, cfg, tv=None):
        self.tv = tv if tv is not None else SmartTube(cfg)
        self.dex = ROOT / "android/classes.dex"
        self.ready = False

    def current(self):
        return from_tv(self.sessions())

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
