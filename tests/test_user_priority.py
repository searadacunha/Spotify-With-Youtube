"""All ADB calls are mocked; these tests must never contact a TV."""
import io
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

import control
from common import Track, communicate_checked, load_config, operation_guard, run
from main import Bridge
from smarttube_adb import SPOTIFY_TV, SmartTube, ManualOverride
from storage import Store


class UserPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "logs").mkdir()
        (self.root / "cache").mkdir()
        self.path = self.root / "cache/matches.sqlite3"
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.cfg = load_config()
        self.app = self.cfg["smarttube_package"]
        self.video = "A" * 11
        self.state = 3
        self.clip = {}
        self.commands = []
        adb = patch("smarttube_adb.run", side_effect=self.adb)
        adb.start()
        self.addCleanup(adb.stop)
        root = patch("main.ROOT", self.root)
        root.start()
        self.addCleanup(root.stop)
        self.tv = SmartTube(self.cfg, self.store)
        self.source = Mock()
        self.track = Track("0" * 22, "Bye Bye", ("Marshmello, Juice WRLD",), 108.5, "Bye Bye")
        self.source.current.return_value = self.track
        self.candidate = {"id": "B" * 11, "title": "Bye Bye"}
        self.store.remember(self.track, self.cfg, self.candidate)
        score = patch("main.score", return_value=Mock(accepted=True, score=120, tier=1))
        score.start()
        self.addCleanup(score.stop)
        self.bridge = Bridge(self.cfg, self.source, self.tv, self.store)

    def adb(self, args, timeout):
        self.assertEqual(args[:3], [self.cfg["adb_path"], "-s", self.cfg["tv_target"]])
        args = args[3:]
        self.commands.append(args)
        if args == ["shell", "dumpsys", "window"]:
            return f"mCurrentFocus=Window{{a u0 {self.app}/.PlaybackActivity}}" if self.app else "mCurrentFocus=null"
        if args == ["get-state"]:
            return "device"
        if args[:3] == ["shell", "pm", "path"]:
            return "package:/app/base.apk"
        if args[0] == "push":
            return "pushed"
        if args[0] == "shell" and "MediaProbe" in args:
            return json.dumps([{"package": self.cfg["smarttube_package"], "state": self.state, "id": self.video,
                                **self.clip}])
        if args[:4] == ["shell", "monkey", "-p", SPOTIFY_TV]:
            self.app = SPOTIFY_TV
            return "Events injected: 1"
        if args[:3] == ["shell", "am", "start"]:
            self.video = args[args.index("-d") + 1].split("v=")[1]
            return "Status: ok"
        self.fail("Unexpected ADB command: " + repr(args))

    def activate(self):
        self.tv.resume()
        self.commands.clear()

    def sends(self):
        return [c for c in self.commands if c[:3] == ["shell", "am", "start"]]

    def skips(self):
        return [c for c in self.commands if c[-1] == "next"]

    def test_default_requires_explicit_resume(self):
        self.assertEqual(self.bridge.step(), "manual_override")
        self.assertTrue(self.store.get_state("tv_control")["manual_override"])
        self.assertEqual(self.sends(), [])
        self.source.current.assert_not_called()

    def test_spotify_in_front_claims_control_without_resume(self):
        self.app = SPOTIFY_TV
        self.assertEqual(self.bridge.step(), "sent")
        state = self.store.get_state("tv_control")
        self.assertEqual((state["manual_override"], state["video_id"]), (False, self.candidate["id"]))
        self.app = "com.google.android.youtube.tv"
        self.assertEqual(self.bridge.step(), "manual_override")
        self.app = SPOTIFY_TV
        self.assertEqual(self.bridge.step(), "duplicate")
        self.assertEqual(len(self.sends()), 1)

    def test_controlled_smarttube_allows_send_without_wake(self):
        self.activate()
        self.assertEqual(self.bridge.step(), "sent")
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(self.commands[-2], ["shell", "dumpsys", "window"])
        self.assertFalse(any("keyevent" in c for c in self.commands))
        self.assertEqual(self.store.get_state("tv_control")["video_id"], self.candidate["id"])

    def test_other_apps_and_unknown_focus_block_direct_send(self):
        for app in ["com.google.android.youtube.tv", "com.netflix.ninja", "com.android.tv.launcher", None]:
            with self.subTest(app=app):
                self.app = self.cfg["smarttube_package"]
                self.activate()
                generation = self.store.get_state("tv_control")["generation"]
                self.app = app
                with self.assertLogs("smarttube_adb", logging.WARNING) as logs, self.assertRaises(ManualOverride):
                    self.tv.send(self.candidate["id"], track_id=self.track.id, generation=generation)
                self.assertTrue(any("SEND_BLOCKED_FOREGROUND_APP" in line for line in logs.output))
                self.assertTrue(self.store.get_state("tv_control")["manual_override"])
                self.assertEqual(self.sends(), [])

    def test_exit_after_send_and_byebye_billie_byebye_never_replays(self):
        self.activate()
        self.assertEqual(self.bridge.step(), "sent")
        self.app = "com.google.android.youtube.tv"
        self.assertEqual(self.bridge.step(), "manual_override")
        self.source.reset_mock()
        self.commands.clear()
        for track in [self.track, Track("1" * 22, "TV", ("Billie Eilish",), 281), self.track]:
            self.source.current.return_value = track
            self.assertEqual(self.bridge.step(), "manual_override")
        self.source.current.assert_not_called()
        self.assertEqual(self.sends(), [])
        self.app = self.cfg["smarttube_package"]
        self.assertEqual(self.bridge.step(), "manual_override")

    def test_paused_stopped_missing_or_replaced_playback_revokes_control(self):
        for state, video in [(2, "A" * 11), (1, "A" * 11), (3, "C" * 11), (3, None)]:
            with self.subTest(state=state, video=video):
                self.state, self.video = 3, "A" * 11
                self.activate()
                self.state, self.video = state, video
                self.assertEqual(self.bridge.step(), "manual_override")
                self.assertEqual(self.sends(), [])

    def test_clip_end_moves_spotify_to_next_track_and_sends_its_clip(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        following = Track("1" * 22, "Hate Me", ("Ellie Goulding, Juice WRLD",), 205.5, "Hate Me")
        self.store.remember(following, self.cfg, {"id": "C" * 11, "title": "Hate Me"})
        self.source.current.return_value = following
        with patch("smarttube_adb.time.sleep", side_effect=lambda seconds:
                   self.clip.update(position_ms=self.clip["position_ms"] + int(seconds * 1000))) as sleep:
            self.assertEqual(self.bridge.step(), "sent")
        sleep.assert_called_once_with(3)
        self.assertEqual(len(self.skips()), 1)
        self.assertEqual(self.video, "C" * 11)
        self.assertFalse(self.store.get_state("tv_control")["manual_override"])

    def test_app_switch_during_clip_end_wait_cancels_handoff(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        with patch("smarttube_adb.time.sleep", side_effect=lambda _: setattr(self, "app", "com.netflix.ninja")):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.app, "com.netflix.ninja")

    def test_pause_during_clip_end_wait_cancels_handoff(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        with patch("smarttube_adb.time.sleep", side_effect=lambda _: setattr(self, "state", 2)):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])

    def test_seek_back_during_clip_end_wait_keeps_current_video(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        self.source.current.return_value = None
        with patch("smarttube_adb.time.sleep", side_effect=lambda _: self.clip.update(position_ms=10000)):
            self.assertEqual(self.bridge.step(), "idle")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])
        self.assertFalse(self.store.get_state("tv_control")["manual_override"])

    def test_small_seek_back_during_clip_end_wait_does_not_skip_remaining_audio(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        self.source.current.return_value = None
        with patch("smarttube_adb.time.sleep", side_effect=lambda _: self.clip.update(position_ms=193000)):
            self.assertEqual(self.bridge.step(), "idle")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])

    def test_frozen_position_during_clip_end_wait_does_not_advance_spotify(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        self.source.current.return_value = None
        with patch("smarttube_adb.time.sleep"):
            self.assertEqual(self.bridge.step(), "idle")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])

    def test_buffering_during_clip_end_wait_does_not_advance_spotify(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        self.source.current.return_value = None

        def buffering(_):
            self.state = 6
            self.clip["position_ms"] = 199500

        with patch("smarttube_adb.time.sleep", side_effect=buffering):
            self.assertEqual(self.bridge.step(), "idle")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])

    def test_new_control_session_during_clip_end_wait_cancels_old_handoff(self):
        self.activate()
        self.clip = {"duration_ms": 200000, "position_ms": 196000}
        previous = self.store.get_state("tv_control")
        current = {**previous, "generation": "new-explicit-session"}
        with patch("smarttube_adb.time.sleep", side_effect=lambda _: self.store.replace_control(previous, current)):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual(self.skips(), [])
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.store.get_state("tv_control"), current)

    def test_autoplay_replacing_the_clip_hands_back_only_near_its_end(self):
        for remaining, expected in [(20000, "idle"), (120000, "manual_override")]:
            with self.subTest(remaining=remaining):
                self.app, self.video = self.cfg["smarttube_package"], "A" * 11
                self.clip = {"duration_ms": 200000, "position_ms": 200000 - remaining}
                self.tv = SmartTube(self.cfg, self.store)
                self.bridge = Bridge(self.cfg, self.source, self.tv, self.store)
                self.activate()
                self.source.current.return_value = None  # Spotify paused under the clip
                self.assertEqual(self.bridge.step(), "idle")
                self.video = "C" * 11  # SmartTube's autoplay
                self.assertEqual(self.bridge.step(), expected)
                self.assertEqual(len(self.skips()), int(expected == "idle"))

    def test_clip_still_loading_after_send_keeps_control(self):
        self.app = SPOTIFY_TV
        self.assertEqual(self.bridge.step(), "sent")
        self.app, self.state, self.video = self.cfg["smarttube_package"], 2, "A" * 11  # previous clip's session
        self.assertEqual(self.bridge.step(), "duplicate")
        self.app = None
        self.assertEqual(self.bridge.step(), "duplicate")
        self.tv.sent_at -= 60
        self.app, self.state, self.video = self.cfg["smarttube_package"], 6, self.candidate["id"]
        self.assertEqual(self.bridge.step(), "duplicate")
        self.state = 2
        self.assertEqual(self.bridge.step(), "manual_override")

    def test_retry_cancelled_and_never_runs_during_override(self):
        self.activate()
        self.bridge.retry_at = 10**20
        self.bridge.failures = 4
        self.app = "com.google.android.youtube.tv"
        self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual((self.bridge.retry_at, self.bridge.failures), (0, 0))
        self.assertEqual(self.sends(), [])

    def test_source_backoff_is_interrupted_by_manual_override(self):
        self.activate()
        self.bridge.retry_at, self.bridge.failures = 10**20, 4
        def leave(_):
            self.app = "com.google.android.youtube.tv"
        with patch("main.time.sleep", side_effect=leave):
            self.bridge.wait(300)
        self.assertEqual((self.bridge.retry_at, self.bridge.failures), (0, 0))
        self.assertTrue(self.store.get_state("tv_control")["manual_override"])
        self.assertEqual(self.sends(), [])

    def test_policy_and_metadata_changes_do_not_replay_sent_track(self):
        self.activate()
        self.assertEqual(self.bridge.step(), "sent")
        self.cfg["min_score"] += 1
        self.source.current.return_value = Track(self.track.id, "Other metadata", self.track.artists, 120)
        with patch("storage.SCORING_VERSION", 999), patch("main.resolve") as resolve:
            self.assertEqual(Bridge(self.cfg, self.source, self.tv, self.store).step(), "duplicate")
            resolve.assert_not_called()
        self.assertEqual(len(self.sends()), 1)

    def test_focus_change_during_resolution_discards_result(self):
        self.activate()
        self.store.db.execute("DELETE FROM matches")
        def resolve(*args):
            self.app = "com.google.android.youtube.tv"
            return Mock(candidate=self.candidate, score=120), []
        with patch("main.resolve", side_effect=resolve):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.bridge.retry_at, 0)

    def test_guard_rechecks_foreground_after_media_probe(self):
        self.activate()
        with patch.object(self.tv, "foreground_app", side_effect=[self.app, "com.google.android.youtube.tv"]):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual(self.sends(), [])

    def test_unknown_adb_state_fails_closed(self):
        self.activate()
        with patch.object(self.tv, "foreground_app", side_effect=RuntimeError("offline")):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertEqual(self.sends(), [])

    def test_uncertain_send_is_persisted_and_never_retried(self):
        self.activate()
        normal = self.adb
        def uncertain(args, timeout):
            if args[3:6] == ["shell", "am", "start"]:
                raise RuntimeError("timeout after possible delivery")
            return normal(args, timeout)
        with patch("smarttube_adb.run", side_effect=uncertain):
            self.assertEqual(self.bridge.step(), "manual_override")
        self.assertTrue(self.store.was_handled(self.track.id, self.tv.destination))
        self.assertEqual(self.bridge.retry_at, 0)
        self.activate()
        self.assertEqual(self.bridge.step(), "duplicate")
        self.assertEqual(self.sends(), [])

    def test_override_survives_new_store_and_service_instance(self):
        self.activate()
        self.app = "com.netflix.ninja"
        self.assertEqual(self.bridge.step(), "manual_override")
        other = Store(self.path)
        try:
            self.app = self.cfg["smarttube_package"]
            self.commands.clear()
            self.assertEqual(Bridge(self.cfg, self.source, SmartTube(self.cfg, other), other).step(), "manual_override")
            self.assertEqual(self.sends(), [])
        finally:
            other.close()

    def test_resume_is_explicit_changes_generation_and_keeps_history(self):
        self.activate()
        old = self.store.get_state("tv_control")["generation"]
        self.assertEqual(self.bridge.step(), "sent")
        self.tv.resume()
        self.assertNotEqual(self.store.get_state("tv_control")["generation"], old)
        with self.assertRaises(ManualOverride):
            self.tv.send("C" * 11, track_id="1" * 22, generation=old)
        self.assertEqual(self.bridge.step(), "duplicate")

    def test_cli_resume_does_not_start_service_or_video(self):
        with patch("control.ROOT", self.root), patch("control.load_config", return_value=self.cfg), \
             patch("sys.argv", ["smarttube", "resume"]), patch("control.launchctl") as launchctl, \
             patch("main.setup_logging"), redirect_stdout(io.StringIO()):
            self.assertEqual(control.main(), 0)
        launchctl.assert_not_called()
        self.assertEqual(self.sends(), [])
        self.assertFalse(self.store.get_state("tv_control")["manual_override"])

    def test_cli_resume_refused_when_youtube_is_foreground(self):
        self.app = "com.google.android.youtube.tv"
        with patch("control.ROOT", self.root), patch("control.load_config", return_value=self.cfg), \
             patch("sys.argv", ["smarttube", "resume"]), patch("control.launchctl") as launchctl, patch("main.setup_logging"):
            self.assertEqual(control.main(), 1)
        launchctl.assert_not_called()
        self.assertEqual(self.sends(), [])

    def test_pending_download_is_killed_when_override_is_detected(self):
        self.activate()
        processes = []
        popen = subprocess.Popen
        def capture(*args, **kwargs):
            process = popen(*args, **kwargs)
            processes.append(process)
            return process
        def check():
            if processes:
                self.app = "com.google.android.youtube.tv"
                self.bridge.next_control_check = 0
            self.bridge.check_pending()
        with patch("common.subprocess.Popen", side_effect=capture), operation_guard(check), self.assertRaises(ManualOverride):
            run([sys.executable, "-c", "import time; time.sleep(30)"], 5)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(self.store.get_state("tv_control")["manual_override"])
        self.assertEqual(self.sends(), [])

    def test_override_blocks_subprocess_before_start(self):
        self.activate()
        self.app = "com.google.android.youtube.tv"
        with patch("common.subprocess.Popen") as popen, operation_guard(self.bridge.check_pending), self.assertRaises(ManualOverride):
            run([sys.executable, "-c", "pass"], 5)
        popen.assert_not_called()

    def test_cancellation_kills_descendants_even_if_parent_already_exited(self):
        process = Mock(pid=12345, args=["mock"], returncode=0)
        process.poll.return_value = 0
        check = Mock(side_effect=ManualOverride("cancelled"))
        with patch("common.os.killpg") as killpg, operation_guard(check), self.assertRaises(ManualOverride):
            communicate_checked(process, 1)
        killpg.assert_called_once()
        self.assertEqual(killpg.call_args.args[0], process.pid)
        process.communicate.assert_called_once_with()

    def test_service_start_disarms_previous_control_without_sending(self):
        import main
        self.activate()
        self.source.name = "tv"
        with patch("main.ROOT", self.root), patch("main.load_config", return_value=self.cfg), \
             patch("main.SpotifySource", return_value=self.source), patch("main.setup_logging"), \
             patch("main.signal.signal"), patch("sys.argv", ["main.py", "--once"]):
            self.assertEqual(main.main(), 1)
        self.assertTrue(self.store.get_state("tv_control")["manual_override"])
        self.assertEqual(self.sends(), [])

    def test_direct_send_without_persistent_ownership_is_blocked(self):
        with self.assertRaises(ManualOverride):
            SmartTube(self.cfg).send("B" * 11, track_id=self.track.id, generation="missing")
        self.assertEqual(self.commands, [])

    def test_legacy_last_sent_migrates_independently_of_matching_policy(self):
        legacy = self.root / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.execute("CREATE TABLE state (name TEXT PRIMARY KEY, payload TEXT)")
        db.execute("INSERT INTO state VALUES ('last_done', ?)", (json.dumps({
            "track_id": self.track.id, "destination": self.tv.destination, "outcome": "sent",
            "video_id": self.video, "time": 1, "policy": "obsolete"}),))
        db.commit()
        db.close()
        migrated = Store(legacy)
        try:
            self.assertTrue(migrated.was_handled(self.track.id, self.tv.destination))
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
