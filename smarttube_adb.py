import logging
import re
import uuid

from common import run
from youtube_match import VIDEO_ID

LOG = logging.getLogger(__name__)
SPOTIFY_TV = "com.spotify.tv.android"


class ManualOverride(Exception):
    """Cancellation, not a transient failure eligible for retry."""


class SmartTube:
    def __init__(self, cfg, store=None):
        self.cfg = cfg
        self.store = store
        self.destination = [cfg["tv_target"], cfg["smarttube_package"]]
        self.probe = None
        self.override_logged = False

    def adb(self, *args):
        return run([self.cfg["adb_path"], "-s", self.cfg["tv_target"], *args],
                   self.cfg["adb_timeout_seconds"])

    def prepare(self):
        try:
            connected = self.adb("get-state").strip() == "device"
        except RuntimeError:
            connected = False
        if not connected:
            LOG.info("Reconnecting ADB to %s", self.cfg["tv_target"])
            self.adb("connect", self.cfg["tv_target"])
            if self.adb("get-state").strip() != "device":
                raise RuntimeError("TV unreachable over ADB or not authorized")
        package = self.adb("shell", "pm", "path", self.cfg["smarttube_package"])
        if not any(line.startswith("package:") for line in package.splitlines()):
            raise RuntimeError("Target package not installed: " + self.cfg["smarttube_package"])

    def foreground_app(self):
        # On the Android 12 TV, "dumpsys window windows" has no mCurrentFocus line; the full dump does.
        windows = self.adb("shell", "dumpsys", "window")
        packages = set(re.findall(r"mCurrentFocus=Window\{[^\n]*?\bu\d+\s+([\w.]+)/", windows))
        return next(iter(packages)) if len(packages) == 1 else None

    def playback(self):
        if self.probe is None:
            from tv_source import TVSource
            self.probe = TVSource(self.cfg, tv=self)
        sessions = [s for s in self.probe.sessions() if s.get("package") == self.cfg["smarttube_package"]]
        if len(sessions) != 1 or sessions[0].get("state") != 3:
            raise ValueError("SmartTube playback interrupted or ambiguous")
        identifier = sessions[0].get("id")
        if not isinstance(identifier, str) or not VIDEO_ID.fullmatch(identifier):
            raise ValueError("SmartTube video identity unavailable")
        return identifier

    def enter_override(self, reason):
        if self.store is not None:
            previous = self.store.get_state("tv_control")
            current = {"manual_override": True, "destination": self.destination,
                       "generation": uuid.uuid4().hex, "video_id": None, "reason": reason}
            if previous is None or previous.get("manual_override") is not True:
                if self.store.replace_control(previous, current):
                    LOG.warning("MANUAL_OVERRIDE_ENTER %s", reason)
        raise ManualOverride(reason)

    def claim_for_spotify(self, state, generation):
        """The user is choosing music in Spotify on the TV: that is the request to show its clip."""
        if not state or state.get("manual_override") is not False or state.get("destination") != self.destination:
            current = {"manual_override": False, "destination": self.destination,
                       "generation": uuid.uuid4().hex, "video_id": None, "reason": "spotify_foreground"}
            if not self.store.replace_control(state, current):
                raise ManualOverride("Control state changed; Spotify claim cancelled")
            LOG.info("MANUAL_OVERRIDE_EXIT Spotify in front")
            state = current
        self.override_logged = False
        if generation is not None and generation != state.get("generation"):
            raise ManualOverride("Control session changed; pending work cancelled")
        return state

    def check_control(self, generation=None):
        state = self.store.get_state("tv_control") if self.store is not None else None
        try:
            app = self.foreground_app() if self.store is not None else None
        except (RuntimeError, OSError, ValueError) as exc:
            app = exc
        if app == SPOTIFY_TV:
            return self.claim_for_spotify(state, generation)
        if not state or state.get("manual_override") is not False:
            if not self.override_logged:
                LOG.info("MANUAL_OVERRIDE_ACTIVE open Spotify on the TV or resume")
                self.override_logged = True
            if state is None:
                self.enter_override("No explicit TV control session")
            raise ManualOverride("Manual override active")
        self.override_logged = False
        if generation is not None and generation != state.get("generation"):
            raise ManualOverride("Control session changed; pending work cancelled")
        if state.get("destination") != self.destination or not state.get("video_id") or not state.get("generation"):
            self.enter_override("TV ownership missing or destination changed")
        try:
            if isinstance(app, Exception):
                raise app
            if app != self.cfg["smarttube_package"]:
                LOG.warning("SEND_BLOCKED_FOREGROUND_APP app=%s", app or "unknown")
                self.enter_override("Foreground app is " + str(app))
            if self.playback() != state["video_id"]:
                self.enter_override("SmartTube video manually replaced")
            # Re-read focus after the media probe, immediately before authorizing a send.
            if self.foreground_app() != self.cfg["smarttube_package"]:
                LOG.warning("SEND_BLOCKED_FOREGROUND_APP changed during inspection")
                self.enter_override("Foreground changed during inspection")
        except (RuntimeError, OSError, ValueError) as exc:
            self.enter_override("TV state unavailable: " + str(exc))
        if self.store.get_state("tv_control") != state:
            raise ManualOverride("Control session changed during inspection")
        return state

    def resume(self):
        if self.store is None:
            raise RuntimeError("Persistent TV control store required")
        try:
            if self.foreground_app() != self.cfg["smarttube_package"]:
                LOG.warning("SEND_BLOCKED_FOREGROUND_APP resume requires SmartTube")
                self.enter_override("Open SmartTube yourself before resuming")
            video_id = self.playback()
            if self.foreground_app() != self.cfg["smarttube_package"]:
                self.enter_override("Foreground changed during resume")
        except (RuntimeError, OSError, ValueError) as exc:
            self.enter_override("Cannot resume: " + str(exc))
        previous = self.store.get_state("tv_control")
        state = {"manual_override": False, "destination": self.destination,
                 "generation": uuid.uuid4().hex, "video_id": video_id, "reason": "explicit_resume"}
        if not self.store.replace_control(previous, state):
            raise ManualOverride("Control state changed; resume cancelled")
        self.override_logged = False
        LOG.info("MANUAL_OVERRIDE_EXIT explicit resume video=%s", video_id)

    def send(self, video_id, *, track_id, generation):
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError("invalid videoId")
        if not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
            raise ValueError("invalid Spotify track id")
        state = self.check_control(generation)
        if not self.store.record_handled(track_id, self.destination, video_id):
            LOG.info("SEND_BLOCKED_ALREADY_HANDLED track=%s", track_id)
            return False
        url = "https://www.youtube.com/watch?v=" + video_id
        self.check_control(generation)
        try:
            out = self.adb("shell", "am", "start", "-W", "-a", "android.intent.action.VIEW",
                           "-d", url, "-p", self.cfg["smarttube_package"])
            if re.search(r"error|exception|unable to resolve|does not exist|timeout", out, re.I):
                raise RuntimeError("Intent rejected by Android: " + out.strip())
            if "Status: ok" not in out and "intent has been delivered" not in out:
                raise RuntimeError("Intent delivery not confirmed: " + out.strip())
        except (RuntimeError, OSError, ValueError) as exc:
            self.enter_override("Uncertain send; no automatic retry: " + str(exc))
        self.store.confirm_handled(track_id, self.destination)
        self.store.replace_control(state, {**state, "video_id": video_id})
        LOG.info("SENT video=%s target=%s | %s", video_id,
                 self.cfg["tv_target"], " ".join(out.split()))
        return True
